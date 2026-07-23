# Default behavior — 참고 (한글)

`SKILL.ko.md`에서 체크리스트/env 상세가 필요할 때만 읽는다. 영문판: [reference.md](reference.md).

## 체크리스트 1–6

| # | 키 / 상태 | PASS 요지 (이 범위를 넘어 추측하지 말 것) |
|---|-----------|------------------------------------------|
| 1 | `_run_checklist["versions"]` | 재부팅 후 Firmware(`V.xx.xx.xxxx`) + SDK + Agent (logcat/getprop). `SKIP_REBOOT=1` 또는 `SKIP_VERSION_CHECK=1`이면 스킵. |
| 2 | `ad_playback` | **비키즈** 채널 내부 광고: cue−2초 ≤ ImpressionLog `playTime` 합 ≤ cue; impression API 200. 시도 횟수 + check2 보너스. |
| 3 | `google_ad` 서브 `full_play` / `leave_during` / `skip_ok` | 3-A Quartile+COMPLETE; 3-B 이탈 후 tracking 중단; 3-C SKIPPABLE→SKIPPED. `SKIP_GOOGLE_CHECK=1`이면 전부 스킵. |
| 4 | `leave_during_ad` | `play ====` 이후 **벽시계 +30초**에 이탈; 이후 player play→stop ≈ ImpressionLog playTime 합 (±`PLAYTIME_MATCH_TOLERANCE_MS`). |
| 5 | `leave_before_play` | `register cue` 후 play 전 이탈 → play/impression 없음. |
| 6 | `kids_check6` + UI | 키즈 ch `311,320-324,328`: kid=true cue / isKid / KidWatermarkManager / `kid_watermark.png`; UI OCR 「광고 방송」 버스트. |

체크리스트 완료(또는 시도 소진) 후: `run_schedule_slot_monitor` — 내부 playTime/impression, 구글 quartile/tracking/skip, 키즈 워터마크+OCR.
`SLOT_AD_START_TIMEOUT_SEC` 내 cue/play 없으면 `[편성 스킵]`.

## 내부 광고 logcat 단계 (`AD_PLAYBACK_PHASES`)

전형적 순서: `register cue` → `ads will play in` → load → play start → `AnypointAdPlayerImpl.play` → callOnPlay → prepareStop → player stop → onStopped → `impression log size` → ImpressionLog → `impression-logs` POST.

## 주요 env 플래그

| Env | 역할 |
|-----|------|
| `STB_DEVICE_IP` / `STB_DEVICE_IPS` | 대상 STB(들). 쉼표/공백/세미콜론 |
| `SKIP_REBOOT` | ps1 기본 `1` — 재부팅 **및** 버전 확인 스킵. `.py` 직접 실행·미설정 시 대화형 y/N (N=스킵) |
| `APPLY_LOCAL_API_ENDPOINT` | `1`+모델이면 비대화형 적용, `0`이면 스킵. 미설정 시 대화형 y/N |
| `STB_LOCAL_API_HOST` | local PC 호스트 (기본 `192.168.10.150`) |
| `STB_LOCAL_API_MODEL` | 경로 모델명 (예: `UHD3`) — 기기 공통일 때 |
| `STB_API_ENDPOINT` | 전체 URL (`http://192.168.10.150/UHD3`) — 설정 시 전 기기 동일 적용 |
| `VERSION_ONLY` | 재부팅+버전만 하고 종료 |
| `SKIP_VERSION_CHECK` | 체크 1만 스킵 |
| `SKIP_GOOGLE_CHECK` | 3-A/B/C 스킵 |
| `CHECKLIST_ONLY` | 체크리스트 후 장기 편성 모니터링 생략 |
| `STB_ESCAPE_CHANNEL` | 이탈/마지막 채널 (기본 `3`) |
| `DRIVE_SCHEDULE_FILE_ID` / `SCHEDULE_SOURCE` / `SCHEDULE_SECTION` | 편성 로드 (`drive`, `uplus`) |
| `GOOGLE_CHAT_SPACE` | (선택) Chat 리포트 스페이스 |
| `STB_TERMINAL_LOG` / `STB_LOG_FILE` | 터미널 미러 / 로그 파일 stem |
| `CHECKLIST_CHECK_MAX_ATTEMPTS` | 체크별 시도 상한 (기본 3) |
| `CHECK2_BONUS_MAX_ATTEMPTS` | 다른 체크 완료 후 체크2 추가 시도 |
| `SLOT_AD_START_TIMEOUT_SEC` | cue/play 없으면 슬롯 스킵 (기본 90) |
| `MONITOR_GOOGLE_AUTO_SKIP` | 모니터링 중 SKIPPABLE에 OK 키 자동 입력 |
| `CHANNEL_SWITCH_CLEAR_LOG` | 위험; 기본 off |
| `AD_SYNC_PACKAGE` | AD_SYNC 브로드캐스트용 LGU 패키지 |

## 존중해야 할 전역 상태

- `_run_checklist`, `_check_attempt_counts`, `_checklist_extra_phase`
- `_active_ad_trackers`, `_active_google_trackers`, `_active_kids_watermark_trackers`
- `_channel_switch_lock`, `_pending_tune_targets`
- `_critical_issues` (Anypoint 관련 crash/ANR만 리포트)
- 단일 인스턴스 락: `test_log/default_behavior.lock`

## 키즈 프라임 우선

`KIDS_CHANNEL_NUMBERS` + 분 ≥ 50 → 체크 6 미완료 시 키즈 슬롯 강제
(`_should_force_kids_prime_priority` / `_skip_non_kids_for_check6`).

## 실행 진입점

```powershell
.\stb-rpa\my.yoon_test\run_default_behavior.ps1 -StbDevices "192.168.x.x"
# UTF-8 콘솔; 기존 "Default behavior.py" 프로세스 종료; 편성 기본값 설정
```

직접 실행:

```powershell
$env:PYTHONUTF8=1
python -u "stb-rpa\my.yoon_test\Default behavior.py"
```
