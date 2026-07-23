"""STB 기본 동작: 연결 → 재부팅 → 로그 저장 → 편성표 채널 전환 → 광고 재생 확인.

실행 환경:
  체크리스트 1~6은 동일 테스트 STB에서 한 번에 수행한다.
  구글(3) 테스트 때 쓰는 mock/QA 엔드포인트를 2·4·5·6에도 그대로 써도 된다.
  상용 STB·상용 엔드포인트가 필수는 아니다.
  SKIP_GOOGLE_CHECK=1 — 구글 편성이 없을 때만 체크 3 생략 (환경 분리용이 아님).
  Google Chat — 기본 spaces/AAQA_7E-M1k (.py 단독 OK). 끄기: GOOGLE_CHAT_SPACE=0
  (run_default_behavior.ps1 은 UTF-8/중복종료 편의용, 필수는 아님)

최종 확인 목표 (체크리스트):
  1. S/W ver. 확인 — 재부팅 후 Firmware → SDK → Agent (펌웨어는 V.xx.xx.xxxx 만 표시)
  2. 광고 재생(내부 소재) — cue−2초≤ImpressionLog playTime합≤cue + API 200 (일반 채널만)
     · 체크리스트 완료 후 편성 슬롯마다 [모니터링] — 내부 playTime/impression, 구글 quartile·tracking·skip, 키즈 워터마크·OCR
     · 채널 진입 후 SLOT_AD_START_TIMEOUT_SEC(기본 90초) 내 cue/play 없으면 [편성 스킵] → 다음 편성
  3. 광고 재생(구글) — 3-A Quartile+COMPLETE / 3-B 이탈→tracking중단 / 3-C SKIPPABLE→SKIPPED
  4. 광고 재생 중 채널 이탈 — 이탈 시점: play ==== +30초(벽시계)
     · 사후 검증: logcat player play→stop 시간차 ≈ ImpressionLog playTime합
  5. 광고 채널 선전환 → register cue 후 이탈(play 전) → play/impression 없음 (미재생)
  6. 키즈 채널 워터마크 — 채널 311,320-324,328
     · receiveCue / ProgramProviderChannel … kid=true
     · KidWatermarkManager.buildWaterMark — isKid: true
     · changed content uri: …/kid_watermark.png
     · PASS: 위 logcat + 광고 재생 중 ADB "광고 방송" OCR (5초 간격 최대 24회≈2분, 1회 검출 시 OK)

광고 재생 logcat 흐름 (cue duration 기준, 다광고 시 단계·impression 횟수 반복):
  1. CueManager.register cue (AddrAD, duration=…)
  2. ads will play in … ms (SDK)
  3. load / onPrepare (SDK)
  4. play start / AnypointAdPlayerImpl.play (SDK)
  5. callOnPlay (SDK)
  6. callListenerPrepareStop → doStop → player stop → onStopped (SDK)
  7. impression log size=N → 이후 ImpressionLogManager.send ImpressionLog N건만 playTime 집계
     (AdEventManager.sendImpressionLogs --> ImpressionLog 미리보기는 제외)
     (`--> ImpressionLog` 전송 직전 로그는 제외)
  8. POST …/impression-logs → 200 (AddrAD)
"""

import atexit
import os
import queue
import re
import signal
import subprocess
import sys
import time
import threading
import zipfile
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
if stbrpa_dir not in sys.path:
    sys.path.append(stbrpa_dir)

from component.channel_catalog import (
    cue_id_matches_slot,
    format_channel_ref,
    get_catalog_path,
    load_channel_catalog,
    lookup_channel,
    parse_program_provider_channel_id,
    parse_register_cue_pp_id,
    resolve_expected_catalog_ids,
)
from component.device_connect_multiple import connect_devices, get_device_ips
from component.schedule_loader import load_schedule_data
from component.google_ad_tracker import (
    GoogleAdEventTracker,
    is_google_ad_term_log_line,
)
from component.adb_capture import (
    adb_capture_path,
    capture_png_via_adb,
    check_phrase_on_device,
)
from component.save_logs import (
    save_multiple_devices_logs,
    print_impression_log_counts,
    stop_all_device_logs,
)

# 편성표: TV 타겟 지상 채널 큐톤 시간표 (Drive Excel)
# https://docs.google.com/spreadsheets/d/1fGc1yW9gBoHhJSo57E81FIAeAhNQz2ol/
SCHEDULE_SPREADSHEET_KEY = "1fGc1yW9gBoHhJSo57E81FIAeAhNQz2ol"
# .py 단독 실행 시에도 Chat 전송 (GOOGLE_CHAT_SPACE=0 이면 끔)
DEFAULT_GOOGLE_CHAT_SPACE = "spaces/AAQA_7E-M1k"
DRIVE_SCHEDULE_FILE_ID = os.environ.get(
    "DRIVE_SCHEDULE_FILE_ID", SCHEDULE_SPREADSHEET_KEY
)
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"
LOG_DIR = r"D:\python_test\anypointmedia-QA\test_log"
SINGLE_INSTANCE_LOCK_FILE = os.path.join(LOG_DIR, "default_behavior.lock")
SCHEDULE_CACHE_DIR = os.path.join(LOG_DIR, "_schedule_cache")
_single_instance_lock_fp = None
LOG_FILTERS = [
    "AnypointAD",
    "ANYPOINT_SDK",
    "not yet ready",
    "FATAL EXCEPTION",
    "beginning of crash",
    "ANR in ",
]
REBOOT_MIN_WAIT_SEC = 60
REBOOT_READY_TIMEOUT_SEC = 300
REBOOT_POLL_INTERVAL_SEC = 5
# 이번 프로세스에서 adb reboot 를 이미 보냈는지 (중복 재부팅 방지)
_stb_reboot_sent = False
VERSION_SCAN_TIMEOUT_SEC = int(os.environ.get("VERSION_SCAN_TIMEOUT_SEC", "60"))
VERSION_STATUS_INTERVAL_SEC = 15
# Agent 만 잡힌 뒤 SDK 미확인이면 이 시간 후 버전 대기 중단(체크1 FAIL 후 모니터링 진행)
VERSION_SDK_WAIT_AFTER_AGENT_SEC = int(
    os.environ.get("VERSION_SDK_WAIT_AFTER_AGENT_SEC", "30")
)
# not linear 복구(라이브) 후 S/W ver. 재확인 — 비선형 중엔 sdkVersion.name 미출력
VERSION_RETRY_AFTER_LIVE_TIMEOUT_SEC = int(
    os.environ.get("VERSION_RETRY_AFTER_LIVE_TIMEOUT_SEC", "45")
)
VERSION_RETRY_AFTER_LIVE_COOLDOWN_SEC = int(
    os.environ.get("VERSION_RETRY_AFTER_LIVE_COOLDOWN_SEC", "90")
)
_version_retry_after_live_at = 0.0
_version_retry_after_live_lock = threading.Lock()
_version_retry_after_live_thread = None
# 버전 확인: logcat 은 최근 N초 이내 줄만 (구버퍼·10분+ 전 로그 제외)
VERSION_SCAN_LOG_LOOKBACK_SEC = int(
    os.environ.get("VERSION_SCAN_LOG_LOOKBACK_SEC", "60")
)
POST_REBOOT_VERSION_LOOKBACK_SEC = int(
    os.environ.get("POST_REBOOT_VERSION_LOOKBACK_SEC", "300")
)
VERSION_LOGCAT_DUMP_MAX_LINES = int(
    os.environ.get("VERSION_LOGCAT_DUMP_MAX_LINES", "8000")
)
FIRMWARE_GETPROP_KEYS = (
    "ro.bootimage.build.version.incremental",
    "ro.build.version.incremental",
    "ro.build.display.id",
    "ro.build.fingerprint",
)
# 단일 광고 ~120초 + impression/API 여유 (예시: play 09:42:00 → impression 09:44:09)
AD_PLAYBACK_WAIT_TIMEOUT_SEC = 200
AD_PLAYBACK_STATUS_INTERVAL_SEC = 30
DEFAULT_EXPECTED_IMPRESSIONS = 1
AD_BROADCAST_UI_TEXT = "광고 방송"
# 키즈 '광고 방송' OCR: play 후 5초 간격 캡처 — 1회 검출 시 PASS (기본 약 2분간)
AD_BROADCAST_UI_CAPTURE_INTERVAL_SEC = float(
    os.environ.get("AD_BROADCAST_UI_CAPTURE_INTERVAL_SEC", "5")
)
AD_BROADCAST_UI_CAPTURE_COUNT = int(
    os.environ.get("AD_BROADCAST_UI_CAPTURE_COUNT", "24")
)
AD_BROADCAST_UI_PLAY_WAIT_SEC = int(
    os.environ.get("AD_BROADCAST_UI_PLAY_WAIT_SEC", "90")
)
# 체크리스트 4·5 (체크 4 이탈 시점은 play ==== 이후 고정 초만 사용 — playTime/impression 으로 sleep 하지 않음)
CHANNEL_LEAVE_DURING_AD_WAIT_SEC = 30
# 체크 4: 편성 시각까지 play ==== 대기 (이탈 전)
CHECK4_PLAY_WAIT_TIMEOUT_SEC = int(
    os.environ.get("CHECK4_PLAY_WAIT_TIMEOUT_SEC", "120")
)
# 체크 4: 이탈(채널 변경) 후 ImpressionLog — 보통 10초 이내
CHECK4_IMPRESSION_WAIT_SEC = int(
    os.environ.get("CHECK4_IMPRESSION_WAIT_SEC", "30")
)
CHANNEL_LEAVE_OBSERVE_SEC = 90
CHECK5_POST_LEAVE_OBSERVE_SEC = int(
    os.environ.get("CHECK5_POST_LEAVE_OBSERVE_SEC", "15")
)
# 체크 5: register cue 후 N초 이탈, ads will play in 예약 시 play N초 전 상한
CHECK5_LEAVE_AFTER_REGISTER_SEC = int(
    os.environ.get(
        "CHECK5_LEAVE_AFTER_REGISTER_SEC",
        os.environ.get("CHECK5_LEAVE_AFTER_AD_SEC", "3"),
    )
)
CHECK5_LEAVE_BEFORE_PLAY_SEC = float(
    os.environ.get("CHECK5_LEAVE_BEFORE_PLAY_SEC", "2")
)
CHECK5_REGISTER_TIMEOUT_SEC = int(os.environ.get("CHECK5_REGISTER_TIMEOUT_SEC", "20"))
# 체크 3 (구글 IMA) — 3-A / 3-B / 3-C 개별 진행
GOOGLE_CHECK3_SUBTESTS = ("full_play", "leave_during", "skip_ok")
GOOGLE_CHECK3_TAGS = {
    "full_play": "3-A",
    "leave_during": "3-B",
    "skip_ok": "3-C",
}
GOOGLE_CHECK3_LABELS = {
    "full_play": "3-A Quartile 정보 + 마지막 광고의 COMPLETE까지 찍히는지 확인",
    "leave_during": "3-B 재생 중 채널 이탈 시 구글 tracking 중단",
    "skip_ok": "3-C SKIPPED — 스킵 가능 광고에서 SKIPPED 이벤트",
}
GOOGLE_AD_START_TIMEOUT_SEC = int(os.environ.get("GOOGLE_AD_START_TIMEOUT_SEC", "180"))
GOOGLE_AD_PLAY_TIMEOUT_SEC = int(os.environ.get("GOOGLE_AD_PLAY_TIMEOUT_SEC", "120"))
GOOGLE_LEAVE_MIN_AFTER_START_SEC = float(
    os.environ.get("GOOGLE_LEAVE_MIN_AFTER_START_SEC", "8")
)
GOOGLE_SKIP_OK_KEYEVENT = int(os.environ.get("GOOGLE_SKIP_OK_KEYEVENT", "23"))
GOOGLE_POST_LEAVE_OBSERVE_SEC = int(os.environ.get("GOOGLE_POST_LEAVE_OBSERVE_SEC", "30"))
# 편성 모니터링: SKIPPABLE 구글 광고 시 자동 skip 입력
MONITOR_GOOGLE_AUTO_SKIP = os.environ.get("MONITOR_GOOGLE_AUTO_SKIP", "1").strip().lower() not in (
    "0",
    "false",
    "n",
    "no",
)
GOOGLE_MONITOR_SUB = "monitor"
ADS_WILL_PLAY_IN_MS_RE = re.compile(r"ads will play in (\d+)\s*ms", re.IGNORECASE)
# 채널 전환 직후 logcat 에 남은 play 로그 무시 (초)
AD_PLAYBACK_LOG_GRACE_SEC = 8
# tracker 시작 시각 이전 logcat 줄 무시 (버퍼에 남은 옛 로그 방지)
AD_LOG_TRUST_LOOKBACK_SEC = 30
# 체크 5: 채널 전환·튜닝 중 register/load 로그 놓치지 않도록 버퍼 조회 범위 확대
CHECK5_LOG_LOOKBACK_SEC = 90
LOGCAT_LINE_TIME_RE = re.compile(
    r"^(?:(\d{4})-)?(\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}\.\d{3})"
)
# 체크 2: cue duration 미확인 시 기본 기대 재생시간 (ms)
EXPECTED_AD_PLAYTIME_MS = 120_000
CUE_DURATION_MS_RE = re.compile(r"duration=(\d+)", re.I)
# 체크 2: playTime 합은 cue duration 이하, 실제 재생은 최대 2초 짧을 수 있음 (예: 118~120초)
CHECK2_PLAYTIME_UNDER_CUE_MS = int(
    os.environ.get("CHECK2_PLAYTIME_UNDER_CUE_MS", "2000")
)
# 체크 4: logcat player play→stop vs ImpressionLog playTime 합 허용 오차
PLAYTIME_MATCH_TOLERANCE_MS = 5_000
# 체크 4: 이탈 후 logcat 덤프·playTime 회수
CHECK4_POST_LEAVE_LOG_LOOKBACK_SEC = int(
    os.environ.get("CHECK4_POST_LEAVE_LOG_LOOKBACK_SEC", "90")
)
CHECK4_POST_LEAVE_LOG_MAX_LINES = int(
    os.environ.get("CHECK4_POST_LEAVE_LOG_MAX_LINES", "3000")
)

# 키즈 채널 (워터마크 확인 대상) — 매시 50분~정각(59분) 구간 우선 편성
KIDS_CHANNEL_NUMBERS = frozenset(
    {"311", "320", "321", "322", "323", "324", "328"}
)
KIDS_PRIME_TIME_START_MINUTE = 50  # XX:50 ~ (요약 안내용)
# 채널 진입 후 cue/play 없으면 편성 오류로 보고 다음 슬롯 (기본 90초)
SLOT_AD_START_TIMEOUT_SEC = int(os.environ.get("SLOT_AD_START_TIMEOUT_SEC", "90"))
SLOT_AD_START_STATUS_INTERVAL_SEC = 20
# (레거시) 예전 3분 리드 우선 — 현재는 :50~:59 시계 기준 무조건 우선으로 대체
KIDS_PRIORITY_MAX_LEAD_SEC = int(os.environ.get("KIDS_PRIORITY_MAX_LEAD_SEC", "180"))
KIDS_WATERMARK_WAIT_SEC = 90
# 키즈 워터마크 단계 라벨 (터미널·요약용)
KIDS_WATERMARK_PHASE_LABELS = {
    "kid_cue": "키즈 Cue (kid=true)",
    "is_kid": "isKid: true",
    "watermark_build": "KidWatermarkManager.buildWaterMark",
    "watermark_uri": "kid_watermark.png URI",
    "legacy": "kid watermark (legacy)",
}

# (phase_key, line contains, 터미널 라벨) — 순서는 전형적 타임라인
AD_PLAYBACK_PHASES = [
    ("cue_register", "register cue:", "Cue 등록"),
    ("ads_scheduled", "ads will play in", "재생 예약"),
    ("load", "AnypointAdsManagerImpl.load", "load/onPrepare"),
    ("play_start", "play start", "play start"),
    ("player_play", "AnypointAdPlayerImpl.play", "player play"),
    ("on_play", "callOnPlay", "onPlay"),
    ("prepare_stop", "callListenerPrepareStop", "prepareStop"),
    ("player_stop", "AnypointAdPlayerImpl.stop", "player stop"),
    ("on_stopped", "onStopped", "onStopped"),
    ("impression_log", "impression log size", "impression log"),
    ("impression_detail", "ImpressionLog(", "ImpressionLog"),
    ("impression_post", "impression-logs", "impression API"),
]

_active_ad_trackers = {}
_ad_tracker_lock = threading.Lock()
_active_kids_watermark_trackers = {}
_kids_watermark_lock = threading.Lock()
_active_google_trackers = {}
_active_google_tune_targets = {}
_google_tracker_lock = threading.Lock()
_active_google_subtest = None


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "y", "yes")


# SKIP_REBOOT=0 일 때만 시작 재부팅 + 버전 확인 (SKIP_REBOOT=1 은 버전도 스킵)
# VERSION_REBOOT_ON_MISS=1: SKIP_REBOOT=0 경로에서 SDK/Agent 미확인 시 재부팅 1회
VERSION_REBOOT_ON_MISS = _env_truthy("VERSION_REBOOT_ON_MISS")


_run_checklist = {
    "versions": {},
    "google_skipped": _env_truthy("SKIP_GOOGLE_CHECK"),
    "google_ad": {},
    "kids_watermark": {},
    "kids_watermark_ui": {},
    "kids_check6": None,
    "ad_broadcast_ui": [],
    "ad_playback": None,
    "leave_during_ad": None,
    "leave_before_play": None,
}
# 체크 2·4·5 슬롯별 재시도 상한
CHECKLIST_CHECK_MAX_ATTEMPTS = int(
    os.environ.get("CHECKLIST_CHECK_MAX_ATTEMPTS", "3")
)
# 체크 2만 미통과·3·4·5·6 완료 후 추가 재시도
CHECK2_BONUS_MAX_ATTEMPTS = int(os.environ.get("CHECK2_BONUS_MAX_ATTEMPTS", "3"))
_check_attempt_counts = {
    "ad_playback": 0,
    "leave_during_ad": 0,
    "leave_before_play": 0,
}
_check_exhausted_announced = set()
_check2_bonus_used = 0
_check2_bonus_announced = False
# 1차 리포트 후 실패 항목 추가 재시도 라운드 (상한 = 기본 + 추가)
CHECKLIST_EXTRA_ROUND_ATTEMPTS = int(
    os.environ.get("CHECKLIST_EXTRA_ROUND_ATTEMPTS", "3")
)
_checklist_extra_phase = False
_report_interim_sent = False
_last_progress_chat_fingerprint = None
# 모니터링 중 크래시/치명 이슈 감지 (logcat 원문 라인 기준)
# Anypoint 관련 패키지/태그만 리포트 — 홈쇼핑·TTS 등 외부 앱은 무시
CRASH_LOG_NEEDLES = (
    "FATAL EXCEPTION",
    "beginning of crash",
    "ANR in ",
    "signal 11 (SIGSEGV",
    "signal 6 (SIGABRT",
)
ANYPOINT_CRITICAL_MARKERS = (
    "tv.anypoint",
    "anypointad",
    "anypoint_sdk",
    "anypoint sdk",
    "anypointads",
    "anypointad_",
)
# FATAL 등은 Process: 줄이 뒤따라오므로 짧게 버퍼링 후 Anypoint 여부 판정
_CRITICAL_PENDING_TTL_SEC = 3.0
_CRITICAL_PENDING_MAX_LINES = 20
_critical_issues = []
_critical_issue_last = {}
_pending_critical = {}
_last_ad_busy_defer_log_at = 0.0
AD_BUSY_DEFER_LOG_INTERVAL_SEC = int(
    os.environ.get("AD_BUSY_DEFER_LOG_INTERVAL_SEC", "15")
)
_last_kids_priority_log_key = None
_last_wait_status_log_at = 0.0
_last_wait_status_slot_key = None
WAIT_STATUS_LOG_INTERVAL_SEC = int(os.environ.get("WAIT_STATUS_LOG_INTERVAL_SEC", "60"))
KIDS_SLOT_LOG_LOOKBACK_SEC = int(os.environ.get("KIDS_SLOT_LOG_LOOKBACK_SEC", "180"))
CHECK2_LOG_LOOKBACK_SEC = int(os.environ.get("CHECK2_LOG_LOOKBACK_SEC", "180"))

# CueManager: linear TV 가 아님(홈/VOD·홈쇼핑 등) — 광고 cue 무시
NOT_LINEAR_TV_STATE_NEEDLE = "not linear tv state"
# SDK: 광고 소재 미준비 — AD_SYNC 브로드캐스트로 동기화
NOT_READY_TO_PLAY_AD_NEEDLE = "not yet ready to play target ad"
AD_SYNC_BROADCAST_ACTION = "tv.anypoint.sdk.AD_SYNC"
AD_SYNC_LGU_PACKAGE = os.environ.get(
    "AD_SYNC_PACKAGE", "tv.anypoint.uplus.tvg.app"
).strip()
AD_SYNC_RECOVERY_COOLDOWN_SEC = int(os.environ.get("AD_SYNC_RECOVERY_COOLDOWN_SEC", "90"))
# local(PC) API: http://{host}/{model}  — 모델명은 셋탑 종류별 (UHD3, UHD4K 등)
CHANGE_TEST_PROPERTY_ACTION = "tv.anypoint.agent.app.CHANGE_TEST_PROPERTY"
DEFAULT_LOCAL_API_HOST = os.environ.get("STB_LOCAL_API_HOST", "192.168.10.150").strip()
_pending_api_endpoints = {}  # device_ip -> endpoint URL
API_ENDPOINT_APPLY_SETTLE_SEC = float(
    os.environ.get("API_ENDPOINT_APPLY_SETTLE_SEC", "3")
)
# 홈/VOD 런처 — 종료·exit 우선 (BACK 은 라이브→홈 유발)
HOME_EXIT_BUTTON_LABELS = ("종료", "exit", "Exit", "EXIT")
HOME_EXIT_RID_MARKERS = ("exit", "btn_exit", "close", "quit", "finish", "end")
HOME_SCREEN_ACTIVITY_MARKERS = (
    "HomeActivity",
    "homeactivity",
    "HomeUi",
    "HOME_UI",
    "VodHome",
    "Launcher",
    "MainHome",
    "MainHomeActivity",
    "UplusHome",
    "HomeMain",
    "SmartHome",
    "iptv3.base.launcher",
)
# 검색·런처 — 숫자 키패드가 채널 튜닝이 아닌 UI 입력으로 먹힘
SEARCH_UI_ACTIVITY_MARKERS = (
    "newsearch",
    "iptv3.base.newsearch",
    "SearchActivity",
    "TvSearch",
)
# OCR/UI 힌트 — Activity 를 모를 때 스크린샷·덤프로 화면 종류 추정
SCREEN_OCR_SEARCH_HINTS = ("검색", "search", "찾고 싶은", "통합검색")
SCREEN_OCR_HOME_HINTS = ("마이메뉴", "추천", "홈쇼핑", "vod", "실시간", "시리즈")
SCREEN_OCR_PURCHASE_HINTS = ("가입", "구매", "비밀번호", "유료")
SCREEN_OCR_DISMISS_HINTS = ("나가기", "닫기", "취소", "이전 단계")
NON_LINEAR_TV_EXIT_LABELS = HOME_EXIT_BUTTON_LABELS + (
    "나가기",
    "닫기",
    "취소",
    "이전",
    "이전 단계",
    "확인",
)
NON_LINEAR_TV_RECOVERY_COOLDOWN_SEC = int(
    os.environ.get("NON_LINEAR_TV_RECOVERY_COOLDOWN_SEC", "15")
)
# 채널 전환 직후 not linear 로그는 무시 (튜닝 중 오탐)
NON_LINEAR_TV_GRACE_AFTER_TUNE_SEC = int(
    os.environ.get("NON_LINEAR_TV_GRACE_AFTER_TUNE_SEC", "25")
)
_non_linear_tv_recovery_at = {}
_non_linear_tv_recovery_lock = threading.Lock()
_ad_sync_recovery_at = {}
_ad_sync_recovery_lock = threading.Lock()
# 메인 루프·비선형 복구 스레드가 동시에 키패드를내면 ch25→5, 121→21 등으로 깨짐
_channel_switch_lock = threading.Lock()
# 채널 전환 중 not linear 복구 시 stale tracker 대신 이 목표 채널 사용
_pending_tune_targets = {}
_pending_tune_lock = threading.Lock()
OSD_CHANNEL_POLL_INTERVAL_SEC = float(
    os.environ.get("OSD_CHANNEL_POLL_INTERVAL_SEC", "0.5")
)
OSD_CHANNEL_POLL_TIMEOUT_SEC = float(
    os.environ.get("OSD_CHANNEL_POLL_TIMEOUT_SEC", "12")
)
CHANNEL_KEYPAD_READY_SEC = float(os.environ.get("CHANNEL_KEYPAD_READY_SEC", "0.8"))
CHANNEL_SWITCH_MAX_RETRIES = int(os.environ.get("CHANNEL_SWITCH_MAX_RETRIES", "3"))
CHANNEL_SWITCH_RETRY_DELAY_SEC = float(
    os.environ.get("CHANNEL_SWITCH_RETRY_DELAY_SEC", "2")
)
CHANNEL_SWITCH_MIN_GAP_SEC = float(os.environ.get("CHANNEL_SWITCH_MIN_GAP_SEC", "1.5"))
CHANNEL_SWITCH_SETTLE_SEC = float(os.environ.get("CHANNEL_SWITCH_SETTLE_SEC", "2.5"))
CHANNEL_TUNE_VERIFY_SEC = float(os.environ.get("CHANNEL_TUNE_VERIFY_SEC", "12"))
TUNE_CATALOG_FALLBACK_TIMEOUT_SEC = float(
    os.environ.get("TUNE_CATALOG_FALLBACK_TIMEOUT_SEC", "40")
)
CHANNEL_CATALOG_SETTLE_SEC = float(os.environ.get("CHANNEL_CATALOG_SETTLE_SEC", "1.5"))
# True 일 때만 채널 전환 직전 logcat -c (CM·광고 로그 삭제 → 오탐·미감지 유발)
CHANNEL_SWITCH_CLEAR_LOG = _env_truthy("CHANNEL_SWITCH_CLEAR_LOG")
_last_successful_tune_at = 0.0


def _status_line(title, state):
    if not state or not isinstance(state, dict):
        return f"{title} ❌ 확인전"
    if state.get("skipped"):
        return f"{title} ⏭ 스킵"
    if not state.get("done"):
        return f"{title} ❌ 확인전"
    # 완료는 했고, pass/fail은 괄호로만 표시
    if state.get("ok") is True:
        return f"{title} ✅ 확인완료(성공)"
    if state.get("ok") is False:
        return f"{title} ✅ 확인완료(실패)"
    return f"{title} ✅ 확인완료"


def _google_check_tag(sub=None) -> str:
    """터미널용 '체크 3-A' 등."""
    sub = sub or _active_google_subtest
    if sub == GOOGLE_MONITOR_SUB:
        return "모니터링/구글"
    if sub and sub in GOOGLE_CHECK3_TAGS:
        return f"체크 {GOOGLE_CHECK3_TAGS[sub]}"
    return "체크 3"


def _google_sub_status(sub: str) -> dict:
    if _run_checklist.get("google_skipped"):
        return {"done": True, "ok": None, "skipped": True}
    g = _run_checklist.get("google_ad") or {}
    r = g.get(sub)
    if isinstance(r, dict) and r.get("done"):
        return {"done": True, "ok": bool(r.get("ok"))}
    if _check_attempts_exhausted(_google_sub_attempt_key(sub)):
        return {"done": True, "ok": False}
    return {"done": False, "ok": None}


def _google_group_status() -> dict:
    """3. 광고 재생(구글) — 3-A/B/C 통합 상태."""
    if _run_checklist.get("google_skipped"):
        return {"done": True, "ok": None, "skipped": True}
    subs = [_google_sub_status(sub) for sub in GOOGLE_CHECK3_SUBTESTS]
    if not all(s.get("done") for s in subs):
        return {"done": False, "ok": None}
    return {"done": True, "ok": google_check3_all_passed()}


def print_checklist_progress(device_ips=None):
    """체크리스트 진행상태 출력 (구글 3-A/3-B/3-C 분리)."""
    device_ips = device_ips or []
    if _run_checklist.get("versions_skipped"):
        ver_done = True
        ver_ok = None
    else:
        versions = _run_checklist.get("versions") or {}
        ver_done = bool(versions)
        ver_ok = False
        if ver_done:
            try:
                ver_ok = all(
                    all(v.values()) for v in versions.values() if isinstance(v, dict)
                )
            except Exception:
                ver_ok = False

    ad2 = _run_checklist.get("ad_playback")
    ad2_done = isinstance(ad2, dict) and ad2.get("done")
    ad2_ok = isinstance(ad2, dict) and ad2.get("ok")

    # 4/5/6
    leave4 = _run_checklist.get("leave_during_ad")
    leave5 = _run_checklist.get("leave_before_play")
    c6 = _run_checklist.get("kids_check6")
    if isinstance(c6, dict):
        kids_done = bool(c6.get("done"))
        kids_ok = bool(c6.get("ok"))
    else:
        kids = _run_checklist.get("kids_watermark") or {}
        kids_done = bool(kids)
        kids_ok = kids_done and all(bool(v) for v in kids.values())

    # 상태 dict로 통일
    if _run_checklist.get("versions_skipped"):
        s1 = {"done": True, "ok": None, "skipped": True}
    else:
        s1 = {"done": ver_done, "ok": ver_ok}
    s2 = {"done": ad2_done, "ok": ad2_ok}
    s4 = {
        "done": isinstance(leave4, dict) and bool(leave4.get("done")),
        "ok": leave4.get("ok") if isinstance(leave4, dict) else None,
    }
    s5 = {
        "done": isinstance(leave5, dict) and bool(leave5.get("done")),
        "ok": leave5.get("ok") if isinstance(leave5, dict) else None,
    }
    s6 = {"done": kids_done, "ok": kids_ok}

    term_print("\n--- 진행상태 ---")
    term_print(_status_line("1. S/W ver. 확인", s1))
    term_print(_status_line("2. 광고 재생(내부 소재)", s2))
    term_print(_status_line("3. 광고 재생(구글)", _google_group_status()))
    for sub in GOOGLE_CHECK3_SUBTESTS:
        sub_title = GOOGLE_CHECK3_LABELS[sub].split(" ", 1)[-1]
        term_print(
            _status_line(f"   {GOOGLE_CHECK3_TAGS[sub]} {sub_title}", _google_sub_status(sub))
        )
    term_print(_status_line("4. 재생 중 채널 이탈 → impression", s4))
    term_print(_status_line("5. 목록 후·play 전 이탈 → 미재생", s5))
    term_print(_status_line("6. 키즈 채널 워터마크", s6))
    if device_ips:
        _on_checklist_progress(device_ips)


def _checklist_chat_fingerprint():
    """Chat 진행 스냅샷용 — 항목별 (done여부, ok) 변화 감지."""
    g = _run_checklist.get("google_ad") or {}

    def _sub(key):
        r = g.get(key)
        if isinstance(r, dict) and r.get("done"):
            return (True, bool(r.get("ok")))
        return (False, None)

    ad2 = _run_checklist.get("ad_playback")
    leave4 = _run_checklist.get("leave_during_ad")
    leave5 = _run_checklist.get("leave_before_play")
    c6 = _run_checklist.get("kids_check6")
    return (
        ("2", (bool(ad2 and ad2.get("done")), bool(ad2.get("ok")) if ad2 else None)),
        ("3a", _sub("full_play")),
        ("3b", _sub("leave_during")),
        ("3c", _sub("skip_ok")),
        (
            "4",
            (
                bool(leave4 and leave4.get("done")),
                bool(leave4.get("ok")) if isinstance(leave4, dict) else None,
            ),
        ),
        (
            "5",
            (
                bool(leave5 and leave5.get("done")),
                bool(leave5.get("ok")) if isinstance(leave5, dict) else None,
            ),
        ),
        (
            "6",
            (
                bool(c6 and c6.get("done")),
                bool(c6.get("ok")) if isinstance(c6, dict) else None,
            ),
        ),
    )


def _checklist_any_done() -> bool:
    fp = _checklist_chat_fingerprint()
    return any(done for _, (done, _ok) in fp)


def _on_checklist_progress(device_ips):
    """체크 완료·상태 변경 시 Chat 진행 반영 + 1차/최종 리포트 트리거."""
    global _last_progress_chat_fingerprint
    if not device_ips:
        return
    fp = _checklist_chat_fingerprint()
    if (
        not _chat_report_sent
        and fp != _last_progress_chat_fingerprint
        and _checklist_any_done()
    ):
        _last_progress_chat_fingerprint = fp
        _send_google_chat(
            device_ips,
            title="*STB QA 진행*",
            label="진행 업데이트",
            include_log_attachments=False,
        )
    _maybe_send_checklist_reports(device_ips)


def _effective_max_attempts() -> int:
    """1차 리포트 이후(extra phase)에는 실패 항목 재시도 상한을 늘린다."""
    if _checklist_extra_phase:
        return CHECKLIST_CHECK_MAX_ATTEMPTS + CHECKLIST_EXTRA_ROUND_ATTEMPTS
    return CHECKLIST_CHECK_MAX_ATTEMPTS


def _check_attempts_exhausted(check_key: str) -> bool:
    return _check_attempt_counts.get(check_key, 0) >= _effective_max_attempts()


def _bump_check_attempt(check_key: str) -> int:
    n = _check_attempt_counts.get(check_key, 0) + 1
    _check_attempt_counts[check_key] = n
    return n


def _unbump_check_attempt(check_key: str) -> int:
    n = max(0, _check_attempt_counts.get(check_key, 0) - 1)
    if n:
        _check_attempt_counts[check_key] = n
    else:
        _check_attempt_counts.pop(check_key, None)
    return n


def _enter_checklist_extra_phase():
    """1차 리포트 후: 실패(소진) 항목에 추가 재시도 라운드 부여."""
    global _checklist_extra_phase
    if _checklist_extra_phase:
        return
    _checklist_extra_phase = True
    # 상한이 늘었으니 재소진 시 다시 안내되도록 초기화
    _check_exhausted_announced.clear()
    term_print(
        f"{current_time_str()} [체크리스트] 실패 항목 추가 재시도 "
        f"(상한 {CHECKLIST_CHECK_MAX_ATTEMPTS} → {_effective_max_attempts()}회)"
    )


def _announce_check_exhausted(check_key: str, label: str):
    if check_key in _check_exhausted_announced:
        return
    _check_exhausted_announced.add(check_key)
    term_print(
        f"{current_time_str()} [체크리스트] {label} "
        f"{_effective_max_attempts()}회 시도 실패 — 재시도 중단"
    )


def _needs_check_item(check_key: str, result_key: str, label: str) -> bool:
    item = _run_checklist.get(result_key)
    if isinstance(item, dict) and item.get("ok"):
        return False
    if _check_attempts_exhausted(check_key):
        _announce_check_exhausted(check_key, label)
        return False
    return True


def _other_checks_passed_for_check2_bonus() -> bool:
    """3·4·5·6 성공 시 체크 2 보너스 재시도 가능."""
    leave4 = _run_checklist.get("leave_during_ad")
    leave5 = _run_checklist.get("leave_before_play")
    return (
        google_check3_all_passed()
        and isinstance(leave4, dict)
        and leave4.get("ok")
        and isinstance(leave5, dict)
        and leave5.get("ok")
        and kids_check6_passed()
    )


def _check2_bonus_remaining() -> int:
    return max(0, CHECK2_BONUS_MAX_ATTEMPTS - _check2_bonus_used)


def needs_check_2():
    item = _run_checklist.get("ad_playback")
    if isinstance(item, dict) and item.get("ok"):
        return False
    if not _check_attempts_exhausted("ad_playback"):
        return True
    if _other_checks_passed_for_check2_bonus() and _check2_bonus_remaining() > 0:
        return True
    if _check_attempts_exhausted("ad_playback"):
        _announce_check_exhausted("ad_playback", "2 광고재생")
    return False


def _bump_check2_attempt() -> tuple[int, int, bool]:
    """체크 2 시도 카운트 — (번호, 상한, 보너스 여부)."""
    global _check2_bonus_announced, _check2_bonus_used
    if not _check_attempts_exhausted("ad_playback"):
        n = _bump_check_attempt("ad_playback")
        return n, CHECKLIST_CHECK_MAX_ATTEMPTS, False
    _check2_bonus_used += 1
    if not _check2_bonus_announced:
        _check2_bonus_announced = True
        term_print(
            f"{current_time_str()} [체크 2] 3·4·5·6 완료 — "
            f"보너스 재시도 최대 {CHECK2_BONUS_MAX_ATTEMPTS}회"
        )
    return _check2_bonus_used, CHECK2_BONUS_MAX_ATTEMPTS, True


def _check2_playtime_ok(total_ms: int, expected_ms: int) -> bool:
    """체크 2: playTime 합 ∈ [cue−2초, cue] (cue 초과는 없음)."""
    if expected_ms <= 0:
        expected_ms = EXPECTED_AD_PLAYTIME_MS
    floor_ms = max(0, expected_ms - CHECK2_PLAYTIME_UNDER_CUE_MS)
    return floor_ms <= total_ms <= expected_ms


def needs_check_4():
    return _needs_check_item(
        "leave_during_ad", "leave_during_ad", "4 재생중 이탈"
    )


def needs_check_5():
    return _needs_check_item(
        "leave_before_play", "leave_before_play", "5 play전 이탈"
    )


def _google_sub_attempt_key(sub: str) -> str:
    return f"google_{sub}"


def google_check3_all_passed() -> bool:
    if _run_checklist.get("google_skipped"):
        return True
    g = _run_checklist.get("google_ad") or {}
    for sub in GOOGLE_CHECK3_SUBTESTS:
        r = g.get(sub)
        if not isinstance(r, dict) or not r.get("ok"):
            return False
    return True


def _next_google_sub_test():
    if _run_checklist.get("google_skipped"):
        return None
    g = _run_checklist.setdefault("google_ad", {})
    for sub in GOOGLE_CHECK3_SUBTESTS:
        r = g.get(sub)
        if isinstance(r, dict) and r.get("ok"):
            continue
        if _check_attempts_exhausted(_google_sub_attempt_key(sub)):
            _announce_check_exhausted(
                _google_sub_attempt_key(sub), GOOGLE_CHECK3_LABELS[sub]
            )
            continue
        if not isinstance(r, dict) or not r.get("done") or not r.get("ok"):
            return sub
    return None


def needs_check_3():
    if _run_checklist.get("google_skipped"):
        return False
    return _next_google_sub_test() is not None


def kids_check6_passed():
    c6 = _run_checklist.get("kids_check6")
    return isinstance(c6, dict) and c6.get("ok")


def _pending_checks_summary():
    """미완료 체크리스트 항목 — 대기/진행 로그용."""
    pending = []
    if needs_check_2():
        pending.append("2 광고재생(cue−2초~cue+API)")
    if needs_check_4():
        pending.append("4 재생중 이탈·impression")
    if needs_check_3():
        sub = _next_google_sub_test()
        if sub:
            pending.append(GOOGLE_CHECK3_LABELS.get(sub, "3 구글"))
    if needs_check_5():
        pending.append("5 play전 이탈(미재생)")
    if not kids_check6_passed():
        pending.append("6 키즈 워터마크")
    return pending


def _only_check6_pending() -> bool:
    return (
        not kids_check6_passed()
        and not needs_check_2()
        and not needs_check_3()
        and not needs_check_4()
        and not needs_check_5()
    )


def _should_force_kids_prime_priority(now=None) -> bool:
    """6번 미완료 + 현재 :50~:59 → 키즈 prime 편성만 선택."""
    now = now or datetime.now()
    return not kids_check6_passed() and is_kids_prime_time(now)


def _skip_non_kids_for_check6(channel_number) -> bool:
    """6번만 남았거나 prime 구간이면 일반 채널 슬롯을 건너뜀."""
    if kids_check6_passed() or is_kids_channel(channel_number):
        return False
    return _should_force_kids_prime_priority() or _only_check6_pending()


def _describe_next_check_action(channel_number):
    """다음 슬롯에서 수행할 체크 (우선순위 반영)."""
    if checklist_all_done([]):
        if is_kids_channel(channel_number):
            return "편성 모니터링(키즈 워터마크·OCR 포함)"
        return "편성 모니터링(내부·구글)"
    if is_kids_channel(channel_number) and not kids_check6_passed():
        return "6 키즈 logcat + 광고방송 OCR"
    if not kids_check6_passed() and (
        _should_force_kids_prime_priority() or _only_check6_pending()
    ):
        return "6 키즈 prime 대기 (:50~:59)"
    if needs_check_5():
        return (
            f"5 register cue 후 +{CHECK5_LEAVE_AFTER_REGISTER_SEC}초 이탈 "
            f"(play {CHECK5_LEAVE_BEFORE_PLAY_SEC:.0f}초 전 상한)"
        )
    if needs_check_4():
        return "4 play 후 30초 이탈 → player play→stop≈playTime합"
    if needs_check_3():
        sub = _next_google_sub_test()
        if sub:
            return GOOGLE_CHECK3_LABELS.get(sub, "3 구글 광고")
    if needs_check_2():
        return "2 playTime cue−2초~cue + impression API 200"
    return "편성 모니터링"


def _log_monitor_wait_status(
    channel_name,
    channel_number,
    ad_time_str,
    switch_time,
    wait_sec,
    *,
    force=False,
):
    """다음 편성 전 대기 중 — 무엇을 확인할 예정인지 주기적으로 출력."""
    global _last_wait_status_log_at, _last_wait_status_slot_key
    slot_key = (normalize_channel_number(channel_number), ad_time_str)
    if slot_key != _last_wait_status_slot_key:
        _last_wait_status_slot_key = slot_key
        force = True
    now_ts = time.time()
    if not force and now_ts - _last_wait_status_log_at < WAIT_STATUS_LOG_INTERVAL_SEC:
        return
    _last_wait_status_log_at = now_ts
    pending = _pending_checks_summary()
    action = _describe_next_check_action(channel_number)
    switch_str = switch_time.strftime("%H:%M:%S")
    pending_str = ", ".join(pending) if pending else "(없음)"
    term_print(
        f"{current_time_str()} [대기] {channel_name}({channel_number}) "
        f"@ {ad_time_str} — ch 전환 {switch_str} ({max(0, int(wait_sec))}초 후)\n"
        f"  미완료: {pending_str}\n"
        f"  다음 슬롯 작업: {action}"
    )


def has_pending_checklist_work(channel_number) -> bool:
    """이 슬롯에서 체크 2·3·4·5·6 중 아직 수행할 작업이 있는지."""
    if is_kids_channel(channel_number) and not kids_check6_passed():
        return True
    return needs_check_5() or needs_check_4() or needs_check_3() or needs_check_2()


def checklist_all_done(devices):
    """체크 2·3·4·5·6 성공 (1 버전 스킵 시 제외)."""
    if _run_checklist.get("versions_skipped"):
        ver_ok = True
    else:
        versions = _run_checklist.get("versions") or {}
        ver_ok = bool(versions) and all(
            all(v.values()) for v in versions.values() if isinstance(v, dict)
        )
    ad2 = _run_checklist.get("ad_playback")
    ad2_ok = isinstance(ad2, dict) and ad2.get("ok")
    leave4 = _run_checklist.get("leave_during_ad")
    leave5 = _run_checklist.get("leave_before_play")
    leave4_ok = isinstance(leave4, dict) and leave4.get("ok")
    leave5_ok = isinstance(leave5, dict) and leave5.get("ok")
    c6 = _run_checklist.get("kids_check6")
    kids_ok = isinstance(c6, dict) and c6.get("ok")
    google_ok = google_check3_all_passed()
    return ver_ok and ad2_ok and google_ok and leave4_ok and leave5_ok and kids_ok


def checklist_round_complete() -> bool:
    """이번 라운드 처리 완료: 체크 2·3·4·5 pass/재시도소진 + 6 pass/확정.

    (현재 상한 기준. extra phase 진입 시 소진 항목이 다시 미완료가 되어 False)
    """
    if needs_check_2() or needs_check_3() or needs_check_4() or needs_check_5():
        return False
    if kids_check6_passed():
        return True
    c6 = _run_checklist.get("kids_check6")
    if isinstance(c6, dict) and c6.get("done"):
        return True
    return False


# 재부팅 후 logcat 버전 (그룹별 여러 표기 허용, findstr 미사용)
# 표시·요약 순서: 펌웨어 → SDK → Agent (dict 삽입 순서 유지)
FIRMWARE_VERSION_PATTERN = re.compile(r"V\.\d+\.\d+\.\d+")
# logcat 예: "- firmware ver(full)  : LGUplus/.../20260115_V.02.02.0191:..."
FIRMWARE_FULL_LINE_RE = re.compile(
    r"firmware\s+ver\s*\(\s*full\s*\)\s*[:=]\s*(\S+)",
    re.IGNORECASE,
)
# 참고: device/AdConfig JSON — "sdkVersion":{"code":240729022,"name":"2.0.9-RC1_20250822"}
SDK_VERSION_JSON_RE = re.compile(
    r'"sdkVersion"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
# logcat 예: sdkVersion=VersionInfo(code=240729026, name=2.0.11-RC2_20260430)
SDK_VERSION_INFO_RE = re.compile(
    r"sdkVersion\s*=\s*VersionInfo\s*\([^)]*?\bname\s*=\s*([^,\s)\"]+)",
    re.IGNORECASE,
)
# 넓은 형태: sdkVersion … name=2.0.11-RC2_20260430 (따옴표 선택)
SDK_VERSION_NAME_RE = re.compile(
    r"sdkVersion[^\n]{0,120}?\bname\s*=\s*[\"']?([^,\"'\s)]+)",
    re.IGNORECASE,
)

VERSION_GROUPS = {
    "firmware": {
        "label": "Firmware",
        # firmware ver: 12 (짧은 값)은 무시 — ver(full) 줄에서 V.xx.xx.xxxx 만 사용
        "needles": [
            "firmware ver(full)",
            "firmware ver (full)",
        ],
    },
    "sdk": {
        "label": "SDK",
        "needles": [
            "anypoint sdk version:",
            "anypoint sdk version",
            "Anypoint SDK version:",
            "sdkVersion=VersionInfo",
            "sdkVersion",
        ],
        # 보조: sdkVersion.name (JSON / VersionInfo)
    },
    "agent": {
        "label": "Agent (APK)",
        "needles": [
            "anypoint apk version:",
            "anypoint apk version",
        ],
    },
}

_print_lock = threading.Lock()
_terminal_log_fp = None


def _resolve_terminal_log_path():
    """STB_TERMINAL_LOG 또는 STB_LOG_FILE 기반 UTF-8 터미널 미러 로그."""
    explicit = os.environ.get("STB_TERMINAL_LOG", "").strip()
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(LOG_DIR, explicit)
    log_file = os.environ.get("STB_LOG_FILE", "").strip()
    if log_file:
        base = os.path.splitext(os.path.basename(log_file))[0]
        return os.path.join(LOG_DIR, f"{base}_terminal.log")
    return None


def open_terminal_log_mirror():
    """PowerShell Tee-Object(cp949) 깨짐 방지 — Python에서 UTF-8 로그 직접 기록."""
    global _terminal_log_fp
    path = _resolve_terminal_log_path()
    if not path:
        return None
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    is_new = not os.path.isfile(path) or os.path.getsize(path) == 0
    _terminal_log_fp = open(path, "a", encoding="utf-8", newline="\n")
    if is_new:
        _terminal_log_fp.write("\ufeff")
    _terminal_log_fp.flush()
    return path


def close_terminal_log_mirror():
    global _terminal_log_fp
    if _terminal_log_fp:
        try:
            _terminal_log_fp.close()
        except Exception:
            pass
        _terminal_log_fp = None


def term_print(*args, **kwargs):
    """터미널 출력 + UTF-8 미러 로그 (멀티스레드 안전)."""
    kwargs.setdefault("flush", True)
    with _print_lock:
        print(*args, **kwargs)
        if _terminal_log_fp is not None:
            sep = kwargs.get("sep", " ")
            end = kwargs.get("end", "\n")
            line = sep.join(str(a) for a in args) + end
            _terminal_log_fp.write(line)
            _terminal_log_fp.flush()


keyevent_map = {str(i): 7 + i for i in range(10)}
# 채널 번호 입력 확정 키
# - KEYCODE_ENTER: 66
# - KEYCODE_DPAD_CENTER(OK): 23
# STB/앱에 따라 둘 중 하나만 먹는 경우가 있어 둘 다 전송한다.
KEYCODE_ENTER = 66
KEYCODE_DPAD_CENTER = 23
KEYCODE_DEL = 67
KEYCODE_BACK = 4
KEYCODE_TV = 170  # 홈/검색 → 라이브 TV (UplusMainActivity)
# 유료가입·비밀번호 팝업 — 채널 전환 전 탭할 버튼(앞쪽 우선)
DISMISS_UI_BUTTON_LABELS = (
    "나가기",
    "가입 취소",
    "취소",
    "닫기",
    "이전 단계",
)
BLOCKING_UI_ACTIVITY_MARKERS = (
    "PurchaseActivity",
    "PassCheck",
    ".purchase.",
)
UI_DUMP_REMOTE_PATH = "/sdcard/_qa_ui_automation.xml"
DISMISS_UI_MAX_ROUNDS = int(os.environ.get("DISMISS_UI_MAX_ROUNDS", "4"))
DISMISS_UI_SETTLE_SEC = float(os.environ.get("DISMISS_UI_SETTLE_SEC", "0.9"))
# 채널 키패드: stb_multi_gspread_sheet_read 와 동일 (편성 번호 그대로)
# 1자리(3)는 입력 후 대기 시 바로 튜닝됨 → 3자리(322)는 빠른 연속 입력, OK 생략
MULTI_CHANNEL_DIGIT_DELAY_SEC = float(
    os.environ.get("MULTI_CHANNEL_DIGIT_DELAY_SEC", "0.2")
)
MULTI_CHANNEL_BURST_DELAY_SEC = float(
    os.environ.get("MULTI_CHANNEL_BURST_DELAY_SEC", "0.05")
)
MULTI_CHANNEL_OK_DELAY_SEC = float(
    os.environ.get("MULTI_CHANNEL_OK_DELAY_SEC", "0.35")
)
# 3자리 이상은 기본 OK 없음 (322 입력 후 OK 누르면 선행 3이 ch3으로 확정되는 단말 있음)
CHANNEL_OK_FOR_3DIGIT = _env_truthy("CHANNEL_OK_FOR_3DIGIT")
# 이탈/마지막 채널 — 1자리(3)가 2자리(25)보다 키패드 전환 빠름 (DEL 불필요)
DEFAULT_ESCAPE_CHANNEL = "3"
KEYCODE_HOME = 3
# LGU: HOME(3) → 홈/VOD 런처. 라이브 복귀는 KEYCODE_TV(170).
# (홈 MainHome에는 '종료' 텍스트가 없고, 검색 UI에 채널 숫자 입력하면 검색창에 먹힘)
LIVE_TV_ACTIVITY_MARKERS = (
    "UplusMainActivity",
    "livetvinput",
    "pineone",
    "LiveTv",
    "TvInput",
    "com.lguplus.android.tv",
)
OSD_CHANNEL_LOG_PATTERN = re.compile(
    r"getOsdChannelForChannelNumber\(\)\[\d+\]\s*>>\s*channelNumber\s*:\s*(\d+)"
)
# LGU STB: LIVE-LGE-CM extraData 가 실제 채널 번호 (getOsdChannel 로그는 없는 단말 다수)
LGU_CM_TUNED_CHANNEL_PATTERN = re.compile(
    r"CM_Handler_notify:.*?extraData=(\d+)"
)
# CM 로그 없는 단말: register cue / ProgramProviderChannel.id 로 튜닝 확인
TUNE_FALLBACK_CATALOG_CUE = not _env_truthy("TUNE_NO_CATALOG_CUE_FALLBACK")
CHECK5_LEAVE_POLL_SEC = 0.1


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def normalize_device_ip(ip):
    ip = ip.strip()
    if not ip:
        return ip
    if ":" not in ip:
        ip = f"{ip}:5555"
    return ip


def _split_device_ip_tokens(value: str) -> list[str]:
    """쉼표·세미콜론·공백으로 구분된 IP 목록 파싱."""
    return [p.strip() for p in re.split(r"[,;\s]+", value.strip()) if p.strip()]


def resolve_device_ips():
    """환경변수(STB_DEVICE_IPS / STB_DEVICE_IP) 또는 대화형(get_device_ips)으로 STB 목록."""
    env_ips = os.environ.get("STB_DEVICE_IPS", "").strip()
    env_single = os.environ.get("STB_DEVICE_IP", "").strip()
    if env_ips:
        parts = []
        for chunk in env_ips.split(";"):
            parts.extend(_split_device_ip_tokens(chunk))
        return [normalize_device_ip(p) for p in parts if p]
    if env_single:
        tokens = _split_device_ip_tokens(env_single)
        return [normalize_device_ip(p) for p in tokens if p]
    raw = get_device_ips()
    return [normalize_device_ip(ip) for ip in raw if ip and ip.strip()]


def _prompt_skip_reboot_if_unset():
    """SKIP_REBOOT 미설정 시 대화형으로 재부팅 여부 확인. 이미 env면 그대로 둠.

    y/yes → 재부팅 1회 + 버전 확인 (SKIP_REBOOT=0)
    n/엔터 → 재부팅·버전 스킵 (SKIP_REBOOT=1, ps1 기본과 동일)
    """
    raw = os.environ.get("SKIP_REBOOT")
    if raw is not None and str(raw).strip() != "":
        return
    ans = input(
        "시작 시 재부팅 후 S/W ver. 확인을 할까요? "
        "[y=재부팅+버전 / N=스킵(기본)]: "
    ).strip().lower()
    if ans in ("y", "yes", "1", "true"):
        os.environ["SKIP_REBOOT"] = "0"
        print("  → 재부팅 1회 + Firmware/SDK/Agent 버전 확인")
    else:
        os.environ["SKIP_REBOOT"] = "1"
        print("  → 재부팅·버전 확인 스킵 (편성 모니터링만)")


def build_local_api_endpoint(host: str, model: str) -> str:
    """http://{host}/{model} 형태. host에 scheme 없으면 http:// 붙임."""
    host = (host or "").strip().rstrip("/")
    model = (model or "").strip().strip("/")
    if not host or not model:
        raise ValueError("host/model 필요")
    if "://" not in host:
        host = f"http://{host}"
    return f"{host}/{model}"


def _api_endpoints_from_env(device_ips) -> dict | None:
    """env로 완전 결정되면 dict, 명시적 스킵이면 {}, 대화형 필요면 None."""
    apply_raw = os.environ.get("APPLY_LOCAL_API_ENDPOINT")
    if apply_raw is not None and str(apply_raw).strip() != "":
        if str(apply_raw).strip().lower() in ("0", "false", "n", "no"):
            return {}
    full = os.environ.get("STB_API_ENDPOINT", "").strip().rstrip("/")
    if full:
        return {d: full for d in device_ips}
    host = os.environ.get("STB_LOCAL_API_HOST", "").strip() or DEFAULT_LOCAL_API_HOST
    model = os.environ.get("STB_LOCAL_API_MODEL", "").strip()
    if _env_truthy("APPLY_LOCAL_API_ENDPOINT") and model:
        return {d: build_local_api_endpoint(host, model) for d in device_ips}
    return None


def prompt_local_api_endpoints(device_ips) -> dict:
    """기기별 local API endpoint 수집. 예: http://192.168.10.150/UHD3

    - STB_API_ENDPOINT / APPLY_LOCAL_API_ENDPOINT+MODEL 이면 비대화형
    - APPLY_LOCAL_API_ENDPOINT=0 이면 스킵
    - 그 외: y/N → PC 호스트 + 기기별 모델명
    """
    resolved = _api_endpoints_from_env(device_ips)
    if resolved is not None:
        if resolved:
            print("API endpoint (env):")
            for d, url in resolved.items():
                print(f"  [{d}] → {url}")
        return resolved

    ans = input(
        "API endpoint를 local(PC)로 변경할까요? "
        "(구글/mock 테스트, 예: http://192.168.10.150/UHD3) [y/N]: "
    ).strip().lower()
    if ans not in ("y", "yes", "1", "true"):
        print("  → endpoint 변경 안 함")
        return {}

    default_host = DEFAULT_LOCAL_API_HOST or "192.168.10.150"
    host = input(
        f"PC API 호스트 (IP 또는 http://IP) [{default_host}]: "
    ).strip() or default_host
    model_default = os.environ.get("STB_LOCAL_API_MODEL", "").strip()
    out = {}
    for device in device_ips:
        hint = (
            f" [{model_default}]"
            if model_default
            else " (예: UHD3, UHD4K — 셋탑 종류별)"
        )
        model = input(f"  [{device}] 모델명{hint}: ").strip() or model_default
        if not model:
            print(f"  [{device}] 모델명 없음 — 스킵")
            continue
        url = build_local_api_endpoint(host, model)
        out[device] = url
        print(f"  [{device}] → {url}")
    return out


def send_change_api_endpoint(device: str, endpoint: str) -> bool:
    """Agent CHANGE_API_ENDPOINT 브로드캐스트."""
    cmd = [
        "adb",
        "-s",
        device,
        "shell",
        "am",
        "broadcast",
        "-a",
        CHANGE_TEST_PROPERTY_ACTION,
        "--es",
        "change.command",
        "CHANGE_API_ENDPOINT",
        "--es",
        "api.endpoint",
        endpoint,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        term_print(f"{current_time_str()} [{device}] [endpoint] 전송 실패: {e}")
        return False
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:200]
        term_print(
            f"{current_time_str()} [{device}] [endpoint] 실패 "
            f"(rc={result.returncode}): {err}"
        )
        return False
    out = (result.stdout or "").strip()
    term_print(
        f"{current_time_str()} [{device}] [endpoint] → {endpoint}"
        + (f" | {out[:120]}" if out else "")
    )
    return True


def apply_pending_api_endpoints(device_ips=None):
    """연결·재부팅 이후 pending endpoint 적용 + (선택) AD_SYNC."""
    global _pending_api_endpoints
    targets = _pending_api_endpoints or {}
    if not targets:
        return
    devices = device_ips or list(targets.keys())
    term_print(
        f"{current_time_str()} [endpoint] local API 적용 "
        f"({len(targets)}대) — CHANGE_API_ENDPOINT"
    )
    ok_devices = []
    for device in devices:
        endpoint = targets.get(device)
        if not endpoint:
            continue
        if send_change_api_endpoint(device, endpoint):
            ok_devices.append(device)
    if not ok_devices:
        return
    time.sleep(API_ENDPOINT_APPLY_SETTLE_SEC)
    if _env_truthy("API_ENDPOINT_AD_SYNC", default=True):
        for device in ok_devices:
            send_ad_sync_broadcast(device)


def prompt_run_config():
    """연결 대수, IP, 로그 파일명, 재부팅·local endpoint 입력 (env로 비대화형 가능)."""
    global _pending_api_endpoints
    device_ips = resolve_device_ips()
    if not device_ips:
        print("연결된 디바이스가 없습니다.")
        sys.exit(1)

    log_filename = os.environ.get("STB_LOG_FILE", "").strip()
    if not log_filename:
        log_filename = input(
            "저장할 로그 파일명을 입력하세요 (예: default_behavior.log): "
        ).strip()
    if not log_filename:
        log_filename = "default_behavior.log"

    _prompt_skip_reboot_if_unset()
    _pending_api_endpoints = prompt_local_api_endpoints(device_ips)

    return device_ips, log_filename


def _ad_playback_started(devices):
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and (
            tracker.phases.get("play_start") or tracker.phases.get("player_play")
        ):
            return True
    return False


def _ad_playback_stopped(devices) -> bool:
    """player stop / onStopped — 광고 종료 후 OCR 중단용."""
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and (
            tracker.phases.get("player_stop") or tracker.phases.get("on_stopped")
        ):
            return True
    return False


def _slot_ad_pending_signal(devices) -> bool:
    """register cue / 재생 예약 / load / play 또는 구글 adEvent."""
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and (
            tracker.register_cue_seen
            or tracker.cue_ready
            or tracker.phases.get("ads_scheduled")
            or tracker.phases.get("load")
            or tracker.phases.get("play_start")
            or tracker.phases.get("player_play")
        ):
            return True
        g = _google_tracker_for(device)
        if g and g.event_names():
            return True
    return False


def clear_slot_watches(devices):
    with _ad_tracker_lock:
        for device in devices:
            _active_ad_trackers.pop(device, None)
    with _kids_watermark_lock:
        for device in devices:
            _active_kids_watermark_trackers.pop(device, None)
    clear_google_ad_watch(devices)


def clear_kids_watermark_pending(devices, channel_number):
    """스킵된 키즈 슬롯의 미확정 워터마크 플래그(False)만 제거.

    광고 미송출로 슬롯을 건너뛰면 start_kids_watermark_watch 가 깔아둔 False
    placeholder 가 남아 진행상태/최종보고가 '확인완료(실패)'로 오인된다. 성공(True)은
    보존하고 미확정만 지워 '확인전/미시청'으로 복원한다.
    """
    ch = normalize_channel_number(channel_number)
    wm = _run_checklist.get("kids_watermark")
    if not isinstance(wm, dict):
        return
    with _kids_watermark_lock:
        for device in devices:
            key = (device, ch)
            if wm.get(key) is False:
                wm.pop(key, None)


def _slot_ad_start_timeout_sec(channel_number, ad_time_str) -> int:
    return SLOT_AD_START_TIMEOUT_SEC


def _log_schedule_trust_hint(channel_number, ad_time_str, timeout_sec: int):
    if not is_kids_channel(channel_number):
        return
    try:
        ad_dt = parse_ad_datetime(datetime.now(), ad_time_str)
    except Exception:
        return
    if ad_dt.minute < KIDS_PRIME_TIME_START_MINUTE:
        term_print(
            f"{current_time_str()} [편성] 키즈 {ad_time_str} — "
            f":50~:59 외 편성(신뢰도 낮음), {timeout_sec}초 내 cue/play 없으면 스킵"
        )


def wait_for_slot_ad_start(
    devices,
    channel_name,
    channel_number,
    ad_time_str,
    *,
    timeout_sec=None,
) -> bool:
    """채널 튜닝 후 timeout 내 cue/play·구글 adEvent 없으면 False."""
    timeout = (
        timeout_sec
        if timeout_sec is not None
        else _slot_ad_start_timeout_sec(channel_number, ad_time_str)
    )
    _log_schedule_trust_hint(channel_number, ad_time_str, timeout)
    lookback = (
        KIDS_SLOT_LOG_LOOKBACK_SEC
        if is_kids_channel(channel_number)
        else CHECK2_LOG_LOOKBACK_SEC
    )
    preload_ad_logcat_buffer(devices, lookback_sec=lookback, max_lines=2000)
    deadline = time.time() + timeout
    last_status = 0.0
    last_preload = 0.0
    while time.time() < deadline:
        if _slot_ad_pending_signal(devices):
            return True
        now = time.time()
        if now - last_preload >= 5:
            preload_ad_logcat_buffer(
                devices, lookback_sec=lookback, max_lines=1500
            )
            last_preload = now
        if now - last_status >= SLOT_AD_START_STATUS_INTERVAL_SEC:
            remain = max(0, int(deadline - now))
            term_print(
                f"{current_time_str()} [편성 대기] {channel_name}({channel_number}) "
                f"@ {ad_time_str} — cue/play 대기 {remain}초"
            )
            last_status = now
        time.sleep(0.5)
    return False


def skip_slot_no_ad(
    devices,
    channel_name,
    channel_number,
    ad_time_str,
    *,
    timeout_sec=None,
) -> bool:
    """False 반환 + 로그 (편성 row 제거용)."""
    timeout = (
        timeout_sec
        if timeout_sec is not None
        else _slot_ad_start_timeout_sec(channel_number, ad_time_str)
    )
    term_print(
        f"{current_time_str()} [편성 스킵] {channel_name}({channel_number}) "
        f"@ {ad_time_str} — {timeout}초 내 광고 cue/play 없음 → 다음 편성"
    )
    clear_slot_watches(devices)
    return False


def _ad_slot_busy(devices, expected_impressions=DEFAULT_EXPECTED_IMPRESSIONS) -> bool:
    """play 시작 후 impression·API 완료 전이면 True (다음 편성 전환 보류)."""
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker:
            continue
        played = tracker.phases.get("play_start") or tracker.phases.get("player_play")
        if played and not tracker.is_complete(expected_impressions):
            return True
    return False


def _defer_if_ad_slot_busy(devices, pending_desc: str) -> bool:
    global _last_ad_busy_defer_log_at
    if not _ad_slot_busy(devices):
        return False
    if time.time() - _last_ad_busy_defer_log_at >= AD_BUSY_DEFER_LOG_INTERVAL_SEC:
        _last_ad_busy_defer_log_at = time.time()
        term_print(
            f"{current_time_str()} [모니터링] 광고 재생·impression 처리 중 — "
            f"{pending_desc} 보류"
        )
    return True


def _player_play_seen(devices):
    """AnypointAdPlayerImpl.play / play start 감지."""
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and (
            tracker.phases.get("player_play") or tracker.phases.get("play_start")
        ):
            return True
    return False


def _cue_list_ready(devices):
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and tracker.cue_ready:
            return True
    return False


def _impression_counts(devices):
    counts = {}
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        counts[device] = tracker.impression_count if tracker else 0
    return counts


def _impression_increased(devices, before_counts):
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        now = tracker.impression_count if tracker else 0
        if now > before_counts.get(device, 0):
            return True
    return False


IMPRESSION_LOG_PAYLOAD_RE = re.compile(r"ImpressionLog\((.+?)\)", re.I)
IMPRESSION_LOG_SIZE_RE = re.compile(r"impression\s+log\s+size\s*=\s*(\d+)", re.I)


def _is_impression_send_preview_line(line: str) -> bool:
    """AdEventManager.sendImpressionLogs 의 --> ImpressionLog(recordId=0) — playTime 집계 제외."""
    compact = line.lower().replace(" ", "")
    if "-->impressionlog(" in compact:
        return True
    if "sendimpressionlogs" in compact and "impressionlog(" in compact:
        return True
    return False


def _is_impression_batch_playtime_line(line: str) -> bool:
    """
    impression log size=N 이후 ImpressionLogManager.send][157] 배치 상세만 집계.
    sendImpressionLogs 미리보기(recordId=0) 는 제외.
    """
    if "impressionlog(" not in line.lower().replace(" ", ""):
        return False
    if _is_impression_send_preview_line(line):
        return False
    lower = line.lower()
    if "sendimpressionlogs" in lower:
        return False
    if "impressionlogmanager.send" in lower:
        return True
    rec_m = re.search(r"recordId=(\d+)", line, re.I)
    return rec_m is not None and int(rec_m.group(1)) > 0


def parse_cue_duration_ms(line: str) -> int | None:
    """register/receive cue 로그의 Cue(... duration=N) — N은 ms."""
    if "cue(" not in line.lower().replace(" ", ""):
        return None
    m = CUE_DURATION_MS_RE.search(line)
    if not m:
        return None
    return int(m.group(1))


def _is_player_play_logcat_line(line: str) -> bool:
    """AnypointAdPlayerImpl.play — play ================================"""
    lower = line.lower()
    return "anypointadplayerimpl.play" in lower and "====" in line


def _is_player_stop_logcat_line(line: str) -> bool:
    """AnypointAdPlayerImpl.stop — stop ================================"""
    lower = line.lower()
    return (
        "anypointadplayerimpl.stop" in lower
        and "stop" in lower
        and "====" in line
    )


def parse_impression_play_time_ms(line: str) -> int | None:
    """ImpressionLog(... playTime=N) 만 집계 (playlist playTime:0 등 제외)."""
    if "impressionlog(" not in line.lower().replace(" ", ""):
        return None
    m = re.search(r"playTime=(\d+)", line, re.I)
    if not m:
        return None
    return int(m.group(1))


def impression_log_dedupe_key(line: str) -> str | None:
    """
    동일 ImpressionLog 가 live tail / logcat -d 재스캔으로 두 번 들어오는 것 방지.
    ImpressionLogManager.send 건은 recordId 우선.
    """
    if "impressionlog(" not in line.lower().replace(" ", ""):
        return None
    if not _is_impression_batch_playtime_line(line):
        return None
    rec_m = re.search(r"recordId=(\d+)", line, re.I)
    if rec_m and int(rec_m.group(1)) > 0:
        return f"recordId={rec_m.group(1)}"
    ad_m = re.search(r"adId=(-?\d+)", line, re.I)
    pt = parse_impression_play_time_ms(line)
    if ad_m and pt is not None:
        asset_m = re.search(r"assetId=(-?\d+)", line, re.I)
        device_m = re.search(r"deviceId=(-?\d+)", line, re.I)
        parts = []
        if device_m:
            parts.append(f"deviceId={device_m.group(1)}")
        parts.append(f"adId={ad_m.group(1)}")
        if asset_m:
            parts.append(f"assetId={asset_m.group(1)}")
        parts.append(f"playTime={pt}")
        return ",".join(parts)
    m = IMPRESSION_LOG_PAYLOAD_RE.search(line)
    if m:
        return f"ImpressionLog({m.group(1).strip()})"
    if pt is not None:
        return f"playTime={pt}"
    return None


def impression_log_size_batch_key(line: str) -> str | None:
    """impression log size=N, try count=M — logcat 재스캔 시 배치 카운터 중복 방지."""
    size_m = IMPRESSION_LOG_SIZE_RE.search(line)
    if not size_m:
        return None
    try_m = re.search(r"try\s+count=(\d+)", line, re.I)
    return f"size={size_m.group(1)}:try={try_m.group(1) if try_m else '0'}"


def collect_impression_play_times(
    devices, after_leave_only=False, session_merge=False
):
    """디바이스별 ImpressionLog playTime(ms) 목록 및 합계."""
    per_device = {}
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker:
            times = []
        elif session_merge:
            merged = list(tracker.slot_play_times_ms) + list(
                tracker.play_times_after_leave_ms
            )
            times = list(dict.fromkeys(merged))
        elif after_leave_only:
            times = list(tracker.play_times_after_leave_ms)
        else:
            times = list(tracker.slot_play_times_ms)
        per_device[device] = times
    total_ms = sum(sum(times) for times in per_device.values())
    return per_device, total_ms


def _format_logcat_dt(dt) -> str:
    if dt is None:
        return "(없음)"
    return f"{dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d}"


def _check4_collect_session_timing(devices):
    """디바이스별 logcat player play ==== → player stop ==== 구간(ms)."""
    per_device = {}
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker or not tracker.first_play_logcat_at:
            continue
        start_at = tracker.first_play_logcat_at
        stop_at = tracker.last_stop_logcat_at
        if stop_at is not None and stop_at < start_at:
            stop_at = None
        end_at = stop_at or start_at
        duration_ms = int((end_at - start_at).total_seconds() * 1000)
        if duration_ms < 0:
            duration_ms = 0
        per_device[device] = {
            "play_start_at": start_at,
            "play_stop_at": stop_at,
            "duration_ms": duration_ms,
        }
    return per_device


def _check4_session_duration_ms(devices) -> int:
    """logcat player play → player stop 구간(ms). playTime 합과 비교."""
    best = 0
    for info in _check4_collect_session_timing(devices).values():
        if info["duration_ms"] > best:
            best = info["duration_ms"]
    return best


def _check4_playtime_compare(session_ms: int, total_ms: int):
    """stop−start(ms) vs ImpressionLog playTime 합 — 차이·허용 여부."""
    delta_ms = total_ms - session_ms
    abs_delta_ms = abs(delta_ms)
    return {
        "session_ms": session_ms,
        "playtime_sum_ms": total_ms,
        "delta_ms": delta_ms,
        "abs_delta_ms": abs_delta_ms,
        "tolerance_ms": PLAYTIME_MATCH_TOLERANCE_MS,
        "match": abs_delta_ms <= PLAYTIME_MATCH_TOLERANCE_MS,
    }


def _print_check4_playtime_comparison(devices, result):
    """체크 4 [B]: logcat player play→stop vs ImpressionLog playTime 합."""
    timing = result.get("per_device_timing") or _check4_collect_session_timing(
        devices
    )
    session_ms = int(result.get("session_playtime_ms") or 0)
    total_ms = int(result.get("total_play_time_ms") or 0)
    cmp_info = result.get("compare") or _check4_playtime_compare(session_ms, total_ms)

    term_print(
        f"{current_time_str()} [B] logcat player play ==== → stop ==== vs playTime 합"
    )
    for device, info in timing.items():
        dur = info.get("duration_ms", 0)
        term_print(
            f"  [{device}] play {_format_logcat_dt(info.get('play_start_at'))}"
            f" → stop {_format_logcat_dt(info.get('play_stop_at'))}"
            f"  (시간차 {dur}ms / {dur / 1000:.1f}초)"
        )
    if not timing:
        term_print("  (logcat player play/stop 시각 미확인)")

    term_print(f"{current_time_str()} [B] ImpressionLog playTime (이탈 후 수신)")
    play_times = result.get("play_times_ms") or []
    if play_times:
        for i, pt in enumerate(play_times, 1):
            term_print(f"  #{i}: {pt}ms ({pt / 1000:.1f}초)")
    else:
        term_print("  (없음)")
    term_print(
        f"  playTime 합계: {total_ms}ms ({total_ms / 1000:.1f}초)"
    )

    delta_ms = cmp_info["delta_ms"]
    delta_sign = "+" if delta_ms >= 0 else ""
    term_print(
        f"{current_time_str()} [B] 비교: stop−start {session_ms}ms ({session_ms / 1000:.1f}초)"
        f" vs playTime합 {total_ms}ms ({total_ms / 1000:.1f}초)"
    )
    term_print(
        f"  차이(playTime합 − stop−start): {delta_sign}{delta_ms}ms "
        f"({delta_sign}{delta_ms / 1000:.1f}초), "
        f"허용 ±{cmp_info['tolerance_ms'] / 1000:.0f}초 "
        f"→ {'일치 ✓' if cmp_info['match'] else '불일치 ✗'}"
    )


def _begin_leave_impression_window(devices):
    """체크 4 이탈 직후부터의 playTime 만 따로 수집."""
    with _ad_tracker_lock:
        for device in devices:
            tracker = _active_ad_trackers.get(device)
            if tracker:
                tracker.play_times_after_leave_ms = []
                tracker.collecting_after_leave = True
                tracker.impression_batch_remaining = 0


def _check2_expected_playtime_ms(devices) -> int:
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker:
            return tracker.expected_playtime_ms()
    return EXPECTED_AD_PLAYTIME_MS


def evaluate_internal_ad_playback(
    devices,
    expected_impressions=DEFAULT_EXPECTED_IMPRESSIONS,
    *,
    update_checklist=True,
):
    """
    체크 2: 재생 완료 + ImpressionLog playTime 합 ∈ [cue−2초, cue] + impression API 200.
    """
    per_device = {}
    all_ok = True
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker:
            per_device[device] = {"ok": False, "reason": "tracker 없음"}
            all_ok = False
            continue
        flow_ok = tracker.is_complete(expected_impressions)
        total_ms = tracker.evaluation_playtime_ms()
        expected_ms = tracker.expected_playtime_ms()
        playtime_ok = _check2_playtime_ok(total_ms, expected_ms)
        device_ok = flow_ok and playtime_ok
        per_device[device] = {
            "ok": device_ok,
            "flow_ok": flow_ok,
            "playtime_ok": playtime_ok,
            "api_200": tracker.impression_api_ok,
            "playtime_sum_sec": round(total_ms / 1000, 1),
            "expected_playtime_sec": round(expected_ms / 1000, 1),
            "cue_duration_ms": tracker.cue_duration_ms,
            "impression_count": tracker.impression_count,
        }
        if not device_ok:
            all_ok = False

    result = {
        "done": all_ok,
        "ok": all_ok,
        "per_device": per_device,
        "expected_playtime_sec": _check2_expected_playtime_ms(devices) / 1000,
    }
    if update_checklist:
        _run_checklist["ad_playback"] = result
    return result


def _log_schedule_ad_monitor_result(devices, eval_result, channel_name):
    term_print(
        f"{current_time_str()} [모니터링] === {channel_name} 광고 검증 ==="
    )
    for device in devices:
        info = (eval_result.get("per_device") or {}).get(device) or {}
        if info.get("reason"):
            term_print(f"  [{device}] ✗ {info['reason']}")
            continue
        total = info.get("playtime_sum_sec", 0)
        exp = info.get("expected_playtime_sec", 120)
        floor = max(0, exp - CHECK2_PLAYTIME_UNDER_CUE_MS / 1000)
        flow = "flow OK" if info.get("flow_ok") else "flow FAIL"
        if info.get("playtime_ok"):
            pt = f"playTime {total}s ∈ [{floor:.0f},{exp:.0f}]"
        else:
            pt = f"playTime {total}s FAIL (기대 {floor:.0f}~{exp:.0f}s)"
        api = "impression API OK" if info.get("api_200") else "impression API FAIL"
        mark = "✓" if info.get("ok") else "✗"
        term_print(f"  [{device}] {mark} {flow}, {pt}, {api}")


def _log_monitor_google_result(devices):
    tracker = _google_tracker_for(devices[0]) if devices else None
    if not tracker or not tracker.has_started():
        term_print(f"{current_time_str()} [모니터링/구글] 구글 광고 없음")
        return
    names = sorted(tracker.event_names())
    term_print(
        f"{current_time_str()} [모니터링/구글] adEvent: {', '.join(names)}"
    )
    term_print(f"  tracking beacon {len(tracker.tracking_events)}건")
    full = tracker.evaluate_full_play()
    if full.get("ok"):
        term_print("  ✓ Quartile+COMPLETE")
    else:
        missing = full.get("missing_quartile") or []
        term_print(
            f"  · Quartile/COMPLETE 미충족 "
            f"(누락: {', '.join(missing) or 'COMPLETE'})"
        )
    if "SKIPPABLE_STATE_CHANGED" in names:
        skip = tracker.evaluate_skip_ok()
        term_print(
            f"  {'✓' if skip.get('ok') else '✗'} SKIPPABLE→SKIPPED"
        )


def _log_monitor_kids_result(devices, channel_number, channel_name, ui_entry):
    ch = normalize_channel_number(channel_number)
    term_print(
        f"{current_time_str()} [모니터링/키즈] === {channel_name}({ch}) ==="
    )
    for device in devices:
        with _kids_watermark_lock:
            tracker = _active_kids_watermark_trackers.get(device)
        if not tracker:
            term_print(f"  [{device}] ✗ 워터마크 추적 없음")
            continue
        ok = tracker.seen
        term_print(
            f"  [{device}] {'✓' if ok else '✗'} logcat 워터마크 "
            f"({', '.join(tracker.missing_summary()) or 'OK'})"
        )
    if ui_entry is not None:
        ui_ok = bool(ui_entry.get("ok"))
        term_print(
            f"  광고방송 OCR: {'✓' if ui_ok else '✗'} "
            f"({ui_entry.get('message') or ''})"
        )


def _google_monitor_play_done(devices) -> bool:
    tracker = _google_tracker_for(devices[0]) if devices else None
    if not tracker or not tracker.has_started():
        return True
    names = tracker.event_names()
    return bool(
        names & {"COMPLETED", "ALL_ADS_COMPLETED", "SKIPPED"}
    )


def _kids_watermark_all_seen(devices) -> bool:
    for device in devices:
        with _kids_watermark_lock:
            tracker = _active_kids_watermark_trackers.get(device)
        if not tracker or not tracker.seen:
            return False
    return True


def wait_for_slot_monitor(
    devices,
    channel_name,
    channel_number,
    ad_time_str,
    *,
    monitor_google=False,
    monitor_kids=False,
):
    """체크리스트 완료 후 슬롯 — 내부·구글·키즈 통합 대기·검증."""
    timeout_sec = AD_PLAYBACK_WAIT_TIMEOUT_SEC
    deadline = time.time() + timeout_sec
    if monitor_kids and ad_time_str:
        now = datetime.now()
        ad_dt = parse_ad_datetime(now, ad_time_str)
        deadline = max(
            deadline, (ad_dt + timedelta(seconds=140)).timestamp()
        )
    last_status = time.time()
    last_kids_preload = 0.0
    google_skip_sent = False
    kids_ui_entry = None
    kids_ui_started = False

    parts = ["내부 재생·impression·playTime"]
    if monitor_google:
        parts.append("구글 quartile·tracking·skip")
    if monitor_kids:
        parts.append("키즈 워터마크·OCR")
    term_print(
        f"{current_time_str()} [모니터링] 대기 (최대 {timeout_sec}초) — "
        f"{', '.join(parts)}"
    )

    while time.time() < deadline:
        if (
            monitor_google
            and MONITOR_GOOGLE_AUTO_SKIP
            and not has_pending_checklist_work(channel_number)
            and not google_skip_sent
            and _google_any_event(devices, "SKIPPABLE_STATE_CHANGED")
        ):
            term_print(
                f"{current_time_str()} [모니터링/구글] SKIPPABLE — "
                f"skip(key {GOOGLE_SKIP_OK_KEYEVENT}) 입력"
            )
            _google_send_keyevent(devices, GOOGLE_SKIP_OK_KEYEVENT)
            google_skip_sent = True

        if monitor_kids and not kids_ui_started and _ad_playback_started(devices):
            kids_ui_started = True
            term_print(
                f"{current_time_str()} [모니터링/키즈] play 확인 — OCR 시작"
            )
            kids_ui_entry = verify_ad_broadcast_ui_burst(
                channel_name, channel_number, devices=devices
            )

        if monitor_kids and time.time() - last_kids_preload >= 4:
            preload_kids_watermark_buffer(devices)
            preload_ad_logcat_buffer(
                devices, lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC, max_lines=2000
            )
            last_kids_preload = time.time()

        ad_done = True
        for device in devices:
            with _ad_tracker_lock:
                tracker = _active_ad_trackers.get(device)
            if not tracker or not tracker.is_complete():
                ad_done = False
                break

        google_done = not monitor_google or _google_monitor_play_done(devices)
        kids_done = not monitor_kids or _kids_watermark_all_seen(devices)

        if ad_done and google_done and kids_done:
            term_print(f"{current_time_str()} [모니터링] 슬롯 확인 완료")
            break

        if time.time() - last_status >= AD_PLAYBACK_STATUS_INTERVAL_SEC:
            notes = []
            if not ad_done:
                notes.append("내부 광고")
            if monitor_google and not google_done:
                notes.append("구글")
            if monitor_kids and not kids_done:
                notes.append("키즈 워터마크")
            if notes:
                term_print(
                    f"{current_time_str()} [모니터링] 대기 중… "
                    f"{', '.join(notes)}"
                )
            last_status = time.time()

        time.sleep(1)
    else:
        term_print(f"{current_time_str()} [모니터링] 슬롯 대기 타임아웃")

    _finish_ad_playback_wait(devices, DEFAULT_EXPECTED_IMPRESSIONS, monitor_only=True)
    if monitor_google:
        _log_monitor_google_result(devices)
    if monitor_kids:
        _log_monitor_kids_result(
            devices, channel_number, channel_name, kids_ui_entry
        )


def run_schedule_slot_monitor(devices, channel_name, channel_number, ad_time_str):
    """체크리스트 완료 후 편성 슬롯 — 내부·구글·키즈 통합 모니터링."""
    expected_ids = _log_expected_catalog_ids(channel_name, channel_number)
    is_kids = is_kids_channel(channel_number)
    monitor_google = not _run_checklist.get("google_skipped")
    log_lookback = KIDS_SLOT_LOG_LOOKBACK_SEC if is_kids else CHECK2_LOG_LOOKBACK_SEC

    start_ad_playback_watch(
        devices,
        channel_name,
        channel_number,
        ad_time_str,
        expected_catalog_ids=expected_ids,
        log_lookback_sec=log_lookback,
        preload_buffer=False,
    )
    if monitor_google:
        start_google_ad_watch(devices, sub=GOOGLE_MONITOR_SUB)
    if is_kids:
        start_kids_watermark_watch(
            devices,
            channel_number,
            channel_name,
            expected_catalog_ids=expected_ids,
            monitor_only=True,
        )

    if not switch_channel_with_verify(
        channel_number,
        devices,
        clear_buffer=False,
        channel_name=channel_name,
        expected_catalog_ids=expected_ids,
    ):
        clear_slot_watches(devices)
        reason = (
            "유료가입 화면"
            if is_purchase_screen_blocking(devices)
            else "튜닝 실패"
        )
        term_print(
            f"{current_time_str()} [모니터링] {reason} — "
            f"{channel_name}({channel_number})"
        )
        return False

    preload_ad_logcat_buffer(devices, lookback_sec=log_lookback, max_lines=2000)
    if monitor_google:
        term_print(
            f"{current_time_str()} [모니터링/구글] quartile·tracking logcat 출력"
        )
    if is_kids:
        preload_kids_watermark_buffer(devices)
        term_print(
            f"{current_time_str()} [모니터링/키즈] 키즈 편성 — "
            f"워터마크 logcat 기본 확인"
        )

    term_print(
        f"{current_time_str()} [모니터링] {channel_name}({channel_number}) "
        f"@ {ad_time_str}"
    )
    if not wait_for_slot_ad_start(
        devices, channel_name, channel_number, ad_time_str
    ):
        skip_slot_no_ad(devices, channel_name, channel_number, ad_time_str)
        return False
    try:
        wait_for_slot_monitor(
            devices,
            channel_name,
            channel_number,
            ad_time_str,
            monitor_google=monitor_google,
            monitor_kids=is_kids,
        )
    finally:
        if monitor_google:
            clear_google_ad_watch(devices)
    return True


def finalize_kids_watermark_check(
    devices,
    channel_number,
    channel_name,
    *,
    log_ok=None,
    ui_ok=False,
):
    """체크 6 PASS: kid 워터마크 logcat + 광고 재생 중 ADB '광고 방송' OCR."""
    ch = normalize_channel_number(channel_number)
    if log_ok is None:
        log_ok = kids_watermark_done_for_all_devices(devices, channel_number)
    ui_map = _run_checklist.setdefault("kids_watermark_ui", {})
    ui_map[ch] = bool(ui_ok)
    overall_ok = bool(log_ok and ui_ok)
    check6 = {
        "done": True,
        "ok": overall_ok,
        "log_ok": bool(log_ok),
        "ui_ok": bool(ui_ok),
        "channel": ch,
    }
    _run_checklist["kids_check6"] = check6
    return check6


def run_kids_check6_ui_verification(devices, channel_name, channel_number):
    """광고 재생(play) 중 ADB 캡처·OCR로 우측 상단 '광고 방송' 확인."""
    entry = verify_ad_broadcast_ui_burst(
        channel_name or "", channel_number, devices=devices
    )
    return bool(entry and entry.get("ok") and entry.get("badge_visible"))


def _register_cue_ready(devices):
    """현재 편성 채널과 일치하는 register cue 만 인정."""
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and tracker.register_cue_seen:
            return True
    return False


def _check5_ad_list_ready_tracker(tracker) -> bool:
    """play 전: register cue 또는 (편성 cue + load/onPrepare). (참고 로그용)"""
    if tracker.phases.get("player_play") or tracker.phases.get("play_start"):
        return False
    if tracker.register_cue_seen:
        return True
    return bool(tracker.cue_ready and tracker.phases.get("load"))


def _check5_tracker_list_ready(devices) -> bool:
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and _check5_ad_list_ready_tracker(tracker):
            return True
    return False


def _check5_register_deadline_epoch(ad_time_str: str) -> float:
    """register cue·이탈 시각을 정하지 못할 때 포기 시각 (편성 +N초)."""
    now = datetime.now()
    ad_dt = parse_ad_datetime(now, ad_time_str)
    return (ad_dt + timedelta(seconds=CHECK5_REGISTER_TIMEOUT_SEC)).timestamp()


def _check5_compute_leave_epoch(devices) -> float | None:
    """register cue +N초 와 예약 재생 N초 전 중 더 이른 시각."""
    leave_candidates = []
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker:
            continue
        if tracker.register_cue_at is not None:
            leave_candidates.append(
                tracker.register_cue_at + CHECK5_LEAVE_AFTER_REGISTER_SEC
            )
        if tracker.scheduled_play_at is not None:
            leave_candidates.append(
                tracker.scheduled_play_at - CHECK5_LEAVE_BEFORE_PLAY_SEC
            )
    if not leave_candidates:
        return None
    return min(leave_candidates)


def _log_expected_catalog_ids(channel_name, channel_number):
    """편성 ↔ 카탈로그 id 매칭 안내."""
    expected = resolve_expected_catalog_ids(channel_name)
    if expected:
        refs = ", ".join(format_channel_ref(cid) for cid in sorted(expected))
        term_print(
            f"{current_time_str()} [편성] STB ch {channel_number} {channel_name} "
            f"→ 기대 cue id: {refs}"
        )
    else:
        term_print(
            f"{current_time_str()} [편성] STB ch {channel_number} {channel_name} "
            f"→ 카탈로그 title 매칭 없음 (다른 채널 register cue 는 무시, "
            f"lgu_channel_catalog.json 보강 필요)"
        )
    return expected


def get_escape_channel(ad_channel_number, data, final_channels, devices):
    """이탈용 채널 — STB_ESCAPE_CHANNEL( final_channels ) 최우선, 편성표는 보조."""
    ad_ch = normalize_channel_number(ad_channel_number)
    env_esc = _escape_channel_from_env()
    for device in devices:
        fc = final_channels.get(device)
        if fc:
            esc = normalize_channel_number(fc)
            if esc and esc != ad_ch:
                return esc
            if esc == ad_ch:
                break
    if env_esc and env_esc != ad_ch:
        return env_esc
    for row in data:
        ch = normalize_channel_number(row.get("채널번호"))
        if ch and ch != ad_ch:
            return ch
    return env_esc if env_esc and env_esc != ad_ch else None


def wait_until_player_play(devices, timeout_sec=CHECK4_PLAY_WAIT_TIMEOUT_SEC):
    preload_ad_logcat_buffer(
        devices, lookback_sec=CHECK5_LOG_LOOKBACK_SEC, max_lines=2000
    )
    deadline = time.time() + timeout_sec
    last_preload = 0.0
    while time.time() < deadline:
        if _player_play_seen(devices):
            return True, time.time()
        if time.time() - last_preload >= 5:
            preload_ad_logcat_buffer(
                devices, lookback_sec=CHECK5_LOG_LOOKBACK_SEC, max_lines=1500
            )
            last_preload = time.time()
        time.sleep(1)
    return False, None


def _set_google_tune_targets(devices, channel_number):
    target = normalize_channel_number(channel_number)
    if not target:
        return
    target = str(target)
    with _google_tracker_lock:
        for device in devices:
            _active_google_tune_targets[device] = target


def _google_tune_target(device):
    with _google_tracker_lock:
        return _active_google_tune_targets.get(device)


def start_google_ad_watch(devices, sub=None, channel_number=None):
    global _active_google_subtest
    _active_google_subtest = sub
    with _google_tracker_lock:
        for device in devices:
            _active_google_trackers[device] = GoogleAdEventTracker()
            if channel_number is not None:
                target = normalize_channel_number(channel_number)
                if target:
                    _active_google_tune_targets[device] = str(target)


def clear_google_ad_watch(devices):
    global _active_google_subtest
    with _google_tracker_lock:
        for device in devices:
            _active_google_trackers.pop(device, None)
            _active_google_tune_targets.pop(device, None)
    _active_google_subtest = None


def _google_tracker_for(device):
    with _google_tracker_lock:
        return _active_google_trackers.get(device)


def _google_any_event(devices, event_name: str) -> bool:
    event_name = event_name.upper()
    for device in devices:
        tracker = _google_tracker_for(device)
        if tracker and event_name in tracker.event_names():
            return True
    return False


def _google_send_keyevent(devices, keycode: int):
    for device in devices:
        subprocess.run(
            ["adb", "-s", device, "shell", "input", "keyevent", str(keycode)],
            capture_output=True,
        )


def _wait_google_event(devices, event_name: str, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _google_any_event(devices, event_name):
            return True
        time.sleep(0.3)
    return False


def execute_test_google_ad(devices, row, escape_channel):
    """체크 3-A/B/C: 구글 광고 (편성 1줄당 1 시나리오)."""
    sub = _next_google_sub_test()
    if not sub:
        return {"ok": False, "done": True, "message": "구글 체크 완료 또는 시도 소진"}

    channel_name = row["채널명"]
    channel_number = row["채널번호"]
    label = GOOGLE_CHECK3_LABELS[sub]
    check_tag = _google_check_tag(sub)
    attempt = _bump_check_attempt(_google_sub_attempt_key(sub))
    result = {
        "ok": False,
        "done": False,
        "sub": sub,
        "channel_name": channel_name,
        "channel_number": normalize_channel_number(channel_number),
        "message": "",
        "events": [],
    }

    term_print(f"\n{'=' * 60}")
    term_print(
        f"{current_time_str()} [{check_tag}] {label} "
        f"(시도 {attempt}/{CHECKLIST_CHECK_MAX_ATTEMPTS})"
    )
    term_print(
        f"  채널 {channel_name}({result['channel_number']})"
        + (f" → 이탈 ch {escape_channel}" if sub == "leave_during" and escape_channel else "")
    )
    term_print(f"{'=' * 60}")

    start_google_ad_watch(devices, sub=sub, channel_number=channel_number)
    if not switch_channel_with_verify(channel_number, devices, channel_name=channel_name):
        result["message"] = (
            "유료가입 화면" if is_purchase_screen_blocking(devices) else "채널 튜닝 실패"
        )
        # 채널 자체가 성립하지 않은 경우는 구글 3-A/B/C 검증 시도로 보지 않음.
        _unbump_check_attempt(_google_sub_attempt_key(sub))
        clear_google_ad_watch(devices)
        term_print(f"  ✗ {result['message']}")
        return result

    if not _wait_google_event(
        devices, "STARTED", min(SLOT_AD_START_TIMEOUT_SEC, GOOGLE_AD_START_TIMEOUT_SEC)
    ):
        result["message"] = (
            f"google adEvent STARTED 미감지 "
            f"({min(SLOT_AD_START_TIMEOUT_SEC, GOOGLE_AD_START_TIMEOUT_SEC)}초)"
        )
        # 구글 광고가 시작되지 않은 편성은 테스트 미성립 — 다음 구글 편성에서 재시도.
        _unbump_check_attempt(_google_sub_attempt_key(sub))
        clear_google_ad_watch(devices)
        term_print(f"  ✗ {result['message']}")
        return result

    term_print(f"{current_time_str()} [{check_tag}] google ad STARTED 감지")
    term_print(
        f"{current_time_str()} [{check_tag}] google Quartile·tracking logcat 실시간 출력 시작"
    )

    if sub == "full_play":
        # SKIPPABLE→SKIPPED 첫 소재의 ALL_ADS_COMPLETED 만으로 종료하지 않음.
        # 편성 내 다음 구글 소재의 Quartile+COMPLETED 까지 GOOGLE_AD_PLAY_TIMEOUT_SEC 대기.
        deadline = time.time() + GOOGLE_AD_PLAY_TIMEOUT_SEC
        eval_res = {"ok": False, "missing_quartile": ["STARTED"], "events": []}
        while time.time() < deadline:
            tracker = _google_tracker_for(devices[0]) if devices else None
            if tracker:
                eval_res = tracker.evaluate_full_play()
                if eval_res.get("ok"):
                    break
            time.sleep(0.5)
        tracker = _google_tracker_for(devices[0]) if devices else None
        if tracker:
            eval_res = tracker.evaluate_full_play()
        result["ok"] = bool(eval_res.get("ok"))
        result["events"] = eval_res.get("events", [])
        if result["ok"]:
            result["message"] = "Quartile + COMPLETED 정상"
        else:
            missing = eval_res.get("missing_quartile") or []
            result["message"] = (
                f"Quartile/COMPLETE 미충족 (누락: {', '.join(missing) or 'COMPLETE'})"
            )

    elif sub == "leave_during":
        if not _wait_google_event(devices, "FIRST_QUARTILE", 60):
            time.sleep(GOOGLE_LEAVE_MIN_AFTER_START_SEC)
        if escape_channel:
            term_print(
                f"{current_time_str()} [{check_tag}] 재생 중 ch {escape_channel} 이탈"
            )
            for device in devices:
                tracker = _google_tracker_for(device)
                if tracker:
                    tracker.mark_channel_leave()
            _set_google_tune_targets(devices, escape_channel)
            switch_channel_with_verify(escape_channel, devices, clear_buffer=False)
        else:
            result["message"] = "이탈 채널 없음"
            _run_checklist.setdefault("google_ad", {})[sub] = result
            clear_google_ad_watch(devices)
            return result
        time.sleep(GOOGLE_POST_LEAVE_OBSERVE_SEC)
        tracker = _google_tracker_for(devices[0]) if devices else None
        eval_res = tracker.evaluate_leave_during() if tracker else {"ok": False}
        result["ok"] = bool(eval_res.get("ok"))
        result["events"] = eval_res.get("events", [])
        if result["ok"]:
            result["message"] = (
                eval_res.get("message") or "이탈 후 tracking 중단 확인"
            )
        else:
            result["message"] = (
                eval_res.get("message")
                or f"이탈 후 google adEvent {eval_res.get('ad_events_after_leave', '?')}건"
            )
        if tracker:
            diag = tracker.leave_diagnostics()
            term_print(
                f"{current_time_str()} [{check_tag}] 이탈 후 타임라인 "
                f"(stopTracking {'✓' if eval_res.get('stop_tracking') else '✗'}, "
                f"adEvent {eval_res.get('ad_events_after_leave', '?')}건, "
                f"tracking {eval_res.get('tracking_after_leave', '?')}건)"
            )
            for diag_line in diag["lines"]:
                term_print(f"    · {diag_line}")

    elif sub == "skip_ok":
        if not _wait_google_event(devices, "SKIPPABLE_STATE_CHANGED", GOOGLE_AD_PLAY_TIMEOUT_SEC):
            result["message"] = "SKIPPABLE_STATE_CHANGED 미감지"
        else:
            term_print(
                f"{current_time_str()} [{check_tag}] 스킵 가능 — "
                f"OK(key {GOOGLE_SKIP_OK_KEYEVENT}) 입력"
            )
            _google_send_keyevent(devices, GOOGLE_SKIP_OK_KEYEVENT)
            _wait_google_event(devices, "SKIPPED", 45)
            tracker = _google_tracker_for(devices[0]) if devices else None
            eval_res = tracker.evaluate_skip_ok() if tracker else {"ok": False}
            result["ok"] = bool(eval_res.get("ok"))
            result["events"] = eval_res.get("events", [])
            result["message"] = (
                "SKIPPABLE → SKIPPED 확인"
                if result["ok"]
                else "SKIPPED 미감지"
            )

    result["done"] = True
    _run_checklist.setdefault("google_ad", {})[sub] = result
    clear_google_ad_watch(devices)
    mark = "✓" if result["ok"] else "✗"
    term_print(f"{current_time_str()} [{check_tag}] {mark} {result['message']}")
    if result.get("events"):
        term_print(f"  events: {', '.join(result['events'][:12])}")
    term_print(f"{'=' * 60}\n")
    print_checklist_progress(devices)
    return result


def execute_test_leave_before_play(devices, row, escape_channel):
    """
    체크리스트 5: 광고 채널 선전환 → register cue 후 이탈(play 전) → play/impression 없으면 성공.

    이탈 시각 = min(register + CHECK5_LEAVE_AFTER_REGISTER_SEC,
                    scheduled_play - CHECK5_LEAVE_BEFORE_PLAY_SEC)
    """
    channel_name = row["채널명"]
    channel_number = row["채널번호"]
    ad_time_str = row["광고편성 시간"]
    leave_label = (
        f"register cue +{CHECK5_LEAVE_AFTER_REGISTER_SEC}초 "
        f"(play {CHECK5_LEAVE_BEFORE_PLAY_SEC:.0f}초 전 상한)"
    )
    result = {
        "ok": False,
        "channel_name": channel_name,
        "channel_number": normalize_channel_number(channel_number),
        "escape_channel": escape_channel,
        "saw_register_cue": False,
        "saw_play": False,
        "saw_impression": False,
        "done": False,
        "message": "",
        "leave_at_label": leave_label,
    }

    term_print(f"\n{'=' * 60}")
    term_print(f"{current_time_str()} [체크 5] 목록 후·play 전 채널 이탈 (광고 미재생)")
    term_print(
        f"  광고 채널 {channel_name}({result['channel_number']}) "
        f"→ {leave_label} 에 ch {escape_channel} 이동"
    )
    term_print(f"{'=' * 60}")

    if not escape_channel:
        result["message"] = "이탈용 다른 채널 번호를 찾지 못함"
        _run_checklist["leave_before_play"] = result
        term_print(f"  ✗ {result['message']}")
        return result

    term_print(f"{current_time_str()} [체크 5] ch {result['channel_number']} 로 전환 중…")
    expected_ids = _log_expected_catalog_ids(channel_name, channel_number)
    leave_at = None
    register_deadline = _check5_register_deadline_epoch(ad_time_str)
    leave_announced = False

    start_ad_playback_watch(
        devices,
        channel_name,
        channel_number,
        ad_time_str,
        announce=False,
        preload_buffer=False,
        expected_catalog_ids=expected_ids,
        log_lookback_sec=CHECK5_LOG_LOOKBACK_SEC,
    )
    # 튜닝 확정(catalog cue 대기)은 register→play 창(≈8초)보다 오래 걸려 이탈 타이밍을
    # 놓친다. 검증 없이 전환한 뒤 곧바로 이탈 루프로 진입하고, 기대 채널 id 의 register cue
    # 가 잡히면 그 자체를 튜닝 확정으로 본다(_check5_compute_leave_epoch).
    switch_channel_via_adb(channel_number, devices, clear_buffer=False)
    time.sleep(CHANNEL_SWITCH_SETTLE_SEC)
    if is_purchase_screen_blocking(devices):
        clear_slot_watches(devices)
        result["message"] = "유료가입 화면 — 가입 채널, 다음 편성에서 재시도"
        _run_checklist["leave_before_play"] = result
        term_print(f"  ✗ {result['message']}")
        return result
    preload_ad_logcat_buffer(devices, lookback_sec=CHECK5_LOG_LOOKBACK_SEC)

    left_channel = False
    last_status = time.time()
    last_preload = time.time()
    while time.time() < register_deadline + 5:
        if time.time() - last_preload >= 1:
            preload_ad_logcat_buffer(
                devices, lookback_sec=CHECK5_LOG_LOOKBACK_SEC, max_lines=1500
            )
            last_preload = time.time()
        if _check5_tracker_list_ready(devices):
            result["saw_register_cue"] = True
        computed_leave = _check5_compute_leave_epoch(devices)
        if computed_leave is not None:
            if leave_at is None or computed_leave < leave_at:
                leave_at = computed_leave
                if not leave_announced:
                    wait_sec = max(0.0, leave_at - time.time())
                    term_print(
                        f"{current_time_str()} [체크 5] 이탈 예정 "
                        f"{datetime.fromtimestamp(leave_at).strftime('%H:%M:%S')} "
                        f"({leave_label}, {wait_sec:.0f}초 후)"
                    )
                    leave_announced = True
        if _player_play_seen(devices):
            if not result["saw_play"]:
                result["saw_play"] = True
                term_print(f"{current_time_str()} [체크 5] ✗ play ==== 감지 (이탈 전)")
        if leave_at is not None and time.time() >= leave_at:
            term_print(
                f"{current_time_str()} [체크 5] {leave_label} → ch {escape_channel} 이동"
            )
            switch_channel_via_adb(escape_channel, devices, clear_buffer=False)
            time.sleep(CHANNEL_SWITCH_SETTLE_SEC)
            left_channel = True
            break
        if time.time() - last_status >= VERSION_STATUS_INTERVAL_SEC:
            if leave_at is not None:
                remain = max(0, leave_at - time.time())
                term_print(
                    f"{current_time_str()} [체크 5] 이탈까지 {remain:.0f}초…"
                )
            else:
                remain = max(0, register_deadline - time.time())
                term_print(
                    f"{current_time_str()} [체크 5] register cue 대기… "
                    f"(최대 {remain:.0f}초)"
                )
            last_status = time.time()
        time.sleep(CHECK5_LEAVE_POLL_SEC)

    if left_channel:
        impression_at_leave = _impression_counts(devices)
        observe_sec = (
            CHECK5_POST_LEAVE_OBSERVE_SEC
            if not result["saw_play"]
            else CHANNEL_LEAVE_OBSERVE_SEC
        )
        term_print(
            f"{current_time_str()} [체크 5] 이탈 후 play/impression 확인 "
            f"({observe_sec}초)…"
        )
        observe_until = time.time() + observe_sec
        while time.time() < observe_until:
            if _player_play_seen(devices):
                result["saw_play"] = True
            if _impression_increased(devices, impression_at_leave):
                result["saw_impression"] = True
                break
            time.sleep(1)
        _, total_ms = collect_impression_play_times(devices)
        if total_ms > 0:
            result["saw_impression"] = True
        result["done"] = True
        result["ok"] = not result["saw_play"] and not result["saw_impression"]
        if result["ok"]:
            result["message"] = (
                f"{leave_label} 이탈, play/impression 없음 (기대 동작)"
            )
        elif result["saw_play"]:
            result["message"] = "play ==== 감지 (미재생 기대)"
        elif result["saw_impression"]:
            result["message"] = "impression log 발생 (미재생 기대)"
    elif not result["message"]:
        if not result["saw_register_cue"]:
            result["message"] = (
                f"register cue 미감지 (편성+{CHECK5_REGISTER_TIMEOUT_SEC}초 내)"
            )
        else:
            result["message"] = f"{leave_label} 이탈 채널 전환 실패"

    _run_checklist["leave_before_play"] = result
    mark = "✓" if result["ok"] else "✗"
    term_print(f"{current_time_str()} [체크 5] {mark} {result['message']}")
    if result["saw_register_cue"]:
        term_print(f"  (참고: 편성 일치 register/load 로그 확인됨)")
    term_print(f"{'=' * 60}\n")
    print_checklist_progress(devices)
    clear_slot_watches(devices)
    return result


def _check4_try_evaluate(devices, result, impression_before):
    """
    ImpressionLog playTime 합 + logcat player play/stop 시간차 확보 시 PASS/FAIL.
    반환: 'pending' | 'done'
    """
    per_device_times, total_ms = collect_impression_play_times(
        devices, after_leave_only=True
    )
    if total_ms <= 0 and not _impression_increased(devices, impression_before):
        return "pending"
    if total_ms <= 0:
        return "pending"

    session_ms = _check4_session_duration_ms(devices)
    if session_ms <= 0:
        _backfill_player_stop_from_logcat(devices)
        session_ms = _check4_session_duration_ms(devices)
    if session_ms <= 0:
        return "pending"

    result["saw_impression"] = True
    all_times = []
    for device, times in per_device_times.items():
        all_times.extend(times)
    result["play_times_ms"] = all_times
    result["total_play_time_ms"] = total_ms
    result["total_play_time_sec"] = round(total_ms / 1000, 1)
    result["per_device_timing"] = _check4_collect_session_timing(devices)
    result["session_playtime_ms"] = session_ms
    result["expected_playtime_ms"] = session_ms
    cmp_info = _check4_playtime_compare(session_ms, total_ms)
    result["compare"] = cmp_info
    result["playtime_delta_ms"] = cmp_info["delta_ms"]
    result["playtime_abs_delta_ms"] = cmp_info["abs_delta_ms"]
    result["playtime_match"] = cmp_info["match"]
    result["done"] = True
    result["ok"] = bool(
        result["saw_play"]
        and result["left_channel"]
        and result["playtime_match"]
    )
    delta_sec = cmp_info["delta_ms"] / 1000
    delta_sign = "+" if delta_sec >= 0 else ""
    if result["ok"]:
        result["message"] = (
            f"player play→stop {session_ms / 1000:.1f}초 ≈ playTime합 "
            f"{result['total_play_time_sec']}초 "
            f"(차이 {delta_sign}{delta_sec:.1f}초), tracking OK"
        )
    else:
        result["message"] = (
            f"player play→stop {session_ms / 1000:.1f}초 vs playTime합 "
            f"{result['total_play_time_sec']}초 "
            f"(차이 {delta_sign}{delta_sec:.1f}초, 허용 ±"
            f"{PLAYTIME_MATCH_TOLERANCE_MS / 1000:.0f}초) 불일치"
        )
    return "done"


def execute_test_leave_during_ad(devices, row, escape_channel):
    """
    체크리스트 4: 편성 종료 전 채널 이탈.

    - 이탈 타이밍: play ==== 감지 후 CHANNEL_LEAVE_DURING_AD_WAIT_SEC (벽시계).
    - 사후 검증: logcat player play→stop 시간차 vs ImpressionLog playTime합.
    """
    channel_name = row["채널명"]
    channel_number = row["채널번호"]
    ad_time_str = row["광고편성 시간"]
    result = {
        "ok": False,
        "channel_name": channel_name,
        "channel_number": normalize_channel_number(channel_number),
        "escape_channel": escape_channel,
        "saw_play": False,
        "left_channel": False,
        "saw_impression": False,
        "play_times_ms": [],
        "total_play_time_ms": 0,
        "total_play_time_sec": 0.0,
        "expected_playtime_ms": 0,
        "playtime_match": False,
        "done": False,
        "message": "",
    }

    term_print(f"\n{'=' * 60}")
    term_print(f"{current_time_str()} [체크 4] 편성 종료 전 채널 이탈")
    term_print(
        f"  [A] 이탈 시점: play ==== + {CHANNEL_LEAVE_DURING_AD_WAIT_SEC}초 "
        f"(고정 대기 — ImpressionLog 와 무관)"
    )
    term_print(
        "  [B] 이탈 후: logcat player play ==== → stop ==== 시간차 vs "
        f"ImpressionLog playTime합 (±{PLAYTIME_MATCH_TOLERANCE_MS / 1000:.0f}초)"
    )
    term_print(
        f"  광고 ch {channel_name}({result['channel_number']}) "
        f"→ 이탈 ch {escape_channel}"
    )
    term_print(f"{'=' * 60}")

    if not escape_channel:
        result["message"] = "이탈용 다른 채널 번호를 찾지 못함"
        _run_checklist["leave_during_ad"] = result
        term_print(f"  ✗ {result['message']}")
        return result

    term_print(f"{current_time_str()} [체크 4] ch {result['channel_number']} 로 전환 중…")
    expected_ids = _log_expected_catalog_ids(channel_name, channel_number)
    start_ad_playback_watch(
        devices,
        channel_name,
        channel_number,
        ad_time_str,
        announce=False,
        preload_buffer=False,
        expected_catalog_ids=expected_ids,
        log_lookback_sec=CHECK5_LOG_LOOKBACK_SEC,
    )
    if not switch_channel_with_verify(
        channel_number,
        devices,
        clear_buffer=False,
        channel_name=channel_name,
        expected_catalog_ids=expected_ids,
    ):
        clear_slot_watches(devices)
        if is_purchase_screen_blocking(devices):
            result["message"] = "유료가입 화면 — 가입 채널, 다음 편성에서 재시도"
        else:
            result["message"] = "채널 튜닝 실패 — 다음 편성에서 재시도"
        _run_checklist["leave_during_ad"] = result
        term_print(f"  ✗ {result['message']}")
        return result
    preload_ad_logcat_buffer(devices, lookback_sec=CHECK5_LOG_LOOKBACK_SEC)

    play_ok, play_at = wait_until_player_play(
        devices, timeout_sec=SLOT_AD_START_TIMEOUT_SEC
    )
    if not play_ok:
        result["message"] = (
            f"play ==== 미감지 ({SLOT_AD_START_TIMEOUT_SEC}초 — 편성 스킵)"
        )
        _run_checklist["leave_during_ad"] = result
        clear_slot_watches(devices)
        term_print(f"  ✗ {result['message']}")
        return result

    result["saw_play"] = True
    term_print(
        f"{current_time_str()} [A] play ==== 확인 — "
        f"{CHANNEL_LEAVE_DURING_AD_WAIT_SEC}초 대기 후 채널 변경"
    )
    time.sleep(CHANNEL_LEAVE_DURING_AD_WAIT_SEC)

    impression_before = _impression_counts(devices)
    _begin_leave_impression_window(devices)
    leave_epoch = time.time()
    left_at = leave_epoch
    # 이탈 시 logcat -c 하면 버퍼의 ImpressionLog 가 사라짐
    switch_channel_via_adb(escape_channel, devices, clear_buffer=False)
    time.sleep(CHANNEL_SWITCH_SETTLE_SEC)
    result["left_channel"] = True
    leave_playtime_ms = int((left_at - play_at) * 1000)
    result["leave_playtime_ms"] = leave_playtime_ms
    term_print(
        f"{current_time_str()} [A] ch {escape_channel} 로 이탈 완료 "
        f"(play→이탈 {leave_playtime_ms / 1000:.1f}초, 고정 대기)"
    )
    term_print(
        f"{current_time_str()} [B] ImpressionLog playTime 수신 대기 "
        f"(최대 {CHECK4_IMPRESSION_WAIT_SEC}초 — 이탈 후 ~10초 내 도착)"
    )

    deadline = time.time() + CHECK4_IMPRESSION_WAIT_SEC
    last_preload = 0.0
    evaluated = False
    while time.time() < deadline:
        now = time.time()
        if now - last_preload >= 1:
            preload_ad_logcat_buffer(
                devices,
                lookback_sec=CHECK4_POST_LEAVE_LOG_LOOKBACK_SEC,
                max_lines=CHECK4_POST_LEAVE_LOG_MAX_LINES,
            )
            last_preload = now
        if _check4_try_evaluate(devices, result, impression_before) == "done":
            evaluated = True
            elapsed = time.time() - leave_epoch
            term_print(
                f"{current_time_str()} [B] ImpressionLog 수신 후 "
                f"{elapsed:.1f}초 만에 play→stop vs playTime합 판정"
            )
            break
        time.sleep(0.3)

    if not evaluated:
        preload_ad_logcat_buffer(
            devices,
            lookback_sec=CHECK4_POST_LEAVE_LOG_LOOKBACK_SEC,
            max_lines=CHECK4_POST_LEAVE_LOG_MAX_LINES,
        )
        evaluated = _check4_try_evaluate(devices, result, impression_before) == "done"

    if evaluated:
        _print_check4_playtime_comparison(devices, result)
    else:
        result["done"] = True
        if _impression_increased(devices, impression_before):
            result["saw_impression"] = True
            _, total_ms = collect_impression_play_times(
                devices, after_leave_only=True
            )
            if total_ms <= 0:
                result["message"] = "ImpressionLog playTime 미수신"
            else:
                result["message"] = (
                    "player stop ==== logcat 미확인 — playTime합만 수신"
                )
        else:
            result["message"] = (
                f"ImpressionLog 미수신 ({CHECK4_IMPRESSION_WAIT_SEC}초 내)"
            )

    _run_checklist["leave_during_ad"] = result
    mark = "✓" if result["ok"] else "✗"
    term_print(f"{current_time_str()} [체크 4] {mark} {result['message']}")
    term_print(f"{'=' * 60}\n")
    print_checklist_progress(devices)
    clear_slot_watches(devices)
    return result


def _log_ui_capture_entry(entry, label=""):
    method = entry.get("capture_method") or "?"
    prefix = f" [{label}]" if label else ""
    term_print(
        f"{current_time_str()} [UI 캡처/{method}]{prefix} {entry.get('message', '')}"
    )
    if entry.get("path"):
        term_print(f"  캡처 저장: {entry['path']}")
    if entry.get("chat_path"):
        vis = entry.get("visibility_score")
        term_print(
            f"  Chat용(시인성 {vis}): {entry['chat_path']}"
            + (" ★" if entry.get("chat_preferred") else "")
        )
    elif entry.get("visibility_note"):
        term_print(f"  Chat 생략: {entry.get('visibility_note')}")
    if entry.get("ocr_text"):
        preview = " ".join(entry["ocr_text"].split())[:150]
        term_print(f"  OCR(상단·우측): {preview}")
    if entry.get("ok") and entry.get("ocr_variant"):
        term_print(
            f"  OCR 매칭: {entry.get('ocr_region')}/{entry.get('ocr_variant')} "
            f"(어두운 배경·밝은 글자 포함 다중 전처리)"
        )
    elif not entry.get("ocr_available"):
        term_print("  (OCR 미설치 시 캡처 파일로 수동 확인)")


def _check_ad_broadcast_phrase_with_fallback(device, tag):
    adb_path = adb_capture_path(LOG_DIR, tag)
    result = check_phrase_on_device(device, adb_path, AD_BROADCAST_UI_TEXT)
    msg = result.get("message") or ""
    should_try_obs = (
        not result.get("ok")
        and os.environ.get("AD_BROADCAST_OBS_FALLBACK", "1").strip().lower()
        not in ("0", "false", "no", "off")
        and (
            msg.startswith("ADB 캡처 실패")
            or not result.get("ocr_available")
            or os.environ.get("AD_BROADCAST_OBS_FALLBACK_ON_MISS", "1").strip().lower()
            not in ("0", "false", "no", "off")
        )
    )
    if not should_try_obs:
        return result

    try:
        from component.obs_capture import (
            OBSScreenCapture,
            check_phrase_on_screen,
            default_capture_path,
        )

        obs = OBSScreenCapture(
            host=os.environ.get("OBS_HOST", "127.0.0.1"),
            port=int(os.environ.get("OBS_PORT", "4455")),
            password=os.environ.get("OBS_PASSWORD", ""),
        )
        obs_path = default_capture_path(LOG_DIR, tag)
        obs_result = check_phrase_on_screen(
            obs,
            obs_path,
            AD_BROADCAST_UI_TEXT,
            source_name=os.environ.get("OBS_SOURCE"),
        )
        obs_result["capture_method"] = "obs"
        obs_result["adb_message"] = msg
        if obs_result.get("ok"):
            return obs_result
        if result.get("path"):
            obs_result["adb_path"] = result.get("path")
        if not obs_result.get("message"):
            obs_result["message"] = "OBS fallback 미검출"
        obs_result["message"] = f"{obs_result['message']} (ADB: {msg or '미검출'})"
        return obs_result
    except Exception as e:
        result["message"] = f"{msg or 'ADB 미검출'}; OBS fallback 실패: {e}"
        return result


def try_verify_ad_broadcast_ui(channel_name, channel_number, devices=None):
    """키즈 채널 우측 상단 OCR '광고 방송' — 1회 ADB screencap(su)."""
    devices = devices or []
    if not devices:
        return None
    if not is_kids_channel(channel_number):
        return None
    tag = f"ch{normalize_channel_number(channel_number)}"
    result = _check_ad_broadcast_phrase_with_fallback(devices[0], tag)
    entry = {
        "channel_name": channel_name,
        "channel_number": normalize_channel_number(channel_number),
        **result,
    }
    _run_checklist["ad_broadcast_ui"].append(entry)
    term_print("")
    _log_ui_capture_entry(entry)
    return entry


def verify_ad_broadcast_ui_burst(channel_name, channel_number, devices=None):
    """
    play ==== 이후·광고 종료(stop) 전까지만 5초 간격 캡처·OCR (최대 ~120초).
    흰 배경 오탐은 PASS로 치지 않음. 글씨 시인성 있는 1회 검출 시 즉시 PASS.
    Chat 첨부는 시인성 점수 최고인 *_chat.png 만 사용.
    """
    devices = devices or []
    if not devices:
        return None
    if not is_kids_channel(channel_number):
        return None

    ch = normalize_channel_number(channel_number)
    tag = f"ch{ch}"
    max_count = max(1, AD_BROADCAST_UI_CAPTURE_COUNT)
    interval = AD_BROADCAST_UI_CAPTURE_INTERVAL_SEC

    term_print(
        f"\n{current_time_str()} [UI 캡처] ch {ch} "
        f"'{AD_BROADCAST_UI_TEXT}' — play ==== ~ stop 구간만 "
        f"{interval:.0f}초 간격 최대 {max_count}회 "
        f"(글씨 보이는 장 검출 시 종료, Chat은 그중 최고 시인성)"
    )

    play_deadline = time.time() + AD_BROADCAST_UI_PLAY_WAIT_SEC
    play_seen = False
    last_preload = 0.0
    while time.time() < play_deadline:
        now = time.time()
        if now - last_preload >= 2:
            preload_ad_logcat_buffer(
                devices,
                lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC,
                max_lines=800,
            )
            last_preload = now
        if _ad_playback_started(devices):
            play_seen = True
            term_print(f"{current_time_str()} [UI 캡처] play ==== 확인 — 캡처 시작")
            break
        time.sleep(0.5)

    if not play_seen:
        term_print(
            f"{current_time_str()} [UI 캡처] play ==== 미감지 "
            f"({AD_BROADCAST_UI_PLAY_WAIT_SEC}초) — 광고 중이 아니므로 OCR 생략"
        )
        return None

    best = None
    best_chat = None
    for attempt in range(1, max_count + 1):
        if _ad_playback_stopped(devices):
            term_print(
                f"{current_time_str()} [UI 캡처] player stop 감지 — "
                f"광고 종료, OCR 중단 ({attempt - 1}/{max_count}회까지)"
            )
            break
        if not _ad_playback_started(devices) and attempt > 1:
            term_print(
                f"{current_time_str()} [UI 캡처] play 상태 아님 — OCR 중단"
            )
            break

        result = _check_ad_broadcast_phrase_with_fallback(devices[0], tag)
        entry = {
            "channel_name": channel_name,
            "channel_number": ch,
            "attempt": attempt,
            "max_attempts": max_count,
            **result,
        }
        # Chat 후보: 시인성 있는 chat_path 만
        if entry.get("chat_path") and entry.get("badge_visible"):
            score = float(entry.get("visibility_score") or 0.0)
            prev = float((best_chat or {}).get("visibility_score") or -1.0)
            if best_chat is None or score >= prev:
                if best_chat is not None:
                    best_chat.pop("chat_preferred", None)
                entry["chat_preferred"] = True
                best_chat = entry
        _run_checklist["ad_broadcast_ui"].append(entry)
        _log_ui_capture_entry(entry, label=f"{attempt}/{max_count}")
        visible_ok = bool(entry.get("ok") and entry.get("badge_visible"))
        if best is None or visible_ok:
            best = entry
        if visible_ok:
            term_print(
                f"{current_time_str()} [UI 캡처] ✓ {attempt}회째 "
                f"'{AD_BROADCAST_UI_TEXT}' 시인성 확인 "
                f"(score={entry.get('visibility_score')}) — 종료"
            )
            break
        if attempt < max_count:
            end_sleep = time.time() + interval
            while time.time() < end_sleep:
                if _ad_playback_stopped(devices):
                    break
                time.sleep(0.25)

    if best_chat is not None and best is not None:
        # Chat 첨부 우선순위: 시인성 최고 장
        best["chat_path"] = best_chat.get("chat_path")
        best["chat_preferred"] = True
        best["visibility_score"] = best_chat.get("visibility_score")
        best["badge_visible"] = True

    if best is None:
        term_print(
            f"{current_time_str()} [UI 캡처] ✗ 광고 재생 구간 캡처 없음"
        )
    elif not (best.get("ok") and best.get("badge_visible")):
        term_print(
            f"{current_time_str()} [UI 캡처] ✗ 광고 재생 중 '{AD_BROADCAST_UI_TEXT}' "
            f"미검출(또는 흰 배경만) — Chat 첨부 생략"
        )
    return best


def connect_all_devices(device_ips):
    for device_ip in device_ips:
        connect_devices(device_ip)


def reboot_devices(device_ips, *, reason=""):
    """adb reboot — 이번 Default behavior 실행당 최대 1회."""
    global _stb_reboot_sent
    if _stb_reboot_sent and not _env_truthy("STB_ALLOW_MULTI_REBOOT"):
        note = f" ({reason})" if reason else ""
        term_print(
            f"{current_time_str()} [재부팅] 이미 이번 실행에서 재부팅함 — "
            f"중복 명령 스킵{note}"
        )
        return False
    _stb_reboot_sent = True
    if reason:
        term_print(f"{current_time_str()} [재부팅] {reason}")

    def reboot_one(device_ip):
        print(f"{current_time_str()} [{device_ip}] 재부팅 명령 전송")
        subprocess.run(
            ["adb", "-s", device_ip, "reboot"],
            capture_output=True,
            text=True,
        )

    threads = [
        threading.Thread(target=reboot_one, args=(ip,)) for ip in device_ips
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"{current_time_str()} 모든 디바이스 재부팅 명령 완료")


def device_is_ready(device_ip):
    result = subprocess.run(
        ["adb", "-s", device_ip, "get-state"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and "device" in result.stdout.lower()


def wait_for_devices_after_reboot(device_ips):
    print(
        f"{current_time_str()} 재부팅 대기 중 "
        f"(최소 {REBOOT_MIN_WAIT_SEC}초, 최대 {REBOOT_READY_TIMEOUT_SEC}초)..."
    )
    time.sleep(REBOOT_MIN_WAIT_SEC)

    pending = set(device_ips)
    deadline = time.time() + REBOOT_READY_TIMEOUT_SEC

    while pending and time.time() < deadline:
        for device_ip in list(pending):
            connect_devices(device_ip)
            if device_is_ready(device_ip):
                print(f"{current_time_str()} [{device_ip}] 재부팅 후 연결 확인")
                pending.remove(device_ip)
        if pending:
            time.sleep(REBOOT_POLL_INTERVAL_SEC)

    if pending:
        print(f"{current_time_str()} 준비되지 않은 디바이스: {', '.join(pending)}")
        return False
    return True


def extract_sdk_version_from_json_line(line: str) -> str | None:
    """sdkVersion.name — JSON / VersionInfo(code=…, name=…) 모두 지원."""
    if "sdkversion" not in line.lower():
        return None
    for pattern in (SDK_VERSION_JSON_RE, SDK_VERSION_INFO_RE, SDK_VERSION_NAME_RE):
        m = pattern.search(line)
        if m:
            return m.group(1).strip()
    return None


def extract_version_value(line, needle):
    """로그 한 줄에서 needle 뒤 버전 문자열 추출 (나머지 줄 전체, 괄호·콜론 포함)."""
    # sdkVersion=VersionInfo(... name=...) — name 필드만 사용
    if "sdkversion" in needle.lower():
        return extract_sdk_version_from_json_line(line)
    lower_line = line.lower()
    lower_needle = needle.lower()
    if lower_needle not in lower_line:
        return None
    idx = lower_line.index(lower_needle)
    rest = line[idx + len(needle) :].strip()
    rest = re.sub(r"^[:=\s]+", "", rest)
    return rest if rest else None


def parse_firmware_version_string(raw_value):
    """firmware ver(full) 값에서 V.xx.xx.xxxx 추출. 없으면 None."""
    if not raw_value:
        return None
    match = FIRMWARE_VERSION_PATTERN.search(raw_value)
    if match:
        return match.group(0)
    # ute7057lgu: …/20260115_V.02.02.0191:userdebug
    m = re.search(r"_V\.(\d+\.\d+\.\d+)", raw_value)
    if m:
        return f"V.{m.group(1)}"
    # UHD4K: LGUPlus/UHD4K/UHD4K:12/SC/02.02.0254:… 또는 FIRMWARE_VER=…_V.02.02.0254
    m = re.search(r"SC/(\d+\.\d+\.\d+)", raw_value, re.IGNORECASE)
    if m:
        return f"V.{m.group(1)}"
    m = re.search(r"_V\.(\d+\.\d+\.\d+)(?:[|:]|$)", raw_value)
    if m:
        return f"V.{m.group(1)}"
    # getprop incremental 단독: 02.02.0254
    m = re.fullmatch(r"\d+\.\d+\.\d+", raw_value.strip())
    if m:
        return f"V.{m.group(0)}"
    # mau7200 등: 02020090 → V.02.02.0090
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", raw_value.strip())
    if m:
        return f"V.{m.group(1)}.{m.group(2)}.{m.group(3)}"
    # display.id 끝 8자리: … 02020090
    m = re.search(r"(?:^|[\s/:])(\d{2})(\d{2})(\d{4})(?:\s|$)", raw_value)
    if m:
        return f"V.{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return None


def format_version_display(group_key, raw_value):
    """펌웨어는 V.xx.xx.xxxx 만 표시 (예: …20260115_V.02.02.0191:… → V.02.02.0191)."""
    if group_key == "firmware":
        return parse_firmware_version_string(raw_value)
    return raw_value


def fetch_firmware_via_getprop(device_ip):
    """Firmware: getprop 로 V.xx.xx.xxxx (logcat 버퍼 의존 없음)."""
    for prop in FIRMWARE_GETPROP_KEYS:
        try:
            result = subprocess.run(
                ["adb", "-s", device_ip, "shell", "getprop", prop],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=15,
            )
        except Exception:
            continue
        raw = (result.stdout or "").strip()
        if not raw:
            continue
        parsed = parse_firmware_version_string(raw)
        if parsed:
            return parsed, f"getprop {prop}: {raw}"
    return None


def fetch_device_datetime(device_ip):
    """STB shell date — logcat 시각(기기 시계)과 PC 시계 차이 보정용."""
    try:
        result = subprocess.run(
            ["adb", "-s", device_ip, "shell", "date", "+%m-%d %H:%M:%S"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return None
        ref = datetime.now()
        year = ref.year
        dt = datetime.strptime(f"{year}-{raw}", "%Y-%m-%d %H:%M:%S")
        if dt > ref + timedelta(hours=2):
            dt = dt.replace(year=year - 1)
        return dt
    except Exception:
        return None


def _format_version_log_age(log_dt, log_now) -> str:
    if log_dt is None or log_now is None:
        return ""
    age_sec = int((log_now - log_dt).total_seconds())
    if age_sec < 0:
        return f" (log {_format_logcat_dt(log_dt)}, 기기 시각)"
    return f" (log {_format_logcat_dt(log_dt)}, {age_sec}초 전)"


def _version_scan_log_now(device_ip, lines=None, log_clock=None):
    """버전 lookback 기준 '지금' — 버퍼 최신 줄 시각 우선, 없으면 adb date."""
    dts = []
    for line in lines or []:
        dt = parse_logcat_line_datetime(line)
        if dt is not None:
            dts.append(dt)
    if log_clock is not None:
        dts.append(log_clock)
    if dts:
        return max(dts)
    return fetch_device_datetime(device_ip) or datetime.now()


def _version_scan_not_before(log_now, lookback_sec=None) -> datetime:
    sec = lookback_sec if lookback_sec is not None else VERSION_SCAN_LOG_LOOKBACK_SEC
    return log_now - timedelta(seconds=sec)


def _apply_firmware_getprop_if_missing(device_ip, versions) -> bool:
    """logcat 에 firmware 없을 때 getprop — True 이면 값 채움."""
    if versions.get("firmware"):
        return True
    fw_prop = fetch_firmware_via_getprop(device_ip)
    if not fw_prop:
        return False
    term_print(
        f"{current_time_str()} [{device_ip}] logcat 에 firmware 없음 "
        f"— getprop fallback"
    )
    _set_firmware_version(versions, device_ip, fw_prop[0], fw_prop[1])
    return True


def _set_firmware_version(
    versions,
    device_ip,
    value,
    source_line="",
    log_dt=None,
    log_now=None,
    newest_at=None,
):
    if not value:
        return False
    if log_dt is not None and newest_at is not None:
        prev_dt = newest_at.get("firmware")
        if prev_dt is not None and log_dt <= prev_dt:
            return False
        newest_at["firmware"] = log_dt
    prev = versions.get("firmware")
    if prev == value and log_dt is None:
        return True
    versions["firmware"] = value
    age = _format_version_log_age(log_dt, log_now)
    term_print(f"{current_time_str()} [{device_ip}] → Firmware: {value}{age}")
    return True


def _set_version_value(
    versions,
    device_ip,
    group_key,
    value,
    label,
    log_dt=None,
    log_now=None,
    newest_at=None,
):
    if not value:
        return False
    if log_dt is not None and newest_at is not None:
        prev_dt = newest_at.get(group_key)
        if prev_dt is not None and log_dt <= prev_dt:
            return False
        newest_at[group_key] = log_dt
    versions[group_key] = value
    age = _format_version_log_age(log_dt, log_now)
    term_print(f"{current_time_str()} [{device_ip}] → {label}: {value}{age}")
    return True


def _apply_version_line(
    stripped, versions, device_ip, log_dt=None, log_now=None, newest_at=None
):
    """한 줄에서 VERSION_GROUPS 매칭 — 동일 구간이면 log 시각이 더 최신인 것만."""
    for group_key, group in VERSION_GROUPS.items():
        if group_key == "firmware":
            m = FIRMWARE_FULL_LINE_RE.search(stripped)
            if m:
                value = parse_firmware_version_string(m.group(1))
                if value:
                    _set_firmware_version(
                        versions,
                        device_ip,
                        value,
                        stripped,
                        log_dt=log_dt,
                        log_now=log_now,
                        newest_at=newest_at,
                    )
            continue

        for needle in group["needles"]:
            if needle.lower() not in stripped.lower():
                continue
            raw = extract_version_value(stripped, needle)
            if not raw:
                continue
            value = format_version_display(group_key, raw)
            if value:
                _set_version_value(
                    versions,
                    device_ip,
                    group_key,
                    value,
                    group["label"],
                    log_dt=log_dt,
                    log_now=log_now,
                    newest_at=newest_at,
                )
            break

        if group_key == "sdk":
            json_sdk = extract_sdk_version_from_json_line(stripped)
            if json_sdk:
                _set_version_value(
                    versions,
                    device_ip,
                    "sdk",
                    json_sdk,
                    VERSION_GROUPS["sdk"]["label"],
                    log_dt=log_dt,
                    log_now=log_now,
                    newest_at=newest_at,
                )


def _scan_versions_from_logcat_dump(
    device_ip, versions, newest_at, log_clock_holder, *, verbose=True, lookback_sec=None
) -> bool:
    """logcat -d — 기기 log 시각 기준 최근 N초, 그중 가장 최신 줄만."""
    try:
        dump = subprocess.run(
            [
                "adb",
                "-s",
                device_ip,
                "logcat",
                "-d",
                "-v",
                "time",
                "-t",
                str(VERSION_LOGCAT_DUMP_MAX_LINES),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=90,
        )
    except Exception:
        return False
    if not dump.stdout:
        return False

    lines = [ln.rstrip() for ln in dump.stdout.splitlines() if ln.strip()]
    log_now = _version_scan_log_now(
        device_ip, lines, log_clock=log_clock_holder.get("dt")
    )
    if log_clock_holder.get("dt") is None or log_now > log_clock_holder["dt"]:
        log_clock_holder["dt"] = log_now
    if verbose:
        lb = lookback_sec if lookback_sec is not None else VERSION_SCAN_LOG_LOOKBACK_SEC
        term_print(
            f"{current_time_str()} [{device_ip}] logcat 버퍼 검색 "
            f"(log 기준 최근 {lb}초, "
            f"기준시각 {_format_logcat_dt(log_now)}, 최대 "
            f"{VERSION_LOGCAT_DUMP_MAX_LINES}줄)"
        )

    not_before = _version_scan_not_before(log_now, lookback_sec)
    for line in lines:
        log_dt = parse_logcat_line_datetime(line)
        if log_dt is None or log_dt < not_before:
            continue
        _apply_version_line(
            line,
            versions,
            device_ip,
            log_dt=log_dt,
            log_now=log_now,
            newest_at=newest_at,
        )
    _apply_firmware_getprop_if_missing(device_ip, versions)
    return all(versions.values())


def scan_versions_on_device(
    device_ip, timeout_sec=VERSION_SCAN_TIMEOUT_SEC, lookback_sec=None
):
    """단일 디바이스 — logcat(기기 시각 최근 N초·최신 줄) + getprop fallback."""
    lb = lookback_sec if lookback_sec is not None else VERSION_SCAN_LOG_LOOKBACK_SEC
    versions = {key: None for key in VERSION_GROUPS}
    newest_at = {}
    log_clock_holder = {"dt": fetch_device_datetime(device_ip)}

    term_print(
        f"{current_time_str()} [{device_ip}] 버전 수집 (logcat 기기 시각 "
        f"최근 {lb}초 + tail, 최대 {timeout_sec}초)"
    )
    for group in VERSION_GROUPS.values():
        needles = ", ".join(group["needles"][:2])
        if group.get("label") == "SDK":
            needles += ", sdkVersion.name / VersionInfo(name=)"
        term_print(f"  {group['label']}: {needles} …")

    process = subprocess.Popen(
        ["adb", "-s", device_ip, "logcat", "-v", "time"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    deadline = time.time() + timeout_sec
    last_status_at = time.time()
    last_dump_at = 0.0
    agent_seen_at = None
    line_queue = queue.Queue()

    def _read_stdout():
        try:
            for stdout_line in process.stdout:
                line_queue.put(stdout_line)
        finally:
            line_queue.put(None)

    threading.Thread(target=_read_stdout, daemon=True).start()

    def _print_version_wait_status():
        missing = [
            VERSION_GROUPS[k]["label"] for k, val in versions.items() if not val
        ]
        if missing:
            remain = max(0, int(deadline - time.time()))
            term_print(
                f"{current_time_str()} [{device_ip}] "
                f"버전 대기… 미확인: {', '.join(missing)} "
                f"(남은 {remain}초 — 그동안 not linear 는 즉시 복구)"
            )

    def _apply_queued_lines(block=False):
        log_now = _version_scan_log_now(
            device_ip, log_clock=log_clock_holder.get("dt")
        )
        not_before = _version_scan_not_before(log_now, lookback_sec)
        while True:
            try:
                line = line_queue.get(timeout=0.15 if block else 0)
            except queue.Empty:
                break
            if line is None:
                return True
            stripped = line.rstrip()
            if not stripped:
                continue
            # 버전 확인 중이어도 not linear 는 바로 채널 복구
            _maybe_recover_non_linear_tv(device_ip, stripped)
            log_dt = parse_logcat_line_datetime(stripped)
            if log_dt is None or log_dt < not_before:
                continue
            if (
                log_clock_holder.get("dt") is None
                or log_dt > log_clock_holder["dt"]
            ):
                log_clock_holder["dt"] = log_dt
                log_now = log_dt
                not_before = _version_scan_not_before(log_now, lookback_sec)
            _apply_version_line(
                stripped,
                versions,
                device_ip,
                log_dt=log_dt,
                log_now=log_now,
                newest_at=newest_at,
            )
            _apply_firmware_getprop_if_missing(device_ip, versions)
        return False

    # 시작 직후 getprop 으로 FW 확보 (logcat 의존 최소화)
    _apply_firmware_getprop_if_missing(device_ip, versions)

    if _scan_versions_from_logcat_dump(
        device_ip, versions, newest_at, log_clock_holder, lookback_sec=lookback_sec
    ):
        process.terminate()
        term_print(
            f"{current_time_str()} [{device_ip}] 버전 확인 완료 (버퍼·log 최근 "
            f"{lb}초)"
        )
        return versions

    try:
        while time.time() < deadline and not all(versions.values()):
            if _apply_queued_lines(block=True):
                break

            now = time.time()
            _apply_firmware_getprop_if_missing(device_ip, versions)
            if all(versions.values()):
                break

            if versions.get("agent"):
                if agent_seen_at is None:
                    agent_seen_at = now
                elif (
                    not versions.get("sdk")
                    and now - agent_seen_at >= VERSION_SDK_WAIT_AFTER_AGENT_SEC
                ):
                    term_print(
                        f"{current_time_str()} [{device_ip}] SDK logcat 미확인 "
                        f"{VERSION_SDK_WAIT_AFTER_AGENT_SEC}초 — "
                        f"버전 대기 중단, 편성 모니터링으로 진행"
                    )
                    break

            if now - last_dump_at >= 10:
                if _scan_versions_from_logcat_dump(
                    device_ip,
                    versions,
                    newest_at,
                    log_clock_holder,
                    verbose=False,
                    lookback_sec=lookback_sec,
                ):
                    break
                last_dump_at = now

            if now - last_status_at >= VERSION_STATUS_INTERVAL_SEC:
                _print_version_wait_status()
                last_status_at = now
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    _apply_firmware_getprop_if_missing(device_ip, versions)

    if all(versions.values()):
        term_print(f"{current_time_str()} [{device_ip}] 버전 확인 완료")
    else:
        missing = [VERSION_GROUPS[k]["label"] for k, val in versions.items() if not val]
        term_print(
            f"{current_time_str()} [{device_ip}] 버전 확인 종료 (미확인: {', '.join(missing)})"
        )

    return versions


def _sw_versions_complete(results=None) -> bool:
    results = results if results is not None else (_run_checklist.get("versions") or {})
    if not results:
        return False
    try:
        return all(
            all(v.values()) for v in results.values() if isinstance(v, dict)
        )
    except Exception:
        return False


def _merge_sw_version_maps(prev: dict, new: dict) -> dict:
    """디바이스별 FW/SDK/Agent — 새로 찾은 값으로 빈칸만(또는 갱신) 채움."""
    out = {}
    devices = set(prev or {}) | set(new or {})
    for device in devices:
        p = (prev or {}).get(device) or {}
        n = (new or {}).get(device) or {}
        out[device] = {
            key: (n.get(key) or p.get(key)) for key in VERSION_GROUPS
        }
    return out


def maybe_retry_sw_versions_after_live(devices, *, reason="라이브 복귀 후"):
    """
    라이브 복귀 후 — 미확인 S/W ver. 재수집.
    부팅 직후/비선형 구간에서는 sdkVersion.name 등이 안 나와 체크1이 영구 FAIL 되는 것을 보완.
    """
    global _version_retry_after_live_at
    if _run_checklist.get("versions_skipped"):
        return False
    if not devices:
        return False
    if _sw_versions_complete():
        return False

    with _version_retry_after_live_lock:
        now = time.time()
        if now - _version_retry_after_live_at < VERSION_RETRY_AFTER_LIVE_COOLDOWN_SEC:
            return False
        _version_retry_after_live_at = now

    live_ok = any(
        screen_kind_from_activity(get_resumed_activity_line(d)) == "live"
        for d in devices
    )
    if not live_ok:
        term_print(
            f"{current_time_str()} [버전 재확인] 라이브 미확인 — S/W ver. 재수집 보류"
        )
        return False

    missing_before = []
    prev = _run_checklist.get("versions") or {}
    for device in devices:
        vers = prev.get(device) or {}
        for key, group in VERSION_GROUPS.items():
            if not vers.get(key):
                missing_before.append(f"{device}:{group['label']}")
    term_print(
        f"\n{current_time_str()} [버전 재확인] {reason} S/W ver. 재수집 "
        f"(미확인: {', '.join(missing_before) or '없음'}, "
        f"최대 {VERSION_RETRY_AFTER_LIVE_TIMEOUT_SEC}초)"
    )

    fresh = {}
    for device in devices:
        fresh[device] = scan_versions_on_device(
            device,
            timeout_sec=VERSION_RETRY_AFTER_LIVE_TIMEOUT_SEC,
            lookback_sec=POST_REBOOT_VERSION_LOOKBACK_SEC,
        )
    merged = _merge_sw_version_maps(prev, fresh)
    _run_checklist["versions"] = merged

    term_print(f"{current_time_str()} === [버전 재확인] 결과 ===")
    for device in devices:
        vers = merged.get(device) or {}
        parts = [
            f"{VERSION_GROUPS[k]['label']}={vers.get(k) or '✗'}"
            for k in VERSION_GROUPS
        ]
        term_print(f"  [{device}] {', '.join(parts)}")
    if _sw_versions_complete(merged):
        term_print(f"{current_time_str()} [버전 재확인] ✓ 체크 1 S/W ver. 보완 완료")
    else:
        still = [
            VERSION_GROUPS[k]["label"]
            for device in devices
            for k in VERSION_GROUPS
            if not (merged.get(device) or {}).get(k)
        ]
        term_print(
            f"{current_time_str()} [버전 재확인] 여전히 미확인: "
            f"{', '.join(dict.fromkeys(still))}"
        )
    print_checklist_progress(devices)
    return _sw_versions_complete(merged)


def start_sw_version_retry_when_live(devices, *, reason="라이브 확인 후"):
    """초기 버전 실패 후 편성 진행을 막지 않고, 라이브가 확인되면 백그라운드 재수집."""
    global _version_retry_after_live_thread
    if _run_checklist.get("versions_skipped") or _sw_versions_complete():
        return False
    if not devices:
        return False
    if (
        _version_retry_after_live_thread
        and _version_retry_after_live_thread.is_alive()
    ):
        return False

    def worker():
        deadline = time.time() + 240
        while time.time() < deadline and not _sw_versions_complete():
            live_ok = any(
                screen_kind_from_activity(get_resumed_activity_line(d)) == "live"
                for d in devices
            )
            if live_ok:
                maybe_retry_sw_versions_after_live(devices, reason=reason)
                return
            time.sleep(5)
        if not _sw_versions_complete():
            term_print(
                f"{current_time_str()} [버전 재확인] 240초 내 라이브 미확인 — "
                f"S/W ver. 재수집 보류"
            )

    _version_retry_after_live_thread = threading.Thread(
        target=worker,
        name="sw-version-retry-after-live",
        daemon=True,
    )
    _version_retry_after_live_thread.start()
    term_print(
        f"{current_time_str()} [버전 재확인] 초기 미확인 항목 있음 — "
        f"라이브 확인 시 백그라운드 S/W ver. 재수집 예약"
    )
    return True


def collect_versions_after_reboot(device_ips, lookback_sec=None):
    """모든 디바이스 — logcat(기기 시각 최근 N초) 우선, firmware 만 getprop fallback."""
    lb = lookback_sec if lookback_sec is not None else VERSION_SCAN_LOG_LOOKBACK_SEC
    term_print(f"\n{'=' * 60}")
    term_print(
        f"{current_time_str()} [버전 확인] logcat 기기 시각 최근 "
        f"{lb}초 (최신 줄 우선) + getprop fallback"
    )
    term_print(f"{'=' * 60}")

    results = {}
    lock = threading.Lock()

    def worker(device_ip):
        versions = scan_versions_on_device(device_ip, lookback_sec=lookback_sec)
        with lock:
            results[device_ip] = versions

    threads = [threading.Thread(target=worker, args=(ip,)) for ip in device_ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    term_print(f"\n{'=' * 60}")
    term_print(f"{current_time_str()} === [버전 확인] 결과 요약 ===")
    term_print(f"{'=' * 60}")
    all_ok = True
    for device_ip in device_ips:
        versions = results.get(device_ip, {})
        term_print(f"\n[{device_ip}]")
        for group_key, group in VERSION_GROUPS.items():
            value = versions.get(group_key)
            label = group["label"]
            if value:
                term_print(f"  ✓ {label}: {value}")
            else:
                needles = ", ".join(group["needles"][:2])
                term_print(f"  ✗ {label}: (미확인 — 예: {needles})")
                all_ok = False

    if all_ok:
        term_print(f"\n{current_time_str()} 모든 디바이스 버전 확인 완료")
    else:
        term_print(
            f"\n{current_time_str()} 일부 버전을 찾지 못했습니다. "
            f"(디바이스당 최대 {VERSION_SCAN_TIMEOUT_SEC}초)"
        )
    term_print(f"{'=' * 60}\n")
    _run_checklist["versions"] = results
    print_checklist_progress(device_ips)
    return all_ok


def _versions_missing_sdk_or_agent(results) -> bool:
    for versions in results.values():
        if not versions.get("sdk") or not versions.get("agent"):
            return True
    return False


def ensure_versions_with_optional_reboot(device_ips, *, skip_reboot: bool) -> bool:
    """버전 확인. SKIP_REBOOT=1 이면 adb reboot 없음 (VERSION_REBOOT_ON_MISS=1 예외)."""
    if not skip_reboot:
        term_print(
            f"\n{current_time_str()} [SKIP_REBOOT=0] 시작 시 STB 재부팅 1회 "
            f"(재부팅 없이 logcat만: SKIP_REBOOT=1)"
        )
        reboot_devices(device_ips, reason="SKIP_REBOOT=0 시작 재부팅")
        if not wait_for_devices_after_reboot(device_ips):
            print("재부팅 후 디바이스 준비 실패. 종료합니다.")
            return False
        term_print(
            f"\n{current_time_str()} 재부팅 완료 — Firmware / SDK / Agent 버전 확인"
        )
        collect_versions_after_reboot(
            device_ips, lookback_sec=POST_REBOOT_VERSION_LOOKBACK_SEC
        )
        start_sw_version_retry_when_live(
            device_ips,
            reason="재부팅 후 라이브 확인",
        )
        return True

    lookback = POST_REBOOT_VERSION_LOOKBACK_SEC
    term_print(
        f"\n{current_time_str()} [SKIP_REBOOT=1] 재부팅 없음 — logcat "
        f"최근 {lookback}초 + getprop"
    )
    if collect_versions_after_reboot(device_ips, lookback_sec=lookback):
        return True
    start_sw_version_retry_when_live(
        device_ips,
        reason="SKIP_REBOOT=1 라이브 확인",
    )
    results = _run_checklist.get("versions") or {}
    if not _versions_missing_sdk_or_agent(results):
        term_print(
            f"\n{current_time_str()} [버전 확인] Firmware getprop 등 확인 — "
            f"재부팅 없이 계속"
        )
        return True
    if not VERSION_REBOOT_ON_MISS:
        term_print(
            f"\n{current_time_str()} [버전 확인] SDK/Agent logcat 미확인 — "
            f"SKIP_REBOOT=1 이므로 재부팅 없이 편성 모니터링 계속 "
            f"(재부팅 원하면 VERSION_REBOOT_ON_MISS=1 또는 SKIP_REBOOT=0)"
        )
        return True
    term_print(
        f"\n{current_time_str()} [VERSION_REBOOT_ON_MISS] SDK/Agent 미확인 — "
        f"재부팅 1회"
    )
    reboot_devices(device_ips, reason="VERSION_REBOOT_ON_MISS")
    if not wait_for_devices_after_reboot(device_ips):
        print("재부팅 후 디바이스 준비 실패. 종료합니다.")
        return False
    term_print(
        f"\n{current_time_str()} 재부팅 완료 — Firmware / SDK / Agent 버전 재확인"
    )
    collect_versions_after_reboot(
        device_ips, lookback_sec=POST_REBOOT_VERSION_LOOKBACK_SEC
    )
    return True


def parse_logcat_line_datetime(line, ref_now=None):
    """logcat -v time 한 줄 앞부분 시각 파싱 (없으면 None)."""
    ref_now = ref_now or datetime.now()
    m = LOGCAT_LINE_TIME_RE.match(line.strip())
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else ref_now.year
    try:
        dt = datetime.strptime(
            f"{year}-{m.group(2)} {m.group(3)}", "%Y-%m-%d %H:%M:%S.%f"
        )
    except ValueError:
        return None
    if dt > ref_now + timedelta(hours=2):
        dt = dt.replace(year=year - 1)
    return dt


def normalize_channel_number(channel_number):
    if channel_number is None:
        return ""
    s = str(channel_number).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def is_kids_channel(channel_number):
    return normalize_channel_number(channel_number) in KIDS_CHANNEL_NUMBERS


def is_kids_prime_time(now=None):
    """매시 50분~59분: 키즈 편성·워터마크 우선 구간."""
    now = now or datetime.now()
    return now.minute >= KIDS_PRIME_TIME_START_MINUTE


def _set_pending_tune_targets(devices, channel_number):
    target = str(normalize_channel_number(channel_number))
    with _pending_tune_lock:
        for device in devices:
            _pending_tune_targets[device] = target


def _clear_pending_tune_targets(devices):
    with _pending_tune_lock:
        for device in devices:
            _pending_tune_targets.pop(device, None)


def _pending_tune_target(device):
    with _pending_tune_lock:
        return _pending_tune_targets.get(device)


def _collect_future_ad_candidates(data, now):
    candidates = []
    for row in data:
        try:
            if (
                "광고편성 시간" not in row
                or "채널명" not in row
                or "채널번호" not in row
            ):
                continue
            ad_dt = parse_ad_datetime(now, row["광고편성 시간"])
            if ad_dt > now:
                candidates.append((ad_dt, row))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0])
    return candidates


def _collect_actionable_ad_candidates(data, now):
    """처리 기한(ad+timeout) 내 슬롯 — 편성 시각이 지났어도 cue 대기 중이면 포함."""
    candidates = []
    for row in data:
        try:
            if (
                "광고편성 시간" not in row
                or "채널명" not in row
                or "채널번호" not in row
            ):
                continue
            ad_dt = parse_ad_datetime(now, row["광고편성 시간"])
            late = ad_dt + timedelta(
                seconds=_slot_ad_start_timeout_sec(
                    row["채널번호"], row["광고편성 시간"]
                )
            )
            if now < late:
                candidates.append((ad_dt, row))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0])
    return candidates


def _filter_kids_prime_candidates(candidates):
    return [
        (ad_dt, row)
        for ad_dt, row in candidates
        if is_kids_channel(row["채널번호"]) and is_kids_prime_time(ad_dt)
    ]


def _log_kids_priority_selected(next_row, next_time, *, mandatory=False):
    global _last_kids_priority_log_key
    log_key = (
        normalize_channel_number(next_row["채널번호"]),
        next_row["광고편성 시간"],
    )
    if log_key == _last_kids_priority_log_key:
        return
    _last_kids_priority_log_key = log_key
    switch_at = (next_time - timedelta(seconds=30)).strftime("%H:%M:%S")
    mode = "무조건 우선" if mandatory else "대기"
    term_print(
        f"{current_time_str()} [키즈 우선/{mode}] "
        f"{next_row['채널명']}({log_key[0]}) @ {log_key[1]} "
        f"(채널 전환 예정 {switch_at})"
    )


def pick_next_ad_row(data, now):
    """다음 광고 row 선택.

    6번 미완료 시:
    - :50~:59(prime): 이번 시각대 남은 키즈 prime만 우선.
      이번 시간 슬롯이 더 없으면 2~5번용 일반 편성으로 진행.
    - 그 외 + 6번만 남음: 다음 키즈 prime 편성까지 대기 (일반 모니터링 안 함).
    """
    if not kids_check6_passed():
        actionable = _collect_actionable_ad_candidates(data, now)
        kids_prime = _filter_kids_prime_candidates(actionable)

        if _should_force_kids_prime_priority(now):
            hour = now.hour
            this_hour = [
                (ad_dt, row) for ad_dt, row in kids_prime if ad_dt.hour == hour
            ]
            if this_hour:
                next_time, next_row = this_hour[0]
                _log_kids_priority_selected(
                    next_row, next_time, mandatory=True
                )
                return next_time, next_row
            # 이번 :50~:59 키즈 소진 → 일반 편성(2~5)으로 진행

        if _only_check6_pending():
            pool = kids_prime
            if not pool:
                future = _collect_future_ad_candidates(data, now)
                pool = _filter_kids_prime_candidates(future)
            if pool:
                next_time, next_row = pool[0]
                _log_kids_priority_selected(
                    next_row, next_time, mandatory=False
                )
                return next_time, next_row
            return None, None

    candidates = _collect_future_ad_candidates(data, now)
    if not candidates:
        return None, None
    return candidates[0]


def parse_ad_datetime(now, ad_time_str):
    """광고 편성 시각을 '가장 가까운 실제 시각' datetime으로 변환."""
    ad_time = datetime.strptime(ad_time_str.strip(), "%H:%M:%S")
    ad_dt = now.replace(
        hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0
    )
    if ad_dt <= now:
        ad_dt -= timedelta(days=1)
    if ad_dt > now + timedelta(hours=18):
        ad_dt -= timedelta(days=1)
    return ad_dt


def clear_logcat_buffers(devices):
    """채널 전환 전 옛 logcat 버퍼 제거 (과거 cue/play 오인 방지)."""
    for device in devices:
        subprocess.run(
            ["adb", "-s", device, "logcat", "-c"],
            capture_output=True,
        )


def get_resumed_activity_line(device):
    """포그라운드 Activity 한 줄. Android 10+ 는 topResumedActivity 우선."""
    try:
        r = subprocess.run(
            ["adb", "-s", device, "shell", "dumpsys", "activity", "activities"],
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ""
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    top = ""
    resumed = ""
    for line in out.splitlines():
        s = line.strip()
        if "topResumedActivity" in s and not top:
            top = s
        elif "mResumedActivity" in s and not resumed:
            resumed = s
    return top or resumed


def is_blocking_purchase_or_dialog(activity_line: str) -> bool:
    if not activity_line:
        return False
    low = activity_line.lower()
    return any(marker.lower() in low for marker in BLOCKING_UI_ACTIVITY_MARKERS)


def is_live_tv_foreground(activity_line: str) -> bool:
    """dumpsys 미수신(빈 문자열)이면 키/탭 복구 생략 — 오탐 방지."""
    if not activity_line:
        return True
    low = activity_line.lower()
    return any(marker.lower() in low for marker in LIVE_TV_ACTIVITY_MARKERS)


def is_search_ui_foreground(activity_line: str) -> bool:
    if not activity_line or is_live_tv_foreground(activity_line):
        return False
    low = activity_line.lower()
    return any(marker.lower() in low for marker in SEARCH_UI_ACTIVITY_MARKERS)


def is_home_or_vod_foreground(activity_line: str) -> bool:
    if not activity_line or is_live_tv_foreground(activity_line):
        return False
    low = activity_line.lower()
    return any(marker.lower() in low for marker in HOME_SCREEN_ACTIVITY_MARKERS)


def press_live_tv_key(device, log_tag="[라이브 TV]") -> bool:
    """KEYCODE_TV — 홈/검색/런처에서 UplusMainActivity(라이브)로 복귀."""
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "keyevent", str(KEYCODE_TV)],
        capture_output=True,
    )
    time.sleep(min(DISMISS_UI_SETTLE_SEC, 0.6))
    act = get_resumed_activity_line(device)
    # dumpsys 빈 문자열은 성공으로 치지 않음 (이전엔 OK로 오인)
    ok = bool(act) and is_live_tv_foreground(act)
    term_print(
        f"{current_time_str()} {log_tag} {device} KEYCODE_TV(170)"
        f"{' OK' if ok else ' (미복귀)'} {_short_activity(act)}"
    )
    return ok


def force_live_then_ready_for_channel(device, log_tag="[비선형 TV]") -> bool:
    """
    not linear / 채널 전환 공통: Activity 만 보고 바로 라이브 복귀.
    UI dump·OCR·종료 탭 없이 KEYCODE_TV (검색만 BACK 선행).
    """
    act = get_resumed_activity_line(device)
    kind = screen_kind_from_activity(act)
    if kind == "live":
        return True
    if kind == "search":
        subprocess.run(
            ["adb", "-s", device, "shell", "input", "keyevent", str(KEYCODE_BACK)],
            capture_output=True,
        )
        term_print(f"{current_time_str()} {log_tag} {device} 검색 BACK")
        time.sleep(0.35)
    elif kind == "purchase":
        try_back_from_purchase_only(device, log_tag)
    if press_live_tv_key(device, log_tag):
        return True
    # 1회 재시도만 — 캡처/덤프 없이
    return press_live_tv_key(device, log_tag)


def _short_activity(act: str) -> str:
    if not act:
        return "(activity 없음)"
    if "{" in act:
        return act.split("{", 1)[-1].strip()[:120]
    return act[:120]


def _collect_ui_visible_texts(xml: str) -> list[str]:
    texts = []
    for frag in re.findall(r"<node\b([^>]*)/>", xml or ""):
        attrs = _parse_ui_node_attrs(frag)
        for key in ("text", "content-desc"):
            t = (attrs.get(key) or "").strip()
            if t and t not in texts:
                texts.append(t)
    return texts


def _ocr_screenshot_text(path: str) -> str:
    """전체 화면 OCR (불확실 화면 분류용). 실패 시 빈 문자열."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return ""
    try:
        try:
            from component.obs_capture import _configure_tesseract

            _configure_tesseract()
        except Exception:
            pass
        img = Image.open(path)
        w, h = img.size
        if w > 960:
            img = img.resize((960, max(1, int(h * 960 / w))))
        text = pytesseract.image_to_string(
            img, lang="kor+eng", config="--psm 6"
        )
        return (text or "").strip()
    except Exception:
        return ""


def _blob_has_any(blob: str, hints) -> bool:
    low = (blob or "").lower()
    return any(h.lower() in low for h in hints)


def screen_kind_from_activity(act: str):
    """Activity 로 확실한 종류만 반환. 불확실하면 None."""
    if not act:
        return None
    if is_live_tv_foreground(act):
        return "live"
    if is_search_ui_foreground(act):
        return "search"
    if is_blocking_purchase_or_dialog(act):
        return "purchase"
    if is_home_or_vod_foreground(act):
        return "home"
    return "unknown"


def diagnose_foreground_screen(device, *, reason: str = "불확실") -> dict:
    """
    스크린샷 + UI dump + OCR 로 현재 화면 분류.
    kind: live|home|search|purchase|dialog|unknown
    plan: none|keycode_tv|back|back_then_tv|tap_dismiss|purchase_back
    """
    act = get_resumed_activity_line(device)
    kind = screen_kind_from_activity(act) or "unknown"
    plan = "none"
    path = ""
    ocr = ""
    ui_texts: list[str] = []
    tap = None
    evidence = [f"activity={_short_activity(act)}"]

    try:
        path = capture_png_via_adb(
            device, adb_capture_path(LOG_DIR, "nonlinear_screen")
        )
        evidence.append(f"shot={path}")
    except Exception as e:
        evidence.append(f"shot_fail={e}")
        path = ""
    xml = dump_ui_hierarchy_xml(device)
    ui_texts = _collect_ui_visible_texts(xml)
    if ui_texts:
        evidence.append("ui=" + ",".join(ui_texts[:12]))
    if path:
        ocr = _ocr_screenshot_text(path)
        if ocr:
            evidence.append("ocr=" + re.sub(r"\s+", " ", ocr)[:160])
    blob = " ".join(ui_texts) + " " + ocr
    if kind == "unknown":
        if _blob_has_any(blob, SCREEN_OCR_SEARCH_HINTS):
            kind = "search"
        elif _blob_has_any(blob, SCREEN_OCR_PURCHASE_HINTS):
            kind = "purchase"
        elif _blob_has_any(blob, HOME_EXIT_BUTTON_LABELS) or _blob_has_any(
            blob, SCREEN_OCR_HOME_HINTS
        ):
            kind = "home"
        elif _blob_has_any(blob, SCREEN_OCR_DISMISS_HINTS):
            kind = "dialog"
    if xml:
        hit = find_exit_button_bounds(xml) or find_dismiss_button_bounds(
            xml, NON_LINEAR_TV_EXIT_LABELS + DISMISS_UI_BUTTON_LABELS
        )
        if hit:
            tap = hit

    if kind == "live":
        plan = "none"
    elif kind == "search":
        plan = "back_then_tv"
    elif kind == "purchase":
        plan = "purchase_back"
    elif kind == "dialog" and tap:
        plan = "tap_dismiss"
    elif kind == "home":
        plan = "tap_dismiss" if tap else "keycode_tv"
    else:
        # 모르는 화면: 숫자 키 금지, TV 키로 라이브 시도
        plan = "tap_dismiss" if tap else "keycode_tv"

    diagnosis = {
        "kind": kind,
        "plan": plan,
        "path": path,
        "activity": act,
        "tap": tap,
        "ocr": ocr,
        "ui_texts": ui_texts,
        "reason": reason,
        "evidence": evidence,
    }
    term_print(
        f"{current_time_str()} [화면분석] {device} ({reason}) "
        f"kind={kind} plan={plan} | {' | '.join(evidence[:4])}"
    )
    return diagnosis


def apply_screen_exit_plan(device, diagnosis: dict, log_tag="[화면복구]") -> bool:
    """diagnose_foreground_screen 결과대로 라이브 복귀 시도."""
    plan = (diagnosis or {}).get("plan") or "keycode_tv"
    tap = (diagnosis or {}).get("tap")
    if plan == "none":
        return bool(
            get_resumed_activity_line(device)
            and is_live_tv_foreground(get_resumed_activity_line(device))
        )
    if plan == "tap_dismiss" and tap:
        label, cx, cy = tap
        tap_screen(device, cx, cy)
        term_print(
            f"{current_time_str()} {log_tag} {device} '{label}' 탭 ({cx},{cy})"
        )
        time.sleep(DISMISS_UI_SETTLE_SEC)
        if is_live_tv_foreground(get_resumed_activity_line(device)):
            return True
        return press_live_tv_key(device, log_tag)
    if plan == "purchase_back":
        if try_back_from_purchase_only(device, log_tag):
            if is_live_tv_foreground(get_resumed_activity_line(device)):
                return True
        return press_live_tv_key(device, log_tag)
    if plan == "back_then_tv":
        subprocess.run(
            ["adb", "-s", device, "shell", "input", "keyevent", str(KEYCODE_BACK)],
            capture_output=True,
        )
        term_print(f"{current_time_str()} {log_tag} {device} BACK (검색/오버레이)")
        time.sleep(DISMISS_UI_SETTLE_SEC)
        if is_live_tv_foreground(get_resumed_activity_line(device)):
            return True
        return press_live_tv_key(device, log_tag)
    # keycode_tv (default)
    return press_live_tv_key(device, log_tag)


def recover_uncertain_screen_to_live(device, reason: str, log_tag="[화면복구]") -> bool:
    """Activity 가 애매하거나 TV 키가 실패했을 때: 캡처→분석→복구."""
    diag = diagnose_foreground_screen(device, reason=reason)
    if diag.get("kind") == "live":
        return True
    ok = apply_screen_exit_plan(device, diag, log_tag)
    if ok:
        return True
    # 1차 실패 시 한 번 더 캡처 후 TV 강제
    diag2 = diagnose_foreground_screen(device, reason=f"{reason}/재시도")
    if diag2.get("kind") == "live":
        return True
    return apply_screen_exit_plan(
        device, {**diag2, "plan": "keycode_tv"}, log_tag
    )


def dismiss_search_ui(device, log_tag="[검색]") -> bool:
    """검색 UI — BACK 후 필요 시 KEYCODE_TV (검색창에 채널 숫자 입력 방지)."""
    act = get_resumed_activity_line(device)
    if not is_search_ui_foreground(act):
        return False
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "keyevent", str(KEYCODE_BACK)],
        capture_output=True,
    )
    term_print(f"{current_time_str()} {log_tag} {device} 검색 UI BACK")
    time.sleep(DISMISS_UI_SETTLE_SEC)
    act = get_resumed_activity_line(device)
    if is_live_tv_foreground(act):
        return True
    if is_search_ui_foreground(act) or is_home_or_vod_foreground(act):
        return press_live_tv_key(device, log_tag)
    return press_live_tv_key(device, log_tag)


def dump_ui_hierarchy_xml(device) -> str:
    try:
        subprocess.run(
            [
                "adb",
                "-s",
                device,
                "shell",
                "uiautomator",
                "dump",
                UI_DUMP_REMOTE_PATH,
            ],
            capture_output=True,
            timeout=45,
        )
        r = subprocess.run(
            ["adb", "-s", device, "shell", "cat", UI_DUMP_REMOTE_PATH],
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ""
    return (r.stdout or b"").decode("utf-8", errors="replace")


def _parse_ui_node_attrs(fragment: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r'(\w+)="([^"]*)"', fragment)}


def _bounds_center(bounds_str: str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
    return (x1 + x2) // 2, (y1 + y2) // 2


def find_dismiss_button_bounds(xml: str, labels):
    """labels 순서대로 첫 매칭 버튼의 (라벨, center_x, center_y) 반환."""
    hits = {}
    cancel_rid_markers = ("cancel_btn", "btn_cancel", "password_2_input_btn_cancel")
    for frag in re.findall(r"<node\b([^>]*)/>", xml):
        attrs = _parse_ui_node_attrs(frag)
        text = (attrs.get("text") or "").strip()
        if text not in labels:
            continue
        rid = (attrs.get("resource-id") or "").lower()
        tappable = (
            attrs.get("clickable") == "true"
            or attrs.get("focusable") == "true"
            or any(m in rid for m in cancel_rid_markers)
        )
        if not tappable:
            continue
        center = _bounds_center(attrs.get("bounds", ""))
        if center:
            hits.setdefault(text, center)
    for label in labels:
        if label in hits:
            cx, cy = hits[label]
            return label, cx, cy
    return None


def find_exit_button_bounds(xml: str):
    """홈/VOD — '종료'·exit 라벨·resource-id 우선 (BACK 대신 사용)."""
    if not xml:
        return None
    hit = find_dismiss_button_bounds(xml, HOME_EXIT_BUTTON_LABELS)
    if hit:
        return hit
    cancel_rid_markers = ("cancel_btn", "btn_cancel")
    best = None
    for frag in re.findall(r"<node\b([^>]*)/>", xml):
        attrs = _parse_ui_node_attrs(frag)
        text = (attrs.get("text") or "").strip()
        desc = (attrs.get("content-desc") or "").strip()
        rid = (attrs.get("resource-id") or "").lower()
        blob = f"{text} {desc} {rid}".lower()
        if "종료" not in blob and "exit" not in blob and not any(
            m in rid for m in HOME_EXIT_RID_MARKERS
        ):
            continue
        tappable = (
            attrs.get("clickable") == "true"
            or attrs.get("focusable") == "true"
            or any(m in rid for m in cancel_rid_markers + HOME_EXIT_RID_MARKERS)
        )
        if not tappable:
            continue
        center = _bounds_center(attrs.get("bounds", ""))
        if center:
            label = text or desc or "종료(exit)"
            best = (label, center[0], center[1])
            if text in HOME_EXIT_BUTTON_LABELS or text.lower() == "exit":
                return best
    return best


def try_exit_from_home_ui(device, log_tag="[홈]") -> bool:
    """홈/검색/비라이브 → KEYCODE_TV (UI dump·종료 탭 스킵, 즉시 복귀)."""
    return force_live_then_ready_for_channel(device, log_tag)


def tap_screen(device, x, y):
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "tap", str(int(x)), str(int(y))],
        capture_output=True,
    )


def try_back_from_purchase_only(device, log_tag="[UI 해제]") -> bool:
    """PurchaseActivity/PassCheck 만 BACK — 라이브 TV 에서는 BACK 금지(홈 유발)."""
    act = get_resumed_activity_line(device)
    if not is_blocking_purchase_or_dialog(act):
        return False
    subprocess.run(
        ["adb", "-s", device, "shell", "input", "keyevent", str(KEYCODE_BACK)],
        capture_output=True,
    )
    term_print(f"{current_time_str()} {log_tag} {device} 유료가입 화면 BACK")
    time.sleep(DISMISS_UI_SETTLE_SEC)
    return True


def is_channel_keypad_safe(device) -> bool:
    """숫자 키패드가 라이브 튜닝으로 들어가도 되는 포그라운드인지."""
    act = get_resumed_activity_line(device)
    if not act:
        # dumpsys 실패 시 보수적으로 불가로 — 잘못된 UI에 숫자 입력 방지
        return False
    if is_blocking_purchase_or_dialog(act):
        return False
    if is_search_ui_foreground(act) or is_home_or_vod_foreground(act):
        return False
    return is_live_tv_foreground(act)


def prepare_channel_keypad_foreground(devices) -> bool:
    """유료가입·홈/VOD·검색을 닫고 라이브 포그라운드일 때만 채널 번호 입력."""
    rounds = max(DISMISS_UI_MAX_ROUNDS, 5)
    for _ in range(rounds):
        if all(is_channel_keypad_safe(device) for device in devices):
            return True
        dismiss_blocking_purchase_ui(devices)
        ensure_live_tv_foreground(devices)
        time.sleep(0.25)
    safe = [d for d in devices if is_channel_keypad_safe(d)]
    if safe != devices:
        blocked = [d for d in devices if not is_channel_keypad_safe(d)]
        term_print(
            f"{current_time_str()} [채널 전환] 키패드 입력 불가 — "
            f"{', '.join(blocked)} (유료가입/홈/VOD/검색/비라이브)"
        )
    return len(safe) == len(devices)


def dismiss_blocking_purchase_ui(devices):
    """유료가입·구매 비밀번호 UI가 떠 있으면 나가기/취소로 닫고 라이브 TV로 복귀."""
    labels = DISMISS_UI_BUTTON_LABELS
    for device in devices:
        prev_act = ""
        for _ in range(DISMISS_UI_MAX_ROUNDS):
            act = get_resumed_activity_line(device)
            if not is_blocking_purchase_or_dialog(act):
                break
            short = act
            if "{" in act:
                short = act.split("{", 1)[-1].strip()[:96]
            term_print(
                f"{current_time_str()} [UI 해제] {device} 차단 화면 — {short}"
            )
            xml = dump_ui_hierarchy_xml(device)
            hit = find_dismiss_button_bounds(xml, labels) if xml else None
            if hit:
                label, cx, cy = hit
                tap_screen(device, cx, cy)
                term_print(
                    f"{current_time_str()} [UI 해제] '{label}' 탭 ({cx},{cy})"
                )
                time.sleep(DISMISS_UI_SETTLE_SEC)
                if act == prev_act and is_blocking_purchase_or_dialog(
                    get_resumed_activity_line(device)
                ):
                    if try_back_from_purchase_only(device):
                        prev_act = ""
                        continue
                prev_act = act
                continue
            if try_back_from_purchase_only(device):
                prev_act = ""
                continue
            if try_exit_from_home_ui(device, "[UI 해제]"):
                continue
            term_print(
                f"{current_time_str()} [UI 해제] {device} 닫기 버튼 없음 "
                f"(라이브 TV 아님 — 채널 번호 입력 보류)"
            )
            break


def ensure_live_tv_foreground(devices):
    """채널 전환 전: Activity 확인 → KEYCODE_TV → (유료가입만) 나가기. OCR/덤프 없음."""
    for device in devices:
        force_live_then_ready_for_channel(device, "[홈]")
    dismiss_blocking_purchase_ui(devices)
    for device in devices:
        act = get_resumed_activity_line(device)
        if screen_kind_from_activity(act) != "live":
            press_live_tv_key(device, "[홈]")


def _escape_channel_from_env():
    raw = os.environ.get("STB_ESCAPE_CHANNEL", DEFAULT_ESCAPE_CHANNEL).strip()
    return normalize_channel_number(raw) or raw


def _channel_target_or_none(channel_number):
    target = normalize_channel_number(channel_number)
    return target if target != "" else None


def recover_from_non_linear_tv_state(
    devices, retune_channel=None, escape_channel=None
):
    """
    not linear tv state → Activity 확인 후 KEYCODE_TV, 곧바로 채널 번호 진입.
    (종료 탭 / UI dump / OCR 로 시간 쓰지 않음)
    """
    env_esc = _escape_channel_from_env()
    retune_target = _channel_target_or_none(retune_channel)
    escape_target = _channel_target_or_none(escape_channel)
    target = retune_target or escape_target or env_esc
    for device in devices:
        act = get_resumed_activity_line(device)
        kind = screen_kind_from_activity(act) or "unknown"
        term_print(
            f"{current_time_str()} [비선형 TV] {device} kind={kind} "
            f"({_short_activity(act)}) → TV키 후 즉시 ch {target or '?'}"
        )
        force_live_then_ready_for_channel(device, "[비선형 TV]")

    if target:
        src = (
            "광고채널 retune"
            if retune_target is not None and str(target) == str(retune_target)
            else "이탈채널 escape"
        )
        term_print(
            f"{current_time_str()} [비선형 TV] 채널 진입 — ch {target} "
            f"({src}, 키패드 '{format_channel_keypad_digits(target)}')"
        )
        switch_channel_with_verify(target, devices, clear_buffer=False)
    else:
        term_print(
            f"{current_time_str()} [비선형 TV] 복귀 채널 없음 — TV키만 수행"
        )
    # 라이브로 돌아온 뒤 SDK(name) 등 S/W ver. 재확인 (비선형 중 미출력 보완)
    maybe_retry_sw_versions_after_live(devices)


def _maybe_recover_non_linear_tv(device, line):
    lower = line.lower()
    if NOT_LINEAR_TV_STATE_NEEDLE not in lower:
        return
    with _non_linear_tv_recovery_lock:
        last = _non_linear_tv_recovery_at.get(device, 0)
        if time.time() - last < NON_LINEAR_TV_RECOVERY_COOLDOWN_SEC:
            return
        _non_linear_tv_recovery_at[device] = time.time()

    retune = _pending_tune_target(device)
    if not retune:
        retune = _google_tune_target(device)
    if not retune:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
            if tracker:
                if time.time() < tracker.started_at + NON_LINEAR_TV_GRACE_AFTER_TUNE_SEC:
                    return
                retune = tracker.channel_number

    snippet = line.strip()[:220]
    term_print(
        f"{current_time_str()} [{device}] [비선형 TV] {snippet} "
        f"→ 종료(exit) 탭 / 채널 전환"
    )

    def _run():
        recover_from_non_linear_tv_state(
            [device],
            retune_channel=retune,
            escape_channel=_escape_channel_from_env(),
        )

    threading.Thread(target=_run, daemon=True).start()


def send_ad_sync_broadcast(device: str) -> bool:
    """LGU: am broadcast -a tv.anypoint.sdk.AD_SYNC -p tv.anypoint.uplus.tvg.app"""
    cmd = [
        "adb",
        "-s",
        device,
        "shell",
        "am",
        "broadcast",
        "-a",
        AD_SYNC_BROADCAST_ACTION,
    ]
    if AD_SYNC_LGU_PACKAGE:
        cmd.extend(["-p", AD_SYNC_LGU_PACKAGE])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        term_print(f"{current_time_str()} [{device}] [AD_SYNC] 전송 실패: {e}")
        return False
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:200]
        term_print(f"{current_time_str()} [{device}] [AD_SYNC] 실패 (rc={result.returncode}): {err}")
        return False
    out = (result.stdout or "").strip()
    if out:
        term_print(f"{current_time_str()} [{device}] [AD_SYNC] {out[:200]}")
    return True


def _maybe_recover_ad_not_ready(device, line):
    if NOT_READY_TO_PLAY_AD_NEEDLE not in line.lower():
        return
    with _ad_sync_recovery_lock:
        last = _ad_sync_recovery_at.get(device, 0)
        elapsed = time.time() - last
        if elapsed < AD_SYNC_RECOVERY_COOLDOWN_SEC:
            term_print(
                f"{current_time_str()} [{device}] [광고 미준비] AD_SYNC 쿨다운 "
                f"({int(elapsed)}/{AD_SYNC_RECOVERY_COOLDOWN_SEC}초) — 스킵"
            )
            return
        _ad_sync_recovery_at[device] = time.time()

    snippet = line.strip()[:220]
    term_print(
        f"{current_time_str()} [{device}] [광고 미준비] {snippet} → AD_SYNC 전송"
    )

    def _run():
        send_ad_sync_broadcast(device)

    threading.Thread(target=_run, daemon=True).start()


def read_last_tuned_channel_number(device) -> str | None:
    """LGU CM extraData 우선, 없으면 getOsdChannel 로그."""
    try:
        r = subprocess.run(
            ["adb", "-s", device, "logcat", "-d", "-t", "600"],
            capture_output=True,
            timeout=25,
        )
    except subprocess.TimeoutExpired:
        return None
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    cm_hits = LGU_CM_TUNED_CHANNEL_PATTERN.findall(out)
    if cm_hits:
        return cm_hits[-1]
    osd_hits = OSD_CHANNEL_LOG_PATTERN.findall(out)
    return osd_hits[-1] if osd_hits else None


def read_last_osd_channel_number(device) -> str | None:
    return read_last_tuned_channel_number(device)


def read_recent_program_channel_ids(device) -> list[int]:
    """최근 logcat 에서 ProgramProviderChannel.id / register cue ppId."""
    try:
        r = subprocess.run(
            ["adb", "-s", device, "logcat", "-d", "-t", "500"],
            capture_output=True,
            timeout=25,
        )
    except subprocess.TimeoutExpired:
        return []
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    ids: list[int] = []
    for line in out.splitlines():
        cid = parse_program_provider_channel_id(line)
        if cid is not None:
            ids.append(cid)
        pp_id = parse_register_cue_pp_id(line)
        if pp_id is not None:
            ids.append(pp_id)
    return ids


def channel_tune_confirmed(
    device, target_ch, expected_catalog_ids=None
) -> tuple[bool, str]:
    """CM/OSD 또는 편성 일치 register cue 로 튜닝 확인."""
    target = str(normalize_channel_number(target_ch) or target_ch)
    tuned = read_last_tuned_channel_number(device)
    if tuned and str(tuned) == target:
        return True, "CM/OSD"

    if TUNE_FALLBACK_CATALOG_CUE and expected_catalog_ids:
        ids = read_recent_program_channel_ids(device)
        if ids and cue_id_matches_slot(ids[-1], expected_catalog_ids):
            return True, f"catalog id={ids[-1]}"
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if tracker and (
            tracker.register_cue_seen or tracker.cue_ready or tracker.cue_count
        ):
            pid = tracker.program_channel_id
            if pid and cue_id_matches_slot(pid, expected_catalog_ids):
                return True, f"register cue id={pid}"

    return False, ""


def _catalog_id_tune_mismatch(devices, expected_ids: set[int]) -> bool:
    """최근 로그에 카탈로그 id 가 보이는데 기대와 다르면 True."""
    if not expected_ids:
        return False
    for device in devices:
        ids = read_recent_program_channel_ids(device)
        if not ids:
            continue
        latest = ids[-1]
        if not cue_id_matches_slot(latest, expected_ids):
            term_print(
                f"{current_time_str()} [튜닝 확인] {device} 카탈로그 id={latest} "
                f"(기대 {', '.join(format_channel_ref(i) for i in sorted(expected_ids))})"
            )
            return True
    return False


def wait_for_tuned_channels(
    devices, target_ch, timeout_sec=None, expected_catalog_ids=None
) -> dict:
    """튜닝 후 CM extraData / OSD / (fallback) catalog cue 일치할 때까지 폴링."""
    target = str(normalize_channel_number(target_ch) or target_ch)
    if expected_catalog_ids is not None:
        expected_catalog_ids = set(expected_catalog_ids)
    timeout = timeout_sec if timeout_sec is not None else OSD_CHANNEL_POLL_TIMEOUT_SEC
    deadline = time.time() + timeout
    last = {}
    confirm_via = {}
    last_preload = 0.0
    while time.time() < deadline:
        all_ok = True
        for device in devices:
            if is_blocking_purchase_or_dialog(get_resumed_activity_line(device)):
                all_ok = False
                last[device] = None
                continue
            ok, via = channel_tune_confirmed(device, target, expected_catalog_ids)
            if ok:
                last[device] = target
                confirm_via[device] = via
                continue
            tuned = read_last_tuned_channel_number(device)
            last[device] = tuned
            if tuned is None or str(tuned) != target:
                all_ok = False
        if all_ok and devices:
            for device in devices:
                via = confirm_via.get(device, "CM/OSD")
                if via != "CM/OSD" or last.get(device) == target:
                    pass  # logged at switch_channel_with_verify
            return last
        now = time.time()
        if now - last_preload >= 2.0:
            with _ad_tracker_lock:
                has_trackers = any(d in _active_ad_trackers for d in devices)
            if has_trackers:
                preload_ad_logcat_buffer(devices, max_lines=1500)
            last_preload = now
        time.sleep(OSD_CHANNEL_POLL_INTERVAL_SEC)
    return last


def wait_for_osd_channels(devices, target_ch, timeout_sec=None) -> dict:
    return wait_for_tuned_channels(devices, target_ch, timeout_sec)


def format_channel_keypad_digits(channel_number) -> str:
    """stb_multi_gspread_sheet_read 와 동일: str(채널번호) 그대로."""
    return str(normalize_channel_number(channel_number) or channel_number).strip()


def _channel_keypad_prefix_conflicts_escape(channel_str: str, escape_channel) -> bool:
    esc = str(normalize_channel_number(escape_channel) or escape_channel or "").strip()
    if not esc or len(channel_str) <= 1:
        return False
    return channel_str.startswith(esc)


def _channel_keypad_use_burst(channel_str: str, escape_channel=None) -> bool:
    """2자리 이상은 연속 입력 — 선행 자리만 튜닝(ch48→8)되는 단말 방지."""
    if len(channel_str) >= 2:
        return True
    return False


def _channel_keypad_needs_ok(channel_str: str) -> bool:
    if len(channel_str) >= 3:
        return CHANNEL_OK_FOR_3DIGIT
    return True


def _input_channel_multi_style(device, channel_str: str, escape_channel=None):
    """LGU STB: 3자리(322)는 빠른 연속 입력. 이탈 ch3 → 322 시 선행 3이 ch3으로 확정되지 않게."""
    channel_str = str(channel_str).strip()
    burst = _channel_keypad_use_burst(channel_str, escape_channel)
    digit_delay = (
        MULTI_CHANNEL_BURST_DELAY_SEC if burst else MULTI_CHANNEL_DIGIT_DELAY_SEC
    )
    if burst:
        note = f"연속 입력({digit_delay:.2f}s/자리)"
        if _channel_keypad_prefix_conflicts_escape(channel_str, escape_channel):
            note += f", 이탈 ch {escape_channel} 선행자리 겹침"
        if not _channel_keypad_needs_ok(channel_str):
            note += ", OK 생략"
        term_print(
            f"{current_time_str()} [채널 전환] ch {channel_str} — {note}"
        )
    for digit in channel_str:
        if digit in keyevent_map:
            subprocess.run(
                [
                    "adb",
                    "-s",
                    device,
                    "shell",
                    "input",
                    "keyevent",
                    str(keyevent_map[digit]),
                ],
                capture_output=True,
            )
            time.sleep(digit_delay)
    if _channel_keypad_needs_ok(channel_str):
        if len(channel_str) <= 2:
            time.sleep(MULTI_CHANNEL_DIGIT_DELAY_SEC)
        else:
            time.sleep(MULTI_CHANNEL_OK_DELAY_SEC)
        subprocess.run(
            [
                "adb",
                "-s",
                device,
                "shell",
                "input",
                "keyevent",
                str(KEYCODE_DPAD_CENTER),
            ],
            capture_output=True,
        )


def _wait_channel_keypad_ready(devices):
    """직전 튜닝·OSD 잔여 후 숫자 키가 먹을 때까지 대기."""
    delay = CHANNEL_KEYPAD_READY_SEC
    if delay > 0:
        time.sleep(delay)


def switch_channel_via_adb(
    channel_number, devices, clear_buffer=True, escape_channel=None
) -> bool:
    """stb_multi_gspread_sheet_read.switch_channel_via_adb 와 동일 (검증 없음)."""
    channel_str = format_channel_keypad_digits(channel_number)
    if escape_channel is None:
        escape_channel = _escape_channel_from_env()
    _wait_channel_keypad_ready(devices)
    if clear_buffer:
        clear_logcat_buffers(devices)
    mode = (
        f"burst {MULTI_CHANNEL_BURST_DELAY_SEC}s"
        if _channel_keypad_use_burst(channel_str, escape_channel)
        else f"숫자 {MULTI_CHANNEL_DIGIT_DELAY_SEC}s, OK"
    )
    term_print(
        f"{current_time_str()} [채널 전환] ch {channel_str} (multi 방식: {mode})"
    )
    with _channel_switch_lock:
        threads = []
        for device in devices:
            t = threading.Thread(
                target=_input_channel_multi_style,
                args=(device, channel_str),
                kwargs={"escape_channel": escape_channel},
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
    return True


class AdPlaybackTracker:
    """채널 전환 후 한 편성(slot) 동안의 광고 재생 logcat 추적."""

    def __init__(
        self,
        device,
        channel_name,
        channel_number,
        ad_time_str,
        expected_catalog_ids=None,
        log_lookback_sec=None,
    ):
        self.device = device
        self.channel_name = channel_name
        self.channel_number = channel_number
        self.ad_time_str = ad_time_str
        self.expected_catalog_ids = set(expected_catalog_ids or [])
        lookback = (
            int(log_lookback_sec)
            if log_lookback_sec is not None
            else AD_LOG_TRUST_LOOKBACK_SEC
        )
        self.started_at = time.time()
        self.phases = {key: False for key, _, _ in AD_PLAYBACK_PHASES}
        self.impression_count = 0
        self.cue_count = 0
        self.last_play_time_ms = None
        self.play_times_ms = []
        self.slot_play_times_ms = []
        self.play_times_after_leave_ms = []
        self.collecting_after_leave = False
        self.cue_duration_ms = None
        self.impression_api_ok = False
        self._seen_impression_keys = set()
        self._seen_impression_size_keys = set()
        self._logged_cue_keys = set()
        self.impression_batch_remaining = 0
        self._printed_phases = set()
        self.cue_ready = False
        self.register_cue_seen = False
        self.register_cue_at = None
        self.scheduled_play_at = None
        self.first_play_logcat_at = None
        self.last_stop_logcat_at = None
        self.program_channel_id = None
        self.play_trust_after = time.time() + AD_PLAYBACK_LOG_GRACE_SEC
        self._slot_trust_floor = datetime.now()
        self.log_trust_not_before = self._slot_trust_floor

    def _slot_trust_cutoff(self) -> datetime:
        """이 슬롯 watch 시작 이전 logcat 은 preload·live 모두 제외."""
        grace = datetime.fromtimestamp(self.started_at) - timedelta(seconds=3)
        return max(self._slot_trust_floor, grace)

    def line_is_trusted(self, line, require_timestamp=False):
        cutoff = self._slot_trust_cutoff()
        log_dt = parse_logcat_line_datetime(line)
        if log_dt is not None:
            return log_dt >= max(self.log_trust_not_before, cutoff)
        if require_timestamp:
            return False
        return time.time() >= self.started_at

    def _slot_cue_match(self, cue_channel_id: int | None) -> bool:
        return cue_id_matches_slot(cue_channel_id, self.expected_catalog_ids)

    def _capture_cue_duration(self, line: str) -> int | None:
        dur = parse_cue_duration_ms(line)
        if dur is not None and dur > 0:
            self.cue_duration_ms = dur
        return dur

    def expected_playtime_ms(self) -> int:
        if self.cue_duration_ms and self.cue_duration_ms > 0:
            return self.cue_duration_ms
        return EXPECTED_AD_PLAYTIME_MS

    def _note_play_logcat_time(self, play_dt):
        """player play 시각 — 새 슬롯이면 stop 시각·phase 초기화."""
        if play_dt is None:
            return
        if (
            self.first_play_logcat_at is not None
            and play_dt <= self.first_play_logcat_at
        ):
            return
        self.first_play_logcat_at = play_dt
        self.last_stop_logcat_at = None
        self.phases["player_stop"] = False
        self.phases["on_stopped"] = False

    def _note_stop_logcat_time(self, stop_dt):
        """player stop ==== 시각 — play 이후 가장 늦은 stop 유지 (phase 무관)."""
        if stop_dt is None or self.first_play_logcat_at is None:
            return
        if stop_dt < self.first_play_logcat_at:
            return
        if (
            self.last_stop_logcat_at is None
            or stop_dt > self.last_stop_logcat_at
        ):
            self.last_stop_logcat_at = stop_dt

    def process_line(self, line):
        stripped = line.rstrip()
        if not self.line_is_trusted(stripped):
            return
        lower = stripped.lower()

        if NOT_LINEAR_TV_STATE_NEEDLE in lower:
            _maybe_recover_non_linear_tv(self.device, stripped)

        if "ads will play in" in lower:
            delay_match = ADS_WILL_PLAY_IN_MS_RE.search(stripped)
            if delay_match:
                delay_sec = int(delay_match.group(1)) / 1000.0
                self.scheduled_play_at = time.time() + delay_sec
        if "register cue:" in lower:
            pp_id = parse_register_cue_pp_id(stripped)
            if pp_id is not None and self._slot_cue_match(pp_id):
                cue_dur = self._capture_cue_duration(stripped)
                self.register_cue_seen = True
                if self.register_cue_at is None:
                    self.register_cue_at = time.time()
                self.cue_ready = True
                self.cue_count += 1
                self.program_channel_id = pp_id
                cue_key = f"register:{pp_id}"
                if cue_key not in self._logged_cue_keys:
                    self._logged_cue_keys.add(cue_key)
                    dur_note = (
                        f" duration={cue_dur}ms ({cue_dur / 1000:.1f}초)"
                        if cue_dur
                        else ""
                    )
                    term_print(
                        f"{current_time_str()} [{self.device}] [Cue] register "
                        f"(편성 일치) {format_channel_ref(pp_id)}{dur_note}"
                    )
        elif "receive cue" in lower:
            ch_id = parse_program_provider_channel_id(stripped)
            if ch_id is not None and self._slot_cue_match(ch_id):
                cue_dur = self._capture_cue_duration(stripped)
                self.cue_ready = True
                self.cue_count += 1
                self.program_channel_id = ch_id
                cue_key = f"receive:{ch_id}"
                if cue_key not in self._logged_cue_keys:
                    self._logged_cue_keys.add(cue_key)
                    dur_note = (
                        f" duration={cue_dur}ms ({cue_dur / 1000:.1f}초)"
                        if cue_dur
                        else ""
                    )
                    term_print(
                        f"{current_time_str()} [{self.device}] [Cue] receive "
                        f"(편성 일치) {format_channel_ref(ch_id)}{dur_note}"
                    )

        if _is_player_play_logcat_line(stripped) and time.time() >= self.play_trust_after:
            self._note_play_logcat_time(parse_logcat_line_datetime(stripped))
            if not self.phases.get("player_play"):
                self.slot_play_times_ms = []

        if _is_player_stop_logcat_line(stripped):
            self._note_stop_logcat_time(parse_logcat_line_datetime(stripped))

        for phase_key, needle, label in AD_PLAYBACK_PHASES:
            if self.phases[phase_key] or needle.lower() not in lower:
                continue
            if phase_key == "cue_register":
                pp_id = parse_register_cue_pp_id(stripped)
                if pp_id is not None and not self._slot_cue_match(pp_id):
                    continue
            if phase_key in ("play_start", "player_play"):
                if time.time() < self.play_trust_after:
                    continue
                # 이전 광고 stop/onStopped 가 새 키즈 슬롯에 남으면 OCR play 대기 루프가
                # 즉시 종료된다. 새 play 계열 로그를 신뢰하는 순간 stale stop 을 제거.
                self.phases["player_stop"] = False
                self.phases["on_stopped"] = False
                self.last_stop_logcat_at = None
            if phase_key in ("player_stop", "on_stopped"):
                stop_dt = parse_logcat_line_datetime(stripped)
                if self.first_play_logcat_at is None:
                    continue
                if stop_dt is not None and stop_dt < self.first_play_logcat_at:
                    continue
            if phase_key == "impression_detail":
                if not _is_impression_batch_playtime_line(stripped):
                    continue
            if phase_key == "impression_post":
                if "--> post" not in lower and "post https" not in lower:
                    continue
            self.phases[phase_key] = True
            if phase_key not in self._printed_phases:
                self._printed_phases.add(phase_key)
                term_print(f"{current_time_str()} [{self.device}] [광고] {label}")
                term_print(f"  {stripped[:200]}{'…' if len(stripped) > 200 else ''}")
        if "impression log size" in lower:
            size_m = IMPRESSION_LOG_SIZE_RE.search(stripped)
            if size_m:
                batch_key = impression_log_size_batch_key(stripped)
                if batch_key and batch_key not in self._seen_impression_size_keys:
                    self._seen_impression_size_keys.add(batch_key)
                    self.impression_batch_remaining = int(size_m.group(1))
            self.impression_count += 1
            if not self.collecting_after_leave:
                self.slot_play_times_ms = []

        play_ms = parse_impression_play_time_ms(stripped)
        if play_ms is not None and _is_impression_batch_playtime_line(stripped):
            if self.impression_batch_remaining <= 0:
                return
            dedupe_key = impression_log_dedupe_key(stripped) or stripped
            if dedupe_key in self._seen_impression_keys:
                return
            self._seen_impression_keys.add(dedupe_key)
            self.impression_batch_remaining -= 1
            self.last_play_time_ms = play_ms
            if self.collecting_after_leave:
                self.play_times_after_leave_ms.append(play_ms)
            elif self.phases.get("player_play") or self.phases.get("play_start"):
                self.slot_play_times_ms.append(play_ms)
            self.impression_count += 1
            sec = play_ms / 1000
            if self.collecting_after_leave:
                leave_sum = sum(self.play_times_after_leave_ms)
                count = len(self.play_times_after_leave_ms)
                sum_note = (
                    f"이탈 impression 누적 {leave_sum}ms ({count}건)"
                )
            else:
                slot_sum = sum(self.slot_play_times_ms)
                count = len(self.slot_play_times_ms)
                sum_note = f"슬롯 누적 {slot_sum}ms ({count}건)"
            term_print(
                f"{current_time_str()} [{self.device}] [광고] playTime={play_ms}ms "
                f"({sec:.1f}초) — {sum_note}"
            )

        if "impression-logs" in lower and "<-- 200" in lower:
            if not self.impression_api_ok:
                self.impression_api_ok = True
                term_print(
                    f"{current_time_str()} [{self.device}] [광고] impression API 200 OK"
                )

    def evaluation_playtime_ms(self):
        """체크 2: 현재 편성(마지막 impression 배치) playTime 합만."""
        return sum(self.slot_play_times_ms)

    def is_complete(self, expected_impressions=DEFAULT_EXPECTED_IMPRESSIONS):
        played = self.phases["play_start"] or self.phases["player_play"]
        stopped = self.phases["player_stop"] or self.phases["on_stopped"]
        impressed = self.impression_count >= expected_impressions
        return played and stopped and impressed and self.impression_api_ok

    def missing_summary(self, expected_impressions=DEFAULT_EXPECTED_IMPRESSIONS):
        missing = []
        if not (self.phases["play_start"] or self.phases["player_play"]):
            missing.append("재생 시작")
        if not (self.phases["player_stop"] or self.phases["on_stopped"]):
            missing.append("재생 종료")
        if self.impression_count < expected_impressions:
            missing.append(
                f"impression ({self.impression_count}/{expected_impressions})"
            )
        if not self.impression_api_ok:
            missing.append("impression API 200")
        return missing


def _backfill_player_stop_from_logcat(devices, max_lines=None):
    """체크 4: player stop ==== 이 버퍼 밖으로 밀렸을 때 logcat 덤프로 보강."""
    max_lines = max_lines or CHECK4_POST_LEAVE_LOG_MAX_LINES
    trust_from = datetime.now() - timedelta(seconds=CHECK4_POST_LEAVE_LOG_LOOKBACK_SEC)
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker or not tracker.first_play_logcat_at:
            continue
        try:
            dump = subprocess.run(
                [
                    "adb",
                    "-s",
                    device,
                    "logcat",
                    "-d",
                    "-v",
                    "time",
                    "-t",
                    str(max_lines),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=45,
            )
            if not dump.stdout:
                continue
            saved_trust = tracker.log_trust_not_before
            tracker.log_trust_not_before = trust_from
            for ln in dump.stdout.splitlines():
                if not ln:
                    continue
                if not _is_player_stop_logcat_line(ln):
                    continue
                if tracker.line_is_trusted(ln, require_timestamp=True):
                    tracker._note_stop_logcat_time(
                        parse_logcat_line_datetime(ln)
                    )
            tracker.log_trust_not_before = max(saved_trust, trust_from)
        except Exception:
            continue


def preload_ad_logcat_buffer(devices, lookback_sec=AD_LOG_TRUST_LOOKBACK_SEC, max_lines=800):
    """logcat 버퍼에서 최근 줄을 tracker 에 반영 (슬롯 시작 이후만)."""
    for device in devices:
        try:
            dump = subprocess.run(
                [
                    "adb",
                    "-s",
                    device,
                    "logcat",
                    "-d",
                    "-v",
                    "time",
                    "-t",
                    str(max_lines),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=45,
            )
            if not dump.stdout:
                continue
            with _ad_tracker_lock:
                tracker = _active_ad_trackers.get(device)
            if not tracker:
                continue
            saved_trust = tracker.log_trust_not_before
            trust_from = max(
                datetime.now() - timedelta(seconds=lookback_sec),
                tracker._slot_trust_cutoff(),
            )
            tracker.log_trust_not_before = trust_from
            for ln in dump.stdout.splitlines()[-max_lines:]:
                if ln and tracker.line_is_trusted(ln, require_timestamp=True):
                    tracker.process_line(ln)
            tracker.log_trust_not_before = max(saved_trust, trust_from)
        except Exception:
            continue


def start_ad_playback_watch(
    devices,
    channel_name,
    channel_number,
    ad_time_str,
    announce=True,
    preload_buffer=True,
    expected_catalog_ids=None,
    log_lookback_sec=None,
):
    if expected_catalog_ids is None:
        expected_catalog_ids = resolve_expected_catalog_ids(channel_name)
    else:
        expected_catalog_ids = set(expected_catalog_ids)
    with _ad_tracker_lock:
        for device in devices:
            _active_ad_trackers[device] = AdPlaybackTracker(
                device,
                channel_name,
                channel_number,
                ad_time_str,
                expected_catalog_ids=expected_catalog_ids,
                log_lookback_sec=log_lookback_sec,
            )
    if announce:
        term_print(
            f"\n{current_time_str()} [광고 재생 확인] "
            f"{channel_name}({channel_number}) @ {ad_time_str}"
        )
    if preload_buffer:
        lookback = (
            int(log_lookback_sec)
            if log_lookback_sec is not None
            else CHECK2_LOG_LOOKBACK_SEC
        )
        preload_ad_logcat_buffer(devices, lookback_sec=lookback, max_lines=2000)


def preload_kids_watermark_buffer(devices, lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC):
    trust_from = datetime.now() - timedelta(seconds=lookback_sec)
    for device in devices:
        try:
            dump = subprocess.run(
                [
                    "adb",
                    "-s",
                    device,
                    "logcat",
                    "-d",
                    "-v",
                    "time",
                    "-t",
                    "2000",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=45,
            )
            if not dump.stdout:
                continue
            with _kids_watermark_lock:
                tracker = _active_kids_watermark_trackers.get(device)
            if not tracker:
                continue
            saved = tracker.log_trust_not_before
            tracker.log_trust_not_before = trust_from
            for ln in dump.stdout.splitlines()[-2000:]:
                if ln and tracker.line_is_trusted(ln, require_timestamp=True):
                    tracker.process_line(ln)
            tracker.log_trust_not_before = max(saved, trust_from)
        except Exception:
            continue


class KidsWatermarkTracker:
    """키즈 채널 시청 중 kid=true Cue · KidWatermarkManager · kid_watermark.png 확인."""

    def __init__(self, device, channel_number, channel_name, expected_catalog_ids=None):
        self.device = device
        self.channel_number = normalize_channel_number(channel_number)
        self.channel_name = channel_name
        self.expected_catalog_ids = set(expected_catalog_ids or [])
        self.phases = {k: False for k in KIDS_WATERMARK_PHASE_LABELS}
        self._printed_phases = set()
        self.started_at = time.time()
        self.program_channel_id = None
        self.log_trust_not_before = datetime.now() - timedelta(
            seconds=AD_LOG_TRUST_LOOKBACK_SEC
        )

    def line_is_trusted(self, line, require_timestamp=False):
        log_dt = parse_logcat_line_datetime(line)
        if log_dt is not None:
            return log_dt >= self.log_trust_not_before
        if require_timestamp:
            return False
        return time.time() >= self.started_at

    def _mark_phase(self, phase_key, stripped):
        if self.phases.get(phase_key):
            return
        if not self.line_is_trusted(stripped):
            return
        if phase_key == "kid_cue":
            ch_id = parse_program_provider_channel_id(stripped)
            if ch_id is not None:
                if not cue_id_matches_slot(ch_id, self.expected_catalog_ids):
                    return
                self.program_channel_id = ch_id
                cat = lookup_channel(ch_id)
                if cat and cat.get("forKids") is False and (
                    "kid=true" in stripped.lower() or "kids=true" in stripped.lower()
                ):
                    term_print(
                        f"{current_time_str()} [{self.device}] [키즈] "
                        f"주의: 로그 kid=true 이지만 카탈로그 forKids=false "
                        f"({format_channel_ref(ch_id)})"
                    )
        self.phases[phase_key] = True
        if phase_key in self._printed_phases:
            return
        self._printed_phases.add(phase_key)
        label = KIDS_WATERMARK_PHASE_LABELS[phase_key]
        term_print(
            f"{current_time_str()} [{self.device}] [키즈 워터마크] {label}"
        )
        term_print(f"  {stripped[:220]}{'…' if len(stripped) > 220 else ''}")
        if self.is_complete():
            key = (self.device, self.channel_number)
            _run_checklist["kids_watermark"][key] = True
            term_print(
                f"{current_time_str()} [{self.device}] [키즈 워터마크] ✓ 완료 "
                f"ch {self.channel_number} {self.channel_name}"
            )

    def process_line(self, line):
        stripped = line.rstrip()
        lower = stripped.lower()

        if ("kid=true" in lower or "kids=true" in lower) and (
            "receive cue" in lower
            or "register cue" in lower
            or "programproviderchannel" in lower
        ):
            self._mark_phase("kid_cue", stripped)

        if "kidwatermarkmanager.buildwatermark" in lower.replace(" ", ""):
            self._mark_phase("watermark_build", stripped)
            if re.search(r"iskid\s*:\s*true", lower):
                self._mark_phase("is_kid", stripped)

        if "kid_watermark.png" in lower:
            self._mark_phase("watermark_uri", stripped)

        if "kid watermark" in lower:
            self._mark_phase("legacy", stripped)

    @property
    def seen(self):
        return self.is_complete()

    def is_complete(self):
        watermark_ok = (
            self.phases["watermark_uri"]
            or self.phases["watermark_build"]
            or self.phases["legacy"]
        )
        return watermark_ok

    def missing_summary(self):
        missing = []
        if not self.phases["kid_cue"]:
            missing.append("kid=true Cue")
        if not self.phases["is_kid"] and self.phases["watermark_build"]:
            missing.append("isKid: true (buildWaterMark)")
        if not (
            self.phases["watermark_uri"]
            or self.phases["watermark_build"]
            or self.phases["legacy"]
        ):
            missing.append("kid_watermark.png / buildWaterMark")
        return missing


def start_kids_watermark_watch(
    devices,
    channel_number,
    channel_name,
    expected_catalog_ids=None,
    *,
    monitor_only=False,
):
    if not is_kids_channel(channel_number):
        return
    ch = normalize_channel_number(channel_number)
    if expected_catalog_ids is None:
        expected_catalog_ids = resolve_expected_catalog_ids(channel_name)
    with _kids_watermark_lock:
        for device in devices:
            _active_kids_watermark_trackers[device] = KidsWatermarkTracker(
                device, ch, channel_name, expected_catalog_ids=expected_catalog_ids
            )
            key = (device, ch)
            if key not in _run_checklist["kids_watermark"]:
                _run_checklist["kids_watermark"][key] = False
    if monitor_only:
        term_print(
            f"{current_time_str()} [모니터링/키즈] 워터마크 logcat — "
            f"ch {ch} {channel_name}"
        )
    else:
        term_print(
            f"{current_time_str()} [키즈 워터마크] 확인 시작 — ch {ch} {channel_name}"
        )
    term_print(
        "  기대 로그: kid=true (receiveCue) → isKid: true → kid_watermark.png"
    )


def wait_for_kids_watermark(
    devices,
    channel_number,
    timeout_sec=KIDS_WATERMARK_WAIT_SEC,
    channel_name=None,
    ad_time_str=None,
):
    if not is_kids_channel(channel_number):
        return True

    ch = normalize_channel_number(channel_number)
    now = datetime.now()
    deadline = now.timestamp() + timeout_sec
    if ad_time_str:
        ad_dt = parse_ad_datetime(now, ad_time_str)
        deadline = max(deadline, (ad_dt + timedelta(seconds=140)).timestamp())
    term_print(
        f"{current_time_str()} [키즈 워터마크] 대기 (ch {ch}, "
        f"편성 {ad_time_str or '?'} 포함 최대 {int(deadline - time.time())}초)"
    )
    last_preload = 0.0
    while time.time() < deadline:
        if time.time() - last_preload >= 4:
            preload_kids_watermark_buffer(devices)
            preload_ad_logcat_buffer(
                devices, lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC, max_lines=2000
            )
            last_preload = time.time()
        all_seen = True
        for device in devices:
            with _kids_watermark_lock:
                tracker = _active_kids_watermark_trackers.get(device)
            if not tracker or not tracker.seen:
                all_seen = False
                break
        if all_seen:
            _print_kids_watermark_summary(devices, ch)
            return True
        time.sleep(1)

    term_print(f"{current_time_str()} [키즈 워터마크] 타임아웃 (ch {ch})")
    _print_kids_watermark_summary(devices, ch)
    return False


def kids_watermark_done_for_all_devices(devices, channel_number):
    """해당 채널에 대해 모든 디바이스에서 키즈 워터마크 확인 완료했는지."""
    if not is_kids_channel(channel_number):
        return False
    ch = normalize_channel_number(channel_number)
    kids = _run_checklist.get("kids_watermark") or {}
    for device in devices:
        if not kids.get((device, ch)):
            return False
    return True


def _print_kids_watermark_summary(devices, channel_number):
    term_print(f"\n--- [키즈 워터마크] ch {channel_number} 단계 ---")
    for device in devices:
        with _kids_watermark_lock:
            tracker = _active_kids_watermark_trackers.get(device)
        if not tracker:
            term_print(f"  [{device}] 추적 없음")
            continue
        ok = tracker.is_complete()
        term_print(f"  [{device}] {'✓' if ok else '✗'} {tracker.channel_name}")
        for phase_key, label in KIDS_WATERMARK_PHASE_LABELS.items():
            mark = "✓" if tracker.phases.get(phase_key) else "·"
            term_print(f"    {mark} {label}")
        if not ok:
            term_print(f"    미확인: {', '.join(tracker.missing_summary())}")
    term_print("---\n")


def _term_log_google_ad_line(device, line: str):
    """체크 3-A/B/C 진행 중 쿼타일(및 서브체크 필수) logcat 실시간 출력."""
    stripped = line.strip()
    if not stripped or not is_google_ad_term_log_line(stripped, _active_google_subtest):
        return
    tag = _google_check_tag()
    if tag == "모니터링/구글":
        term_print(f"{current_time_str()} [{tag}] [{device}] {stripped}")
    else:
        term_print(f"{current_time_str()} [{tag}/google] [{device}] {stripped}")


def _is_anypoint_critical_related(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in ANYPOINT_CRITICAL_MARKERS)


def _record_critical_issue(device, needle, snippet):
    """Anypoint 관련 치명 이슈만 기록·출력 (30초 버스트 dedupe)."""
    key = (device, needle)
    now = time.time()
    if now - _critical_issue_last.get(key, 0) < 30:
        return
    _critical_issue_last[key] = now
    snippet = (snippet or "").strip()[:200]
    _critical_issues.append((current_time_str(), device, needle, snippet))
    term_print(
        f"{current_time_str()} [⚠ CRITICAL] {device} {needle} "
        f"(Anypoint) — {snippet[:160]}"
    )


def _flush_pending_critical(device, *, force=False):
    """버퍼링 중인 FATAL/crash 스택을 Anypoint 여부로 확정 또는 폐기."""
    pending = _pending_critical.get(device)
    if not pending:
        return
    age = time.time() - pending["ts"]
    blob = "\n".join(pending["lines"])
    has_process = "process:" in blob.lower()
    if force or has_process or age >= _CRITICAL_PENDING_TTL_SEC:
        _pending_critical.pop(device, None)
        if _is_anypoint_critical_related(blob):
            _record_critical_issue(
                device, pending["needle"], pending["lines"][-1]
            )


def _detect_critical_issue(device, line):
    """FATAL/크래시/ANR — Anypoint 관련일 때만 리포트.

    ANR: 같은 줄 패키지로 즉시 판정.
    FATAL / beginning of crash / signal: 이어지는 Process:·스택을
    잠시 모은 뒤 Anypoint면 기록, 아니면 무시.
    """
    stripped = line.strip()
    low = stripped.lower()

    pending = _pending_critical.get(device)
    if pending:
        pending["lines"].append(stripped[:200])
        if len(pending["lines"]) >= _CRITICAL_PENDING_MAX_LINES:
            _flush_pending_critical(device, force=True)
        elif "process:" in low:
            _flush_pending_critical(device, force=True)
        elif time.time() - pending["ts"] >= _CRITICAL_PENDING_TTL_SEC:
            _flush_pending_critical(device, force=True)
        return

    for needle in CRASH_LOG_NEEDLES:
        if needle.lower() not in low:
            continue
        if needle.startswith("ANR"):
            if _is_anypoint_critical_related(stripped):
                _record_critical_issue(device, needle, stripped)
            return
        # FATAL / crash marker / native signal — Process: 대기
        _pending_critical[device] = {
            "needle": needle,
            "ts": time.time(),
            "lines": [stripped[:200]],
        }
        return


def on_log_line_for_monitoring(device, line):
    lower = line.lower()
    _detect_critical_issue(device, line)
    if NOT_READY_TO_PLAY_AD_NEEDLE in lower:
        _maybe_recover_ad_not_ready(device, line)
    if NOT_LINEAR_TV_STATE_NEEDLE in lower:
        _maybe_recover_non_linear_tv(device, line)

    on_log_line_for_ad_playback(device, line)
    with _google_tracker_lock:
        gtracker = _active_google_trackers.get(device)
    if gtracker:
        if is_google_ad_term_log_line(line, _active_google_subtest):
            _term_log_google_ad_line(device, line)
        gtracker.process_line(line)
    with _kids_watermark_lock:
        wm = _active_kids_watermark_trackers.get(device)
    if wm:
        wm.process_line(line)


def on_log_line_for_ad_playback(device, line):
    with _ad_tracker_lock:
        tracker = _active_ad_trackers.get(device)
    if tracker:
        tracker.process_line(line)


def _finish_ad_playback_wait(
    devices,
    expected_impressions,
    *,
    monitor_only=False,
):
    _print_ad_playback_summary(devices, expected_impressions)
    eval_result = evaluate_internal_ad_playback(
        devices, expected_impressions, update_checklist=not monitor_only
    )
    if monitor_only:
        ch_name = ""
        with _ad_tracker_lock:
            t0 = _active_ad_trackers.get(devices[0]) if devices else None
            if t0:
                ch_name = t0.channel_name or ""
        _log_schedule_ad_monitor_result(devices, eval_result, ch_name)
        return bool(eval_result.get("ok"))
    exp_sec = eval_result.get("expected_playtime_sec", 120)
    floor_sec = max(0, exp_sec - CHECK2_PLAYTIME_UNDER_CUE_MS / 1000)
    if eval_result.get("ok"):
        term_print(
            f"{current_time_str()} [체크 2] ✓ playTime {floor_sec:.0f}~{exp_sec:.0f}초 "
            f"(cue) + impression API 200"
        )
    else:
        term_print(
            f"{current_time_str()} [체크 2] ✗ "
            f"playTime/API 조건 미충족 (기대 {floor_sec:.0f}~{exp_sec:.0f}초)"
        )
    print_checklist_progress(devices)
    return bool(eval_result.get("ok"))


def wait_for_ad_playback(
    devices,
    timeout_sec=AD_PLAYBACK_WAIT_TIMEOUT_SEC,
    expected_impressions=1,
    ui_channel_name=None,
    ui_channel_number=None,
    *,
    monitor_only=False,
):
    tag = "[모니터링] " if monitor_only else ""
    term_print(
        f"{current_time_str()} {tag}[광고 재생 확인] 대기 (최대 {timeout_sec}초, "
        f"impression {expected_impressions}회 목표, logcat 기준)"
    )
    deadline = time.time() + timeout_sec
    last_status = time.time()

    all_complete = True
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker or not tracker.is_complete(expected_impressions):
            all_complete = False
            break
    if all_complete:
        term_print(
            f"{current_time_str()} {tag}[광고 재생 확인] 이미 logcat 에서 완료됨"
        )
        return _finish_ad_playback_wait(
            devices, expected_impressions, monitor_only=monitor_only
        )

    while time.time() < deadline:
        all_done = True
        for device in devices:
            with _ad_tracker_lock:
                tracker = _active_ad_trackers.get(device)
            if not tracker or not tracker.is_complete(expected_impressions):
                all_done = False
                break
        if all_done:
            term_print(
                f"{current_time_str()} {tag}[광고 재생 확인] 모든 디바이스 완료"
            )
            return _finish_ad_playback_wait(
                devices, expected_impressions, monitor_only=monitor_only
            )

        if time.time() - last_status >= AD_PLAYBACK_STATUS_INTERVAL_SEC:
            for device in devices:
                with _ad_tracker_lock:
                    tracker = _active_ad_trackers.get(device)
                if tracker and not tracker.is_complete(expected_impressions):
                    missing = tracker.missing_summary(expected_impressions)
                    term_print(
                        f"{current_time_str()} [{device}] [광고] 대기 중… "
                        f"미확인: {', '.join(missing)}"
                    )
            last_status = time.time()

        time.sleep(1)

    term_print(f"{current_time_str()} {tag}[광고 재생 확인] 타임아웃")
    if monitor_only:
        eval_result = evaluate_internal_ad_playback(
            devices, expected_impressions, update_checklist=False
        )
        ch_name = ""
        with _ad_tracker_lock:
            t0 = _active_ad_trackers.get(devices[0]) if devices else None
            if t0:
                ch_name = t0.channel_name or ""
        _print_ad_playback_summary(devices, expected_impressions)
        _log_schedule_ad_monitor_result(devices, eval_result, ch_name)
        return False
    _finish_ad_playback_wait(devices, expected_impressions, monitor_only=False)
    return False


def _print_ad_playback_summary(devices, expected_impressions):
    term_print(f"\n{'=' * 60}")
    term_print(f"{current_time_str()} === [광고 재생 확인] 결과 ===")
    for device in devices:
        with _ad_tracker_lock:
            tracker = _active_ad_trackers.get(device)
        if not tracker:
            term_print(f"\n[{device}] 추적 없음")
            continue
        ok = tracker.is_complete(expected_impressions)
        term_print(f"\n[{device}] {'✓ 완료' if ok else '✗ 미완료'} — {tracker.channel_name}")
        for phase_key, _, label in AD_PLAYBACK_PHASES:
            mark = "✓" if tracker.phases.get(phase_key) else "·"
            term_print(f"  {mark} {label}")
        term_print(
            f"  cue={tracker.cue_count}, impression={tracker.impression_count}"
            + (
                f", playTime={tracker.last_play_time_ms}ms"
                if tracker.last_play_time_ms
                else ""
            )
        )
        if not ok:
            term_print(
                f"  미확인: {', '.join(tracker.missing_summary(expected_impressions))}"
            )
    term_print(f"{'=' * 60}\n")


def prompt_final_channels(device_ips):
    """모니터링 종료 후 복귀할 채널 번호 (이탈용 escape 채널)."""
    env_ch = os.environ.get("STB_ESCAPE_CHANNEL", DEFAULT_ESCAPE_CHANNEL).strip()
    if env_ch.isdigit():
        return {device_id: env_ch for device_id in device_ips}

    final_channels = {}
    for device_id in device_ips:
        while True:
            ch = input(f"디바이스 {device_id}의 마지막 채널 번호를 입력하세요: ")
            if ch.isdigit():
                final_channels[device_id] = ch.strip()
                break
            print("숫자만 입력해주세요.")
    return final_channels


def is_purchase_screen_blocking(devices) -> bool:
    for device in devices:
        if is_blocking_purchase_or_dialog(get_resumed_activity_line(device)):
            return True
    return False


def switch_channel_with_verify(
    channel_number,
    devices,
    *,
    clear_buffer=None,
    channel_name=None,
    expected_catalog_ids=None,
    tune_timeout_sec=None,
) -> bool:
    """채널 전환 후 STB 채널번호·(가능 시) 카탈로그 id 일치할 때까지 재시도."""
    global _last_successful_tune_at
    if clear_buffer is None:
        clear_buffer = CHANNEL_SWITCH_CLEAR_LOG
    if not devices:
        return False
    gap = CHANNEL_SWITCH_MIN_GAP_SEC - (time.time() - _last_successful_tune_at)
    if gap > 0:
        time.sleep(gap)
    target = normalize_channel_number(channel_number)
    if expected_catalog_ids is None and channel_name:
        expected_catalog_ids = resolve_expected_catalog_ids(channel_name)
    elif expected_catalog_ids is not None:
        expected_catalog_ids = set(expected_catalog_ids)
    else:
        expected_catalog_ids = set()

    tune_timeout = (
        tune_timeout_sec if tune_timeout_sec is not None else CHANNEL_TUNE_VERIFY_SEC
    )
    if (
        tune_timeout_sec is None
        and TUNE_FALLBACK_CATALOG_CUE
        and expected_catalog_ids
    ):
        tune_timeout = max(tune_timeout, TUNE_CATALOG_FALLBACK_TIMEOUT_SEC)
    max_tries = max(1, CHANNEL_SWITCH_MAX_RETRIES)
    _set_pending_tune_targets(devices, target)
    try:
        for attempt in range(1, max_tries + 1):
            if attempt > 1:
                term_print(
                    f"{current_time_str()} [채널 전환] 재시도 {attempt}/{max_tries} "
                    f"ch {target}"
                )
                ok_pre = True
                via_pre = ""
                for device in devices:
                    ok, via = channel_tune_confirmed(
                        device, target, expected_catalog_ids
                    )
                    if not ok:
                        ok_pre = False
                        break
                    via_pre = via
                if ok_pre:
                    term_print(
                        f"{current_time_str()} [튜닝 확인] ch {target} 일치 "
                        f"({via_pre or 'CM/OSD'}) — 키패드 재입력 생략"
                    )
                    _last_successful_tune_at = time.time()
                    return True
            ensure_live_tv_foreground(devices)
            if not prepare_channel_keypad_foreground(devices):
                # 홈/검색 등 비라이브에 숫자 입력하면 채널 튜닝이 안 되고
                # not linear 만 반복됨 — 키패드 스킵하고 KEYCODE_TV 재시도
                if is_purchase_screen_blocking(devices):
                    if attempt >= max_tries:
                        return False
                    time.sleep(CHANNEL_SWITCH_RETRY_DELAY_SEC)
                    continue
                term_print(
                    f"{current_time_str()} [채널 전환] ch {target} - "
                    f"라이브 미복귀, 키패드 보류 (재시도 {attempt}/{max_tries})"
                )
                for device in devices:
                    force_live_then_ready_for_channel(device, "[채널 전환]")
                if attempt >= max_tries:
                    return False
                time.sleep(CHANNEL_SWITCH_RETRY_DELAY_SEC)
                continue
            do_clear = clear_buffer and attempt == 1
            switch_channel_via_adb(channel_number, devices, clear_buffer=do_clear)
            time.sleep(CHANNEL_SWITCH_SETTLE_SEC)

            if is_purchase_screen_blocking(devices):
                term_print(
                    f"{current_time_str()} [튜닝 확인] ch {target} — 유료가입/차단 화면"
                )
                if attempt >= max_tries:
                    return False
                time.sleep(CHANNEL_SWITCH_RETRY_DELAY_SEC)
                continue

            tuned_map = wait_for_tuned_channels(
                devices,
                target,
                timeout_sec=tune_timeout,
                expected_catalog_ids=expected_catalog_ids,
            )
            stb_ok = True
            confirm_note = ""
            for device in devices:
                ok, via = channel_tune_confirmed(
                    device, target, expected_catalog_ids
                )
                if ok:
                    if via and via != "CM/OSD":
                        confirm_note = f" ({via})"
                    continue
                got = tuned_map.get(device)
                if got is None:
                    term_print(
                        f"{current_time_str()} [튜닝 확인] {device} ch {target} 미확인 "
                        f"(CM extraData·catalog cue 없음)"
                    )
                    stb_ok = False
                elif str(got) != str(target):
                    term_print(
                        f"{current_time_str()} [튜닝 확인] {device} 실제 ch {got} "
                        f"(기대 {target})"
                    )
                    stb_ok = False

            if not stb_ok:
                if attempt >= max_tries:
                    return False
                time.sleep(CHANNEL_SWITCH_RETRY_DELAY_SEC)
                continue

            if expected_catalog_ids and CHANNEL_CATALOG_SETTLE_SEC > 0:
                time.sleep(CHANNEL_CATALOG_SETTLE_SEC)

            if _catalog_id_tune_mismatch(devices, expected_catalog_ids):
                if attempt >= max_tries:
                    return False
                time.sleep(CHANNEL_SWITCH_RETRY_DELAY_SEC)
                continue

            cat_note = ""
            if expected_catalog_ids and any(
                read_recent_program_channel_ids(d) for d in devices
            ):
                cat_note = " (카탈로그 id OK)"
            term_print(
                f"{current_time_str()} [튜닝 확인] ch {target} 일치"
                f"{confirm_note}{cat_note}"
            )
            _last_successful_tune_at = time.time()
            return True

        return False
    finally:
        _clear_pending_tune_targets(devices)


def verify_tuned_channel(devices, channel_number) -> bool:
    """튜닝 후 CM extraData / OSD 가 기대 채널과 일치하면 True."""
    target = normalize_channel_number(channel_number)
    if is_purchase_screen_blocking(devices):
        return False
    tuned_map = wait_for_tuned_channels(devices, target, timeout_sec=6.0)
    for device in devices:
        got = tuned_map.get(device)
        if got is None:
            term_print(
                f"{current_time_str()} [튜닝 확인] {device} ch {target} 미확인 "
                f"(CM extraData·catalog cue 없음)"
            )
            return False
        if str(got) != str(target):
            term_print(
                f"{current_time_str()} [튜닝 확인] {device} 실제 ch {got} "
                f"(기대 {target})"
            )
            return False
    return True


def _maybe_send_checklist_reports(devices):
    """2단계 리포트:
    1) 한 바퀴 완료(전 항목 pass/재시도소진) → 1차 결과 전송 후 실패 항목 추가 재시도
    2) 전부 PASS 또는 추가 재시도까지 완료 → 최종 결과 1회 전송
    """
    global _report_interim_sent
    if _chat_report_sent:
        return
    if not checklist_round_complete():
        return
    if checklist_all_done(devices):
        post_results_to_google_chat(devices)
        _report_interim_sent = True
        return
    if not _report_interim_sent:
        term_print(
            f"{current_time_str()} [Google Chat] 체크리스트 1차 완료 — "
            f"결과 전송 후 실패 항목 추가 재시도"
        )
        post_interim_results_to_google_chat(devices)
        _report_interim_sent = True
        _enter_checklist_extra_phase()
    else:
        # 추가 재시도까지 완료(전부 PASS 아님) → 최종 전송
        post_results_to_google_chat(devices)


def monitor_and_switch_channels_with_data(data, devices, final_channels=None):
    checklist_only = _env_truthy("CHECKLIST_ONLY")
    if checklist_only:
        term_print(
            f"{current_time_str()} [CHECKLIST_ONLY] 체크리스트 완료 시 "
            f"편성 모니터링 생략"
        )
    print(f"{current_time_str()} 모니터링 시작, data 길이: {len(data)}")
    final_channels = final_channels or {}
    while data:
        _maybe_send_checklist_reports(devices)
        if checklist_only and checklist_all_done(devices):
            term_print(
                f"{current_time_str()} [CHECKLIST_ONLY] 체크리스트 완료 — "
                f"편성 모니터링 생략 후 종료"
            )
            break
        try:
            now = datetime.now()
            next_row = None
            next_ad_time = None

            for i in reversed(range(len(data))):
                row = data[i]
                try:
                    if (
                        "광고편성 시간" not in row
                        or "채널명" not in row
                        or "채널번호" not in row
                    ):
                        data.pop(i)
                        continue

                    ad_dt = parse_ad_datetime(now, row["광고편성 시간"])
                    if now > ad_dt + timedelta(seconds=180):
                        data.pop(i)
                except Exception as e:
                    print(f"{current_time_str()} 파싱 오류: {e}")
                    data.pop(i)

            next_ad_time, next_row = pick_next_ad_row(data, now)

            if next_row is None:
                print(f"{current_time_str()} 광고 스케줄 모두 완료, 모니터링 종료.")
                break

            channel_name = next_row["채널명"]
            channel_number = next_row["채널번호"]
            ad_time_str = next_row["광고편성 시간"]
            switch_time = next_ad_time - timedelta(seconds=30)
            slot_late_deadline = next_ad_time + timedelta(
                seconds=_slot_ad_start_timeout_sec(channel_number, ad_time_str)
            )

            if switch_time <= now < slot_late_deadline:
                pending_desc = (
                    f"{channel_name}({channel_number}) @ {ad_time_str}"
                )
                if _defer_if_ad_slot_busy(devices, pending_desc):
                    time.sleep(1)
                    continue

                next_action = _describe_next_check_action(channel_number)
                term_print(
                    f"{current_time_str()} [진행] 광고 예정: "
                    f"{channel_name} ({channel_number}) @ {ad_time_str}\n"
                    f"  확인 중: {next_action}"
                )
                escape_ch = get_escape_channel(
                    channel_number, data, final_channels, devices
                )

                # 체크리스트 완료 또는 재시도 소진 시 편성 모니터링(재생·impression 확인).
                if checklist_all_done(devices) or not has_pending_checklist_work(
                    channel_number
                ):
                    if _skip_non_kids_for_check6(channel_number):
                        term_print(
                            f"{current_time_str()} [키즈] 6번 미완료 — "
                            f"일반 채널 {channel_name}({channel_number}) "
                            f"@ {ad_time_str} 스킵"
                        )
                        data.remove(next_row)
                        continue
                    run_schedule_slot_monitor(
                        devices, channel_name, channel_number, ad_time_str
                    )
                    label = (
                        "체크리스트 완료 후"
                        if checklist_all_done(devices)
                        else "미완료 항목 재시도 소진"
                    )
                    term_print(
                        f"{current_time_str()} [모니터링] 슬롯 처리 완료 ({label})"
                    )
                    print(
                        f"{current_time_str()} 실행된 row 제거: "
                        f"{channel_name} {ad_time_str}"
                    )
                    data.remove(next_row)
                    continue

                # 키즈 채널 슬롯: 6번만 (2번은 일반 채널 단독 편성에서 수행).
                if is_kids_channel(channel_number) and not kids_check6_passed():
                    expected_ids = _log_expected_catalog_ids(
                        channel_name, channel_number
                    )
                    start_ad_playback_watch(
                        devices,
                        channel_name,
                        channel_number,
                        ad_time_str,
                        announce=False,
                        preload_buffer=False,
                        expected_catalog_ids=expected_ids,
                        log_lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC,
                    )
                    start_kids_watermark_watch(
                        devices,
                        channel_number,
                        channel_name,
                        expected_catalog_ids=expected_ids,
                    )
                    if not switch_channel_with_verify(
                        channel_number,
                        devices,
                        clear_buffer=False,
                        channel_name=channel_name,
                        expected_catalog_ids=expected_ids,
                    ):
                        clear_slot_watches(devices)
                        clear_kids_watermark_pending(devices, channel_number)
                        reason = (
                            "유료가입 화면"
                            if is_purchase_screen_blocking(devices)
                            else "채널 튜닝 실패"
                        )
                        term_print(
                            f"{current_time_str()} [키즈] {reason} — "
                            f"{channel_name}({channel_number}) 다음 편성 재시도"
                        )
                        data.remove(next_row)
                        continue
                    preload_ad_logcat_buffer(
                        devices, lookback_sec=KIDS_SLOT_LOG_LOOKBACK_SEC
                    )
                    preload_kids_watermark_buffer(devices)
                    print(f"{current_time_str()} 키즈 슬롯 — 6번 확인")
                    if not wait_for_slot_ad_start(
                        devices, channel_name, channel_number, ad_time_str
                    ):
                        skip_slot_no_ad(
                            devices, channel_name, channel_number, ad_time_str
                        )
                        clear_kids_watermark_pending(devices, channel_number)
                        data.remove(next_row)
                        continue
                    log_ok = wait_for_kids_watermark(
                        devices,
                        channel_number,
                        channel_name=channel_name,
                        ad_time_str=ad_time_str,
                    )
                    ui_ok = run_kids_check6_ui_verification(
                        devices, channel_name, channel_number
                    )
                    if not _ad_playback_started(devices) and ui_ok is False:
                        term_print(
                            f"{current_time_str()} [키즈] 광고 play 미감지 — "
                            f"{channel_name}({channel_number}) 체크6 실패 확정 없이 다음 편성 재시도"
                        )
                        clear_slot_watches(devices)
                        clear_kids_watermark_pending(devices, channel_number)
                        data.remove(next_row)
                        continue
                    finalize_kids_watermark_check(
                        devices,
                        channel_number,
                        channel_name,
                        log_ok=log_ok,
                        ui_ok=ui_ok,
                    )
                    print_checklist_progress(devices)
                    clear_slot_watches(devices)
                    clear_kids_watermark_pending(devices, channel_number)
                elif needs_check_5():
                    attempt5 = _bump_check_attempt("leave_before_play")
                    term_print(
                        f"{current_time_str()} [체크 5] 시도 "
                        f"{attempt5}/{CHECKLIST_CHECK_MAX_ATTEMPTS}"
                    )
                    result5 = execute_test_leave_before_play(
                        devices, next_row, escape_ch
                    )
                    msg5 = result5.get("message") or ""
                    charged5 = True
                    if not result5.get("ok") and (
                        "register cue 미감지" in msg5
                        or msg5.startswith("유료가입")
                    ):
                        attempt5 = _unbump_check_attempt("leave_before_play")
                        charged5 = False
                        term_print(
                            f"{current_time_str()} [체크 5] 테스트 미성립 — "
                            f"시도 횟수 미차감 ({msg5 or '원인 미상'})"
                        )
                    if charged5 and not result5.get("ok") and not msg5.startswith(
                        "유료가입"
                    ):
                        if attempt5 < CHECKLIST_CHECK_MAX_ATTEMPTS:
                            term_print(
                                f"{current_time_str()} [체크 5] 이탈 미완료 → "
                                f"다음 큐 재시도 ({attempt5}/"
                                f"{CHECKLIST_CHECK_MAX_ATTEMPTS})"
                            )
                elif needs_check_4():
                    attempt4 = _bump_check_attempt("leave_during_ad")
                    charged4 = True
                    term_print(
                        f"{current_time_str()} [체크 4] 시도 "
                        f"{attempt4}/{CHECKLIST_CHECK_MAX_ATTEMPTS}"
                    )
                    result4 = execute_test_leave_during_ad(
                        devices, next_row, escape_ch
                    )
                    msg4 = result4.get("message") or ""
                    if not result4.get("ok") and (
                        "채널 튜닝 실패" in msg4
                        or "play ==== 미감지" in msg4
                        or msg4.startswith("유료가입")
                    ):
                        attempt4 = _unbump_check_attempt("leave_during_ad")
                        charged4 = False
                        term_print(
                            f"{current_time_str()} [체크 4] 테스트 미성립 — "
                            f"시도 횟수 미차감 ({msg4 or '원인 미상'})"
                        )
                    if charged4 and not result4.get("ok") and not (
                        msg4
                    ).startswith("유료가입"):
                        if attempt4 < CHECKLIST_CHECK_MAX_ATTEMPTS:
                            term_print(
                                f"{current_time_str()} [체크 4] 미완료 → "
                                f"다음 큐 재시도 ({attempt4}/"
                                f"{CHECKLIST_CHECK_MAX_ATTEMPTS}) "
                                f"({result4.get('message') or '원인 미상'})"
                            )
                elif needs_check_3():
                    execute_test_google_ad(devices, next_row, escape_ch)
                elif needs_check_2():
                    expected_ids = _log_expected_catalog_ids(
                        channel_name, channel_number
                    )
                    start_ad_playback_watch(
                        devices,
                        channel_name,
                        channel_number,
                        ad_time_str,
                        preload_buffer=False,
                        expected_catalog_ids=expected_ids,
                        log_lookback_sec=CHECK2_LOG_LOOKBACK_SEC,
                    )
                    if not switch_channel_with_verify(
                        channel_number,
                        devices,
                        clear_buffer=False,
                        channel_name=channel_name,
                        expected_catalog_ids=expected_ids,
                    ):
                        clear_slot_watches(devices)
                        reason = (
                            "유료가입 화면"
                            if is_purchase_screen_blocking(devices)
                            else "채널 튜닝 실패"
                        )
                        term_print(
                            f"{current_time_str()} [체크 2] {reason} — "
                            f"{channel_name}({channel_number}) 다음 편성 재시도"
                        )
                        data.remove(next_row)
                        continue
                    preload_ad_logcat_buffer(
                        devices, lookback_sec=CHECK2_LOG_LOOKBACK_SEC
                    )
                    print(f"{current_time_str()} [체크 2] 광고 채널 전환 완료")
                    attempt2, max2, bonus2 = _bump_check2_attempt()
                    bonus_label = "보너스 " if bonus2 else ""
                    term_print(
                        f"{current_time_str()} [체크 2] {bonus_label}시도 "
                        f"{attempt2}/{max2}"
                    )
                    if not wait_for_slot_ad_start(
                        devices, channel_name, channel_number, ad_time_str
                    ):
                        skip_slot_no_ad(
                            devices, channel_name, channel_number, ad_time_str
                        )
                        data.remove(next_row)
                        continue
                    wait_for_ad_playback(
                        devices,
                        ui_channel_name=channel_name,
                        ui_channel_number=channel_number,
                    )

                print(
                    f"{current_time_str()} 실행된 row 제거: "
                    f"{channel_name} {ad_time_str}"
                )
                data.remove(next_row)
            elif now >= slot_late_deadline:
                term_print(
                    f"{current_time_str()} [편성 스킵] {channel_name}("
                    f"{channel_number}) @ {ad_time_str} — "
                    f"처리 기한 초과(광고+"
                    f"{_slot_ad_start_timeout_sec(channel_number, ad_time_str)}초)"
                )
                data.remove(next_row)
            else:
                wait_sec = (switch_time - now).total_seconds()
                if wait_sec > 0:
                    _log_monitor_wait_status(
                        channel_name,
                        channel_number,
                        ad_time_str,
                        switch_time,
                        wait_sec,
                    )
                    if wait_sec > 60:
                        time.sleep(min(30, wait_sec - 30))
                    else:
                        time.sleep(1)
                else:
                    time.sleep(0.5)

        except Exception as err:
            print(f"{current_time_str()} 반복 중 오류 발생: {err}")
            time.sleep(30)


def count_impression_logs(log_dir, filenames_dict):
    counts = {}
    for device, filename in filenames_dict.items():
        log_path = os.path.join(log_dir, filename)
        counts[device] = {"count": 0, "last_time": None}

        if not os.path.exists(log_path):
            continue

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "impression log size" in line:
                        counts[device]["count"] += 1
                        m = re.match(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
                        if m:
                            log_time_str = m.group(1)
                            try:
                                log_time = datetime.strptime(
                                    f"{datetime.now().year}-{log_time_str}",
                                    "%Y-%m-%d %H:%M:%S.%f",
                                )
                                counts[device]["last_time"] = log_time
                            except Exception:
                                pass
        except Exception as e:
            print(f"로그 파일 읽기 오류: {log_path}, {e}")

    return counts


def print_final_checklist_summary(device_ips):
    """체크리스트 최종 요약 (터미널)."""
    term_print(f"\n{'=' * 60}")
    term_print(f"{current_time_str()} === 최종 확인 체크리스트 요약 ===")
    term_print(f"{'=' * 60}")

    if _run_checklist.get("versions_skipped"):
        term_print("\n1. S/W ver. 확인: ⏭ 스킵 (SKIP_REBOOT)")
    elif _run_checklist.get("versions"):
        versions = _run_checklist.get("versions") or {}
        ver_ok = all(
            all(v.values()) for v in versions.values() if isinstance(v, dict)
        )
        term_print(f"\n1. S/W ver. 확인: {'✓' if ver_ok else '△/✗'}")
        for device, vmap in versions.items():
            for group_key, group in VERSION_GROUPS.items():
                val = vmap.get(group_key)
                term_print(
                    f"   [{device}] {group['label']}: {val or '(미확인)'}"
                )
    else:
        term_print("\n1. S/W ver. 확인: (미실행)")

    ad2 = _run_checklist.get("ad_playback")
    if isinstance(ad2, dict):
        mark = "✓" if ad2.get("ok") else "✗"
        term_print(
            f"\n2. 광고 재생(내부 소재): {mark} "
            f"playTime {max(0, ad2.get('expected_playtime_sec', 120) - CHECK2_PLAYTIME_UNDER_CUE_MS / 1000):.0f}"
            f"~{ad2.get('expected_playtime_sec', 120):.0f}초(cue) + API 200"
        )
        for device, info in (ad2.get("per_device") or {}).items():
            if isinstance(info, dict):
                term_print(
                    f"   [{device}] playTime={info.get('playtime_sum_sec')}초 "
                    f"(기대 {max(0, (info.get('expected_playtime_sec') or 120) - CHECK2_PLAYTIME_UNDER_CUE_MS / 1000):.0f}"
                    f"~{info.get('expected_playtime_sec', '?')}초) "
                    f"flow={'✓' if info.get('flow_ok') else '✗'} "
                    f"playTime={'✓' if info.get('playtime_ok') else '✗'} "
                    f"API200={'✓' if info.get('api_200') else '✗'}"
                )
    else:
        term_print("\n2. 광고 재생(내부 소재): (미실행)")
    if _run_checklist.get("google_skipped"):
        term_print("3-A~C 광고 재생(구글): ⏭ 생략 (SKIP_GOOGLE_CHECK)")
    else:
        g = _run_checklist.get("google_ad") or {}
        for sub in GOOGLE_CHECK3_SUBTESTS:
            title = GOOGLE_CHECK3_LABELS[sub]
            r = g.get(sub)
            if isinstance(r, dict) and r.get("done"):
                mark = "✓" if r.get("ok") else "✗"
                term_print(f"\n{title}: {mark} {r.get('message', '')}")
            else:
                term_print(f"\n{title}: (미실행)")

    leave4 = _run_checklist.get("leave_during_ad")
    if leave4:
        mark = "✓" if leave4.get("ok") else "✗"
        term_print(
            f"\n4. 편성 종료 전 이탈·재생량(ImpressionLog): {mark} {leave4.get('message', '')}"
        )
        session_sec = leave4.get("session_playtime_ms", 0) / 1000
        delta_sec = leave4.get("playtime_delta_ms", 0) / 1000
        delta_sign = "+" if delta_sec >= 0 else ""
        term_print(
            f"   ch {leave4.get('channel_number')} → {leave4.get('escape_channel')} "
            f"(stop−start={session_sec:.1f}초, playTime합="
            f"{leave4.get('total_play_time_sec', 0)}초, "
            f"차이={delta_sign}{delta_sec:.1f}초, "
            f"일치={'✓' if leave4.get('playtime_match') else '✗'})"
        )
    else:
        term_print("\n4. 편성 종료 전 이탈·재생량(ImpressionLog): (미실행)")

    leave5 = _run_checklist.get("leave_before_play")
    if leave5:
        mark = "✓" if leave5.get("ok") else "✗"
        verdict = "성공" if leave5.get("ok") else "실패"
        term_print(
            f"\n5. 목록 후·play 전 이탈(미재생): {mark} {verdict} — {leave5.get('message', '')}"
        )
        term_print(
            f"   ch {leave5.get('channel_number')} → {leave5.get('escape_channel')} "
            f"(register_cue={leave5.get('saw_register_cue')}, play={leave5.get('saw_play')}, "
            f"impression={leave5.get('saw_impression')})"
        )
    else:
        term_print("\n5. 목록 후·play 전 이탈(미재생): (미실행)")

    kids_results = _run_checklist.get("kids_watermark") or {}
    ui_map = _run_checklist.get("kids_watermark_ui") or {}
    c6 = _run_checklist.get("kids_check6")
    if isinstance(c6, dict) and c6.get("done"):
        log_ok = bool(c6.get("log_ok"))
        ui_ok = bool(c6.get("ui_ok"))
        ok = bool(c6.get("ok"))
        mark = "✓" if ok else "✗"
        verdict = "성공" if ok else "실패"
        term_print(
            f"\n6. 키즈 채널 워터마크: {mark} {verdict} "
            f"(logcat={'✓' if log_ok else '✗'}, "
            f"광고방송OCR={'✓' if ui_ok else '✗'})"
        )
        term_print(f"   ch {c6.get('channel')}")
    elif kids_results:
        log_ok_count = sum(1 for v in kids_results.values() if v)
        ui_ok_count = sum(1 for v in ui_map.values() if v)
        ch_seen = sorted({ch for (_, ch) in kids_results.keys()})
        all_log_ok = log_ok_count == len(kids_results)
        all_ui_ok = ch_seen and ui_ok_count == len(ch_seen)
        ok = all_log_ok and all_ui_ok
        mark = "✓" if ok else "✗"
        verdict = "성공" if ok else "실패"
        term_print(
            f"\n6. 키즈 채널 워터마크: {mark} {verdict} "
            f"(logcat {log_ok_count}/{len(kids_results)}, "
            f"광고방송OCR {ui_ok_count}/{len(ch_seen) if ch_seen else 0})"
        )
        for ch in ch_seen:
            log_marks = [
                kids_results.get((d, ch))
                for d in device_ips
                if (d, ch) in kids_results
            ]
            log_ok_ch = log_marks and all(log_marks)
            ui_ok_ch = ui_map.get(ch, False)
            term_print(
                f"   ch {ch}: logcat={'✓' if log_ok_ch else '✗'} "
                f"광고방송OCR={'✓' if ui_ok_ch else '✗'}"
            )
    else:
        term_print("\n6. 키즈 채널 워터마크: (미시청)")

    if _critical_issues:
        term_print(
            f"\n⚠ Anypoint 관련 치명 이슈: {len(_critical_issues)}건"
        )
        for ts, dev, needle, snippet in _critical_issues[-10:]:
            term_print(f"   {ts} [{dev}] {needle}")
    else:
        term_print("\n⚠ Anypoint 관련 치명 이슈: 없음")

    if is_kids_prime_time():
        term_print(
            f"\n   현재 {datetime.now().strftime('%H:%M')} — 키즈 prime time 구간입니다."
        )

    term_print(f"{'=' * 60}\n")


def _check_mark(ok):
    if ok is None:
        return "⏭"
    return "✅" if ok else "❌"


def _zip_log_for_chat(log_path):
    """원본 logcat 을 zip 으로 압축 (Chat 200MB 제한 대응)."""
    if not log_path or not os.path.isfile(log_path):
        return None
    zip_path = os.path.splitext(log_path)[0] + "_chat.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(log_path, arcname=os.path.basename(log_path))
    return zip_path


def build_google_chat_report(device_ips, title="*STB QA 결과*", *, include_log_attachments=True):
    """Google Chat 전송용 요약 텍스트 + 첨부 이미지 경로 리스트."""
    lines = [
        f"{title} — {', '.join(device_ips) or '(no device)'}",
        current_time_str(),
        "",
    ]

    versions = _run_checklist.get("versions") or {}
    if _run_checklist.get("versions_skipped"):
        s1 = None
    else:
        s1 = (
            all(all(v.values()) for v in versions.values() if isinstance(v, dict))
            if versions
            else None
        )
    lines.append(f"{_check_mark(s1)} 1. S/W ver.")
    for device, vmap in versions.items():
        if isinstance(vmap, dict):
            lines.append(
                f"     · {device}: FW {vmap.get('firmware', '?')} / "
                f"SDK {vmap.get('sdk', '?')} / Agent {vmap.get('agent', '?')}"
            )

    ad2 = _run_checklist.get("ad_playback")
    s2 = bool(ad2.get("ok")) if isinstance(ad2, dict) and ad2.get("done") else None
    lines.append(f"{_check_mark(s2)} 2. 광고 재생(내부 소재)")
    if isinstance(ad2, dict):
        for device, info in (ad2.get("per_device") or {}).items():
            if isinstance(info, dict):
                lines.append(
                    f"     · {device}: playTime {info.get('playtime_sum_sec')}초"
                    f"/cue {info.get('expected_playtime_sec')}초, "
                    f"impression {info.get('impression_count')}건, "
                    f"API {'200' if info.get('api_200') else 'X'}"
                )

    if _run_checklist.get("google_skipped"):
        lines.append("⏭ 3. 광고 재생(구글) — 생략")
    else:
        g = _run_checklist.get("google_ad") or {}
        lines.append(f"{_check_mark(google_check3_all_passed())} 3. 광고 재생(구글)")
        for sub in GOOGLE_CHECK3_SUBTESTS:
            r = g.get(sub)
            s = bool(r.get("ok")) if isinstance(r, dict) and r.get("done") else None
            lines.append(f"   {_check_mark(s)} {GOOGLE_CHECK3_LABELS[sub]}")
            if isinstance(r, dict):
                ev = r.get("events") or []
                if ev:
                    lines.append(f"     · events: {', '.join(ev)}")
                if r.get("message"):
                    lines.append(f"     · {r.get('message')}")

    leave4 = _run_checklist.get("leave_during_ad")
    s4 = bool(leave4.get("ok")) if isinstance(leave4, dict) and leave4.get("done") else None
    lines.append(f"{_check_mark(s4)} 4. 재생 중 이탈 → impression")
    if isinstance(leave4, dict) and leave4.get("done"):
        sess = leave4.get("session_playtime_ms", 0) / 1000
        delta = leave4.get("playtime_delta_ms", 0) / 1000
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"     · ch {leave4.get('channel_number')}→{leave4.get('escape_channel')} "
            f"play→stop {sess:.1f}초 ≈ playTime합 "
            f"{leave4.get('total_play_time_sec', 0)}초 (차이 {sign}{delta:.1f}초)"
        )

    leave5 = _run_checklist.get("leave_before_play")
    s5 = bool(leave5.get("ok")) if isinstance(leave5, dict) and leave5.get("done") else None
    lines.append(f"{_check_mark(s5)} 5. play 전 이탈 → 미재생")
    if isinstance(leave5, dict) and leave5.get("done"):
        lines.append(
            f"     · ch {leave5.get('channel_number')}→{leave5.get('escape_channel')} "
            f"register_cue={leave5.get('saw_register_cue')} "
            f"play={leave5.get('saw_play')} impression={leave5.get('saw_impression')}"
        )

    c6 = _run_checklist.get("kids_check6")
    s6 = bool(c6.get("ok")) if isinstance(c6, dict) and c6.get("done") else None
    lines.append(f"{_check_mark(s6)} 6. 키즈 채널 워터마크")
    if isinstance(c6, dict) and c6.get("done"):
        lines.append(
            f"     · ch {c6.get('channel')} "
            f"logcat(kid=true→isKid→watermark.png) "
            f"{'✓' if c6.get('log_ok') else '✗'}, "
            f"'광고방송' OCR {'✓' if c6.get('ui_ok') else '✗'}"
        )

    if _critical_issues:
        lines.append("")
        lines.append(f"⚠ Anypoint 관련 치명 이슈 {len(_critical_issues)}건:")
        for ts, dev, needle, _snippet in _critical_issues[-5:]:
            lines.append(f"   {ts} {dev} {needle}")

    images = []
    # Chat: 5초 간격 캡처 중 시인성 있는 *_chat.png 만 (흰 배경 제외)
    chat_candidates = [
        e
        for e in (_run_checklist.get("ad_broadcast_ui") or [])
        if e.get("chat_path")
        and e.get("badge_visible")
        and os.path.isfile(e.get("chat_path") or "")
    ]
    if chat_candidates:
        preferred = [e for e in chat_candidates if e.get("chat_preferred")]
        pool = preferred or chat_candidates
        pool.sort(
            key=lambda e: float(e.get("visibility_score") or 0.0),
            reverse=True,
        )
        images.append(pool[0]["chat_path"])
    images = list(dict.fromkeys(images))[-4:]

    if include_log_attachments:
        for path in (_run_log_files or {}).values():
            zip_path = _zip_log_for_chat(path)
            if zip_path and os.path.isfile(zip_path):
                images.append(zip_path)

    return "\n".join(lines), images


_chat_report_sent = False
_run_device_ips = []
_run_log_files = {}


_chat_space_warned = False


def _resolve_google_chat_space() -> str:
    """GOOGLE_CHAT_SPACE env, 미설정 시 DEFAULT. 0/false/off/none 이면 비활성."""
    raw = os.environ.get("GOOGLE_CHAT_SPACE")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_GOOGLE_CHAT_SPACE
    space = str(raw).strip()
    if space.lower() in ("0", "false", "n", "no", "off", "none", "-"):
        return ""
    return space


def _send_google_chat(device_ips, title, label, *, include_log_attachments=True) -> bool:
    global _chat_space_warned
    space = _resolve_google_chat_space()
    if not space:
        if not _chat_space_warned:
            _chat_space_warned = True
            term_print(
                f"{current_time_str()} [Google Chat] 스킵 — "
                f"GOOGLE_CHAT_SPACE=0 (비활성)"
            )
        return False
    try:
        from component.chat_notify import send_report

        text, images = build_google_chat_report(
            device_ips,
            title=title,
            include_log_attachments=include_log_attachments,
        )
        send_report(space, text, images)
        attach_n = sum(1 for p in (images or []) if p and os.path.isfile(p))
        term_print(
            f"{current_time_str()} [Google Chat] {label} 전송 완료 "
            f"(요약+첨부 {attach_n}개, zip은 별도 메시지) → {space}"
        )
        return True
    except Exception as e:
        term_print(f"{current_time_str()} [Google Chat] {label} 전송 실패: {e}")
        return False


def post_interim_results_to_google_chat(device_ips):
    """1차 리포트: 체크리스트 한 바퀴 완료(실패 항목 포함) — 추가 재시도 예정."""
    _send_google_chat(
        device_ips,
        title="*STB QA 1차 결과 (실패 항목 추가 재시도 예정)*",
        label="1차 결과",
    )


def post_results_to_google_chat(device_ips):
    """최종 리포트: 결과 요약 + 캡처를 Google Chat 으로 전송 (실행당 1회)."""
    global _chat_report_sent
    if _chat_report_sent:
        return
    if _send_google_chat(device_ips, title="*STB QA 최종 결과*", label="최종 결과"):
        _chat_report_sent = True


def post_aborted_results_to_google_chat():
    """사용자 강제 종료(Ctrl+C/SIGTERM 등) 시 현재까지 결과를 Chat 으로 전송 (1회)."""
    global _chat_report_sent
    if _chat_report_sent:
        return
    if _send_google_chat(
        _run_device_ips,
        title="*STB QA 중단 결과 (사용자 종료 — 여기까지)*",
        label="중단 결과",
    ):
        _chat_report_sent = True


def _handle_termination_signal(signum, frame):
    """Ctrl+C / Ctrl+Break / SIGTERM 수신 시 현재까지 결과 전송 후 종료."""
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    term_print(f"\n{current_time_str()} [중단] 신호 {name} 수신 — 현재까지 결과 전송")
    stop_all_device_logs()
    post_aborted_results_to_google_chat()
    _release_single_instance_lock()
    close_terminal_log_mirror()
    os._exit(130)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_pid(lock_path: str):
    try:
        with open(lock_path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _remove_stale_single_instance_lock():
    if not os.path.isfile(SINGLE_INSTANCE_LOCK_FILE):
        return
    pid = _read_lock_pid(SINGLE_INSTANCE_LOCK_FILE)
    if pid is None or not _is_pid_alive(pid):
        try:
            os.remove(SINGLE_INSTANCE_LOCK_FILE)
        except OSError:
            pass


def _acquire_single_instance_lock() -> bool:
    """동일 스크립트 2개 이상 동시 실행 방지."""
    global _single_instance_lock_fp
    os.makedirs(LOG_DIR, exist_ok=True)
    _remove_stale_single_instance_lock()
    try:
        fd = os.open(
            SINGLE_INSTANCE_LOCK_FILE,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        pid = _read_lock_pid(SINGLE_INSTANCE_LOCK_FILE)
        holder = f"PID {pid}" if pid else "다른 프로세스"
        print(
            f"{current_time_str()} [중복 실행] Default behavior.py 가 "
            f"이미 실행 중입니다 ({holder}).\n"
            f"  lock: {SINGLE_INSTANCE_LOCK_FILE}\n"
            f"  기존 프로세스 종료 후 다시 실행하세요.",
            file=sys.stderr,
        )
        return False
    _single_instance_lock_fp = os.fdopen(fd, "w", encoding="utf-8")
    _single_instance_lock_fp.write(str(os.getpid()))
    _single_instance_lock_fp.flush()
    atexit.register(_release_single_instance_lock)
    return True


def _release_single_instance_lock():
    global _single_instance_lock_fp
    if _single_instance_lock_fp is not None:
        try:
            _single_instance_lock_fp.close()
        except OSError:
            pass
        _single_instance_lock_fp = None
    try:
        if os.path.isfile(SINGLE_INSTANCE_LOCK_FILE):
            pid = _read_lock_pid(SINGLE_INSTANCE_LOCK_FILE)
            if pid is None or pid == os.getpid():
                os.remove(SINGLE_INSTANCE_LOCK_FILE)
    except OSError:
        pass


def _install_termination_handlers():
    for sig_name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_termination_signal)
        except (ValueError, OSError):
            pass


def _configure_stdio_utf8():
    """Windows 콘솔·stdout 을 UTF-8 로 (Tee-Object cp949 깨짐 완화)."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["cmd", "/c", "chcp", "65001 >nul"],
                shell=True,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main():
    _configure_stdio_utf8()
    if not _acquire_single_instance_lock():
        sys.exit(1)
    _install_termination_handlers()
    tlog = open_terminal_log_mirror()
    if tlog:
        term_print(f"{current_time_str()} [로그] UTF-8 터미널 미러 → {tlog}")
    device_ips, log_filename = prompt_run_config()
    global _run_device_ips
    _run_device_ips = list(device_ips)
    term_print(
        f"{current_time_str()} [설정] 연결 STB {len(device_ips)}대: "
        f"{', '.join(device_ips)}"
    )
    final_channels = prompt_final_channels(device_ips)
    escape_ch = _escape_channel_from_env() or next(iter(final_channels.values()), "")
    if escape_ch:
        term_print(
            f"{current_time_str()} [설정] 이탈/마지막 채널: {escape_ch} "
            f"(키패드 '{format_channel_keypad_digits(escape_ch)}')"
        )

    skip_reboot = os.environ.get("SKIP_REBOOT", "").strip().lower() in (
        "1",
        "true",
        "y",
        "yes",
    )
    skip_reboot_raw = os.environ.get("SKIP_REBOOT", "(미설정)")
    term_print(
        f"{current_time_str()} [설정] SKIP_REBOOT={skip_reboot_raw} → "
        f"{'재부팅·버전확인 없음' if skip_reboot else '시작 시 재부팅 1회 + 버전 확인'}"
    )
    version_only = _env_truthy("VERSION_ONLY")
    skip_version = _env_truthy("SKIP_VERSION_CHECK")

    if skip_version or skip_reboot:
        _run_checklist["versions_skipped"] = True
        if skip_version:
            term_print(
                f"{current_time_str()} [SKIP_VERSION_CHECK] 1. S/W ver. 확인 스킵"
            )
        else:
            term_print(
                f"{current_time_str()} [SKIP_REBOOT=1] 1. S/W ver. 확인 스킵 "
                f"(재부팅·logcat 버전 수집 없이 편성 모니터링)"
            )

    catalog = load_channel_catalog()
    term_print(
        f"{current_time_str()} 채널 카탈로그 {len(catalog)}건 로드 "
        f"({get_catalog_path()})"
    )
    if catalog and len(catalog) < 50:
        term_print(
            "  ※ 카탈로그가 일부만 있습니다. API 전체 응답으로 "
            "stb-rpa/data/lgu_channel_catalog.json 을 교체하세요."
        )

    print(f"\n{current_time_str()} 디바이스 연결 중...")
    connect_all_devices(device_ips)

    if skip_version or skip_reboot:
        pass
    elif version_only:
        print(f"\n{current_time_str()} [VERSION_ONLY] 재부팅 후 버전 확인만 수행")
        reboot_devices(device_ips, reason="VERSION_ONLY")
        if not wait_for_devices_after_reboot(device_ips):
            print("재부팅 후 디바이스 준비 실패. 종료합니다.")
            return
        term_print(
            f"\n{current_time_str()} 재부팅 완료 — Firmware / SDK / Agent 버전 확인"
        )
        collect_versions_after_reboot(device_ips)
        print_checklist_progress(device_ips)
        print_final_checklist_summary(device_ips)
        term_print(f"\n{current_time_str()} [VERSION_ONLY] 완료")
        close_terminal_log_mirror()
        return
    elif not ensure_versions_with_optional_reboot(
        device_ips, skip_reboot=skip_reboot
    ):
        close_terminal_log_mirror()
        return

    apply_pending_api_endpoints(device_ips)

    if _run_checklist.get("google_skipped"):
        term_print(
            f"{current_time_str()} [SKIP_GOOGLE_CHECK] 체크 3-A~C 구글 광고 생략 "
            f"(구글 편성 없음 등 — 2·4·5·6은 동일 테스트 STB에서 계속)"
        )
    else:
        term_print(
            f"{current_time_str()} 체크 3-A/B/C 구글: "
            f"Quartile+COMPLETE / 이탈→tracking중단 / SKIPPABLE→SKIPPED "
            f"(편성 3줄 권장)"
        )

    print(f"\n{current_time_str()} 로그 저장 시작 (전체 logcat, 필터 없음)")
    os.makedirs(LOG_DIR, exist_ok=True)
    log_threads, log_files = save_multiple_devices_logs(
        device_ips,
        LOG_DIR,
        filters=None,
        log_filename=log_filename,
        on_log_line=on_log_line_for_monitoring,
    )
    global _run_log_files
    _run_log_files = {
        d: os.path.join(LOG_DIR, fn) for d, fn in (log_files or {}).items()
    }

    from component.gspread_reader import sheet_tab_name

    tab_hint = f"{sheet_tab_name()} 모니터링"
    term_print(
        f"\n{current_time_str()} [편성표] Sheet {SCHEDULE_SPREADSHEET_KEY} "
        f"탭 '{tab_hint}' / ART(U+)"
    )
    print(f"\n{current_time_str()} 편성표 데이터 로드 중...")
    data = load_schedule_data(
        SERVICE_ACCOUNT_PATH,
        spreadsheet_key=SCHEDULE_SPREADSHEET_KEY,
        source=os.environ.get("SCHEDULE_SOURCE", "drive"),
        section="uplus",
    )

    print(f"\n{current_time_str()} 광고 편성 채널 모니터링 시작")
    monitor_and_switch_channels_with_data(
        data,
        device_ips,
        final_channels=final_channels,
    )

    time.sleep(5)
    for device_id, channel in final_channels.items():
        keypad = format_channel_keypad_digits(channel)
        term_print(
            f"\n{current_time_str()} 마지막 채널 복귀: "
            f"{device_id} → ch {channel} (키패드 '{keypad}')"
        )
        ok = switch_channel_with_verify(channel, [device_id])
        mark = "완료" if ok else "⚠ OSD 미확인"
        term_print(f"{current_time_str()} ch {channel} 전환 {mark}")

    term_print(f"\n{current_time_str()} [로그] logcat 캡처 종료 (파일 확정)")
    stop_all_device_logs()
    for t in log_threads:
        t.join(timeout=5)

    counts = count_impression_logs(LOG_DIR, log_files)
    print_impression_log_counts(counts)

    print_final_checklist_summary(device_ips)
    post_results_to_google_chat(device_ips)
    print(f"\n{current_time_str()} 완료")
    _release_single_instance_lock()
    close_terminal_log_mirror()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        term_print(f"\n{current_time_str()} [중단] KeyboardInterrupt — 현재까지 결과 전송")
        stop_all_device_logs()
        post_aborted_results_to_google_chat()
    finally:
        _release_single_instance_lock()
        close_terminal_log_mirror()
