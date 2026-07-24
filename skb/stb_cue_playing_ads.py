import os
import sys
import re
import time
import subprocess
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from component.setting import SkbSetting



TS_PATTERN         = re.compile(r"^(?P<md>\d{2}-\d{2}) (?P<hms>\d{2}:\d{2}:\d{2}\.\d{3})")
CUE_PATTERN        = re.compile(r"receive cue", re.I)
ADPLAY_PATTERN     = re.compile(r"AdPlayItem\s*:\s*AdPlayItem ad:Ad\(", re.I)
IMPRESSION_PATTERN = re.compile(r"   ImpressionLog\([^)]*playTime=(\d+)", re.I)
POST_PATTERN = re.compile(r"POST https://.*/v3/device/impression-logs", re.I)
STATUS_PATTERN = re.compile(r"200 https://.*/v3/device/impression-logs", re.I)


# 타이밍 상수
AD_SKIP_DELAY          = 50     # AdPlayItem 이후 채널 스킵까지 대기(초)
RECEIVE_CUE_OFFSET      = 20     # 광고 시작 –20 s (receive cue 예상 오프셋)
RECEIVE_CUE_TOLERANCE   = 1      # 허용 오차(초)
RECEIVE_CUE_TIMEOUT     = 180    # 채널 변경 후 receive cue 감지 타임아웃(3 분)
POST_IMPRESSION_TIMEOUT = 180    # 스킵 후 Impression/POST/200 대기 타임아웃(3 분)
ADDRAD_PATTERN = re.compile(r"AddrAD", re.I)


LOG_DIR = "log"

os.makedirs(LOG_DIR, exist_ok=True)


def notify(message):
    print(f"[notify] {message}")



def notify_file(file_path, title=None):
    print(f"[notify skipped] file={file_path} title={title}")



def adb(*cmd):
    return subprocess.run(["adb", "-s", SkbSetting.device_ip, *map(str, cmd)], capture_output=True)


def switch_channel(channel: str):
    key_map = {str(i): 7 + i for i in range(20)}
    for d in channel:
        if d in key_map:
            adb("shell", "input", "keyevent", key_map[d])
            time.sleep(0.35)


def clear_logcat():
    adb("logcat", "-c")


def start_logcat():
    return subprocess.Popen(["adb", "-s", SkbSetting.device_ip, "logcat", "-v", "time"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def parse_ts(line: str):
    m = TS_PATTERN.match(line)
    if not m:
        return None
    y = datetime.now().year
    try:
        return datetime.strptime(f"{y}-{m.group('md')} {m.group('hms')}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def sheet_tab_name():
    return datetime.now().strftime("%y%m%d skb모니터링")


def load_schedule():
    from component.schedule_loader import load_ad_schedule as _load_schedule_rows
    from datetime import timedelta
    rows = _load_schedule_rows(SkbSetting.SERVICE_ACCOUNT_PATH, section="skb")
    out = []
    for r in rows:
        ad_dt = r["ad_time"]
        if ad_dt < datetime.now():
            ad_dt += timedelta(days=1)
        out.append({"name": r["channel_name"], "ch": str(r["channel"]), "ad_time": ad_dt})
    return sorted(out, key=lambda x: x["ad_time"])

def analyze(lines: list[str], ad_time: datetime):
    cue_ts = ad_ts = None
    impr: list[tuple[datetime, int]] = []
    post = status = False
    for ln in lines:
        ts = parse_ts(ln)
        low = ln.lower()
        if CUE_PATTERN.search(low):
            cue_ts = ts
        elif ADPLAY_PATTERN.search(ln):
            ad_ts = ts
        elif (m := IMPRESSION_PATTERN.search(ln)) and ts:
            impr.append((ts, int(m.group(1))))
        elif POST_PATTERN.search(ln):
            post = True
        elif STATUS_PATTERN.search(ln):
            status = True

    # 총 playTime 합산
    total_playtime = sum(p for _, p in impr)

    res = {
        "cue_delta": None if cue_ts is None else (ad_time - cue_ts).total_seconds(),
        "cue_ok":   cue_ts is not None and abs((ad_time - cue_ts).total_seconds() - RECEIVE_CUE_OFFSET) <= RECEIVE_CUE_TOLERANCE,
        "adplay":   ad_ts is not None,
        "impr_cnt": len(impr),
        "total_playtime": total_playtime,
        "post_ok":  post and status,
    }
    return res

def monitor(ads):
    for ad in ads:
        name, ch, ad_time = ad["name"], ad["ch"], ad["ad_time"]
        if datetime.now() >= ad_time:
            continue

        # 채널 전환 2 분 전
        switch_time = ad_time - timedelta(minutes=2)
        while datetime.now() < switch_time:
            time.sleep(0.5)
        print(f"[채널 전환] {name}({ch}) {datetime.now():%H:%M:%S}")
        switch_channel(ch)

        # receive cue 대기 (최대 3 분)
        clear_logcat()
        proc = start_logcat()
        lines: list[str] = []
        addrad_lines: list[str] = []
        cue_detected = False
        cue_start = time.time()
        while time.time() - cue_start < RECEIVE_CUE_TIMEOUT:
            raw = proc.stdout.readline()
            if not raw:
                continue
            ln = raw.decode("utf-8", errors="ignore").rstrip()
            lines.append(ln)
            if ADDRAD_PATTERN.search(ln):
                addrad_lines.append(ln)
            if CUE_PATTERN.search(ln.lower()):
                cue_detected = True
                print(f"[receive cue] 감지 {name} {datetime.now():%H:%M:%S}")
                break
        if not cue_detected:
            proc.terminate()
            notify(f"[PASS] {name}({ch})  3 분 내 receive cue 미감지 → 다음 스케줄로")
            continue  # 다음 광고로

        # 이후 AdPlayItem ~ Impression/POST/200 감시
        adplay_seen = False
        skip_start = None
        impr_found = post_found = status_found = False
        while True:
            raw = proc.stdout.readline()
            if not raw:
                continue
            ln = raw.decode("utf-8", errors="ignore").rstrip()
            lines.append(ln)
            if ADDRAD_PATTERN.search(ln):
                addrad_lines.append(ln)

            if ADPLAY_PATTERN.search(ln) and not adplay_seen:
                adplay_seen = True
                time.sleep(AD_SKIP_DELAY)
                adb("shell", "input", "keyevent", 166)
                skip_start = time.time()
                print(f"[스킵] {name} 광고 스킵, 후속 로그 대기")
                continue

            if skip_start:
                if IMPRESSION_PATTERN.search(ln):
                    impr_found = True
                if POST_PATTERN.search(ln):
                    post_found = True
                if STATUS_PATTERN.search(ln):
                    status_found = True
                if impr_found and post_found and status_found:
                    print("[조기 종료] 모든 로그 수집")
                    break
                if time.time() - skip_start > POST_IMPRESSION_TIMEOUT:
                    print("[타임아웃] 후속 로그 미감지")
                    break

        proc.terminate()

        res = analyze(lines, ad_time)
        msg = [f"*{name}({ch}) 결과* [{ad_time:%H:%M:%S}]",
        f"- AdPlayItem: {'OK' if res['adplay'] else 'FAIL'}",
        f"- ImpressionLog: {res['impr_cnt']}건",
        f"- playTime 총합: {res['total_playtime']}",
        f"- 노출 POST/200: {'OK' if res['post_ok'] else 'FAIL'}"]
        notify("\n".join(msg))

        addrad_path = os.path.join(LOG_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{ch}_AddrAD.log")
        with open(addrad_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(addrad_lines))
        notify_file(addrad_path, f"{name}_{ch}_AddrAD_log")


        return

if __name__ == "__main__":
    try:
        schedule = load_schedule()
        if not schedule:
            notify("[경고] 오늘 모니터링할 광고 편성이 없습니다.")
            sys.exit(0)
        monitor(schedule)
    except Exception as e:
        notify(f"[오류] {e}")
        raise
