import os ,sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import time
import subprocess
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re
from component.setting import AirtelSetting

# 슬랙 설정

# POST 요청 설정
url = "http://am-device-app-prod2.ap-northeast-1.elasticbeanstalk.com/v3/devices/commands"
headers = {"Content-Type": "application/json"}
BODY = {
    "serverMessageType": "UpdateAssetCommand",
    "deviceIds": [57125576]
}

LOG_DIR = "log"
RECORD_DIR = "recordings"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RECORD_DIR, exist_ok=True)

def send_post_request():
    try:
        response = requests.post(url, headers=headers, json=BODY)
        response.raise_for_status()
        return BODY["deviceIds"][0]
    except requests.exceptions.RequestException as e:
        notify(f"[오류] POST 요청 실패: {e}")
        exit()

def notify(message):
    print(f"[notify] {message}")



def notify_file(file_path, title=None):
    print(f"[notify skipped] file={file_path} title={title}")



def switch_channel(channel_number):
    keyevent_map = {str(i): 7 + i for i in range(10)}
    for digit in str(channel_number):
        if digit in keyevent_map:
            subprocess.run(["adb", "-s", AirtelSetting.device_ip, "shell", "input", "keyevent", str(keyevent_map[digit])])
            time.sleep(0.5)

def clear_logcat():
    subprocess.run(["adb", "-s", AirtelSetting.device_ip, "logcat", "-c"])

def start_logcat():
    return subprocess.Popen(["adb", "-s", AirtelSetting.device_ip, "logcat", "-v", "time"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def start_recording(filename):
    path = os.path.join(RECORD_DIR, filename)
    return subprocess.Popen(["adb", "-s", AirtelSetting.device_ip, "shell", "screenrecord", f"/sdcard/{filename}"]), path

def stop_recording(proc, filename):
    proc.terminate()
    time.sleep(1)
    subprocess.run(["adb", "-s", AirtelSetting.device_ip, "pull", f"/sdcard/{filename}", os.path.join(RECORD_DIR, filename)])
    # subprocess.run(["adb", "-s", AirtelSetting.device_ip, "shell", "rm", f"/sdcard/{filename}"])

def sheet_tab_name():
    return datetime.now().strftime("%y%m%d") + " skb모니터링"

def load_ad_schedule():
    try:
        from component.schedule_loader import load_ad_schedule as _load_schedule_rows
        return _load_schedule_rows(AirtelSetting.SERVICE_ACCOUNT_PATH, section="skb")
    except Exception as e:
        notify(f"[오류] 광고 스케줄 로드 실패: {e}")
        exit()

def parse_ads_end_line(lines):
    pattern = r'ads will end in (\d+) ms'
    matched = []
    for line in lines:
        match = re.search(pattern, line)
        if match:
            ms = int(match.group(1))
            matched.append((line, ms))
    return matched

def parse_device_id(log_line):
    match = re.search(r'deviceId=(\d+)', log_line)
    return match.group(1) if match else None

def parse_pts(line):
    try:
        splice = int(re.search(r'splicePts: (\d+)', line).group(1))
        current = int(re.search(r'currentPts: (\d+)', line).group(1))
        return (splice - current) / 90
    except:
        return None

def parse_playtime(line):
    match = re.search(r'playTime=(\d+)', line)
    return int(match.group(1)) if match else 0

def parse_splice_command_data(lines):
    return [line for line in lines if r'splice command data' in line]

def parse_adplayitem_section(lines):
    return [line for line in lines if r'AdPlayItem: AdPlayItem' in line]

def parse_impression_log_section(lines):
    return [line for line in lines if r'   ImpressionLog(' in line]

def parse_play_stop_times(lines):
    play_time, stop_time = None, None
    for line in lines:
        if 'play ================================' in line:
            play_time = datetime.now()
        if 'stop ================================' in line:
            stop_time = datetime.now()
    return play_time, stop_time

def monitor_ads(device_id, ad_schedule):
    for ad in ad_schedule:
        now = datetime.now()
        ad_time = ad['ad_time']
        channel = ad['channel']
        channel_name = ad['channel_name']
        switch_time = ad_time - timedelta(minutes=2, seconds=30)
        monitor_time = ad_time - timedelta(seconds=15)

        if now >= ad_time:
            continue

        while datetime.now() < switch_time:
            print(f"{datetime.now()} 대기 중... (채널 전환 예정: {switch_time})")
            time.sleep(5)

        print(f"[전환] {channel_name} ({channel}) 채널로 전환")
        switch_channel(channel)

        time_to_monitor = (monitor_time - datetime.now()).total_seconds()
        if time_to_monitor > 0:
            print(f"{datetime.now()} 광고 감시 대기 중... (감시 시작 예정: {monitor_time})")
            time.sleep(time_to_monitor)

        clear_logcat()
        proc = start_logcat()

        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{channel}.mp4"
        recorder_proc, filepath = start_recording(filename)

        all_lines = []
        found_receive_cue = False
        cue_check_start_time = time.time()  # cue_check_start_time을 채널 전환 직후로 설정

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            decoded_line = line.decode(errors='ignore').strip()
            all_lines.append(decoded_line)

            if r'receive cue...' in decoded_line:
                found_receive_cue = True

            if 'success to send impression-logs' in decoded_line:
                print("[종료] 광고 감시 완료 로그 감지")
                break

            # 60초 이내 receive cue 미감지 시
            if not found_receive_cue and (time.time() - cue_check_start_time) > 90:
                notify(f"[스킵] {channel_name}({channel}) 채널에서 90초 이내 receive cue 미감지. 다음 채널로 이동합니다.")
                break  # 현재 채널 감시 실패 → 루프 끝내고 다음 채널로 이동

        proc.terminate()
        stop_recording(recorder_proc, filename)

        # 감시 성공 조건: receive cue 감지됨 & impression log 감지됨
        impression_logs = parse_impression_log_section(all_lines)
        if not found_receive_cue or not impression_logs:
            continue  # 다음 채널로

        ad_playitem_lines = parse_adplayitem_section(all_lines)
        splice_lines = parse_splice_command_data(all_lines)
        play_time, stop_time = parse_play_stop_times(all_lines)

        total_playtime = sum(parse_playtime(line) for line in impression_logs)
        matched_device_id_count = sum(1 for line in impression_logs if parse_device_id(line) == str(device_id))
        ads_end_lines = parse_ads_end_line(all_lines)

        splice_results = []
        for line in splice_lines:
            result = parse_pts(line)
            if result:
                # msg = f"splice command data 계산값 : {result:.2f} ms - #{line}"
                msg = f"splice command data 계산값 : {result:.2f} ms"
                print(msg)
                splice_results.append(msg)

        ads_end_report = "\n".join([f"{line} (→ {ms} ms)" for line, ms in ads_end_lines])

        log_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file_path = os.path.join(LOG_DIR, f"ImpressionLog_{log_time}.log")
        with open(log_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(impression_logs + splice_lines + splice_results))

        notify_file(log_file_path, "Impression Log")

        summary = (
            f"[완료] 광고 감시 결과\n"
            f"- 채널: {channel_name} ({channel})\n"
            f"- AdPlayItem 수: {len(ad_playitem_lines)}\n"
            f"- ImpressionLog 수: {len(impression_logs)}\n"
            f"- deviceId 일치 수: {matched_device_id_count}\n"
            f"- 총 playtime: {total_playtime} ms \n"
            f"- PTS 비교 결과:\n{chr(10).join(splice_results) if splice_results else '(해당 없음)'}\n"
            f"- ad-play will end in :\n{ads_end_report if ads_end_report else '(해당 없음)'}"
        )

        notify(summary)
        return  # 첫 성공 시 루프 종료


if __name__ == "__main__":
    device_id = AirtelSetting.device_id
    schedule = load_ad_schedule()
    if not schedule:
        notify("[경고] 광고 스케줄 없음")
        exit()
    monitor_ads(device_id, schedule)
