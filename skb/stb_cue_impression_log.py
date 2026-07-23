import os , sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import time
import subprocess
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re
import json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from component.setting import SkbSetting, Slack

# 설정
slack_client = WebClient(token=Slack.SLACK_BOT_TOKEN)
LOG_DIR = "log"
RECORD_DIR = "recordings"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RECORD_DIR, exist_ok=True)

url = "http://uplus-device-app-prod2.ap-northeast-2.elasticbeanstalk.com/v3/devices/commands"
headers = {"Content-Type": "application/json"}
BODY = { "serverMessageType": "UpdateAssetCommand", "deviceIds": [57125576], "soId": [1] }

def send_post_request():
    try:
        requests.post(url, headers=headers, json=BODY).raise_for_status()
        return BODY["deviceIds"][0]
    except requests.RequestException as e:
        send_slack_message(f"[오류] POST 요청 실패: {e}")
        exit()

def send_slack_message(message):
    try:
        slack_client.chat_postMessage(channel=Slack.CHANNEL_ID, text=message)
    except SlackApiError as e:
        print(f"Slack 메시지 실패: {e.response['error']}")

def send_slack_file(file_path, title):
    try:
        slack_client.files_upload_v2(channel=Slack.CHANNEL_ID, file=file_path, title=title)
    except SlackApiError as e:
        print(f"Slack 파일 업로드 실패: {e.response['error']}")

def switch_channel(channel_number):
    keyevent_map = {str(i): 7 + i for i in range(10)}
    for digit in str(channel_number):
        if digit in keyevent_map:
            subprocess.run(["adb", "-s", SkbSetting.device_ip, "shell", "input", "keyevent", str(keyevent_map[digit])])
            time.sleep(0.5)

def clear_logcat():
    subprocess.run(["adb", "-s", SkbSetting.device_ip, "logcat", "-c"])

def start_logcat():
    return subprocess.Popen(["adb", "-s", SkbSetting.device_ip, "logcat", "-v", "time"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def start_recording(filename):
    return subprocess.Popen(["adb", "-s", SkbSetting.device_ip, "shell", "screenrecord", f"/sdcard/{filename}"])

def stop_recording(proc, filename):
    proc.terminate()
    time.sleep(1)
    subprocess.run(["adb", "-s", SkbSetting.device_ip, "pull", f"/sdcard/{filename}", os.path.join(RECORD_DIR, filename)])

def sheet_tab_name():
    return datetime.now().strftime("%y%m%d") + " skb모니터링"

def load_ad_schedule():
    try:
        from component.schedule_loader import load_ad_schedule as _load_schedule_rows
        return _load_schedule_rows(SkbSetting.SERVICE_ACCOUNT_PATH, section="skb")
    except Exception as e:
        send_slack_message(f"[오류] 광고 스케줄 로드 실패: {e}")
        exit()

def load_ads():
    with open(SkbSetting.ads_file_path, "r", encoding="utf-8") as f:
        return json.load(f)["ads"]

def parse_impression_log(line):
    pattern = (
        r"   ImpressionLog\(deviceId=(\d+), campaignId=(\d+), adId=(\d+), assetId=(\d+), "
        r"ppId=(\d+), impressionTimeHuman=[^,]+, impressionTime=(\d+), playTime=(\d+), "
        r"soId=(\d+), placementId=(\d+)"
    )
    match = re.search(pattern, line)
    if not match:
        return None
    return {
        "deviceId": int(match.group(1)),
        "campaignId": int(match.group(2)),
        "adId": int(match.group(3)),
        "assetId": int(match.group(4)),
        "ppId": int(match.group(5)),
        "impressionTime": int(match.group(6)),
        "playTime": int(match.group(7)),
        "soId": int(match.group(8)),
        "placementId": int(match.group(9)),
    }


def compare_impression_to_ads(impression, ads):
    try:
        matched = next((ad for ad in ads if
                        ad.get("campaignId") == impression["campaignId"] and
                        ad.get("id") == impression["adId"] and
                        ad.get("asset") and ad["asset"].get("assetId") == impression["assetId"]), None)
    except Exception as e:
        send_slack_message(f"[예외 발생] 광고 매칭 중 오류 발생: {e}")
        return  # 예외 발생 시 해당 로그는 무시하고 다음으로 넘어감

    if not matched:
        print("일치하는 광고 없음")
        return

    # 아래는 기존 검증 로직 유지
    checks = [
        ("campaignId", impression["campaignId"], matched["campaignId"]),
        ("adId", impression["adId"], matched["id"]),
        ("assetId", impression["assetId"], matched["asset"]["assetId"]),
    ]
    for label, actual, expected in checks:
        result = "PASS" if actual == expected else f"FAIL ({actual} ≠ {expected})"
        print(f"[검증] {label}: {actual} vs {expected} → {result}")

    body_deviceIds = BODY.get("deviceIds", [])
    body_soids = BODY.get("soId", [])
    log_deviceId = impression.get("deviceId")
    log_soid = impression.get("soId")

    device_match = log_deviceId in body_deviceIds
    soid_match = log_soid in body_soids

    result = "PASS" if device_match and soid_match else f"FAIL ({log_deviceId}/{log_soid} vs {body_deviceIds}/{body_soids})"
    print(f"[검증] DeviceInfoCheck = deviceId : {log_deviceId}/soId : {log_soid} vs deviceId : {body_deviceIds}/soId : {body_soids} → {result}")

def monitor_ads(device_id, ad_schedule):
    ads = load_ads()
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

        wait_sec = (monitor_time - datetime.now()).total_seconds()
        if wait_sec > 0:
            time.sleep(wait_sec)

        clear_logcat()
        proc = start_logcat()

        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{channel}.mp4"
        recorder_proc = start_recording(filename)

        found_receive_cue = False
        impression_logs = []
        cue_check_start_time = time.time()

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors='ignore').strip()

            if re.search(r'receive\s*cue...', decoded, re.IGNORECASE):
                print(f"[감지] receive cue → {decoded}")
                found_receive_cue = True

            if re.search(r'   ImpressionLog\(', decoded):
                print(f"[감지] ImpressionLog → {decoded}")
                impression_logs.append(decoded)

            if 'success to send impression-logs' in decoded:
                print("[종료] 광고 감시 완료 로그 감지")
                break

            if not found_receive_cue and (time.time() - cue_check_start_time) > 90:
                send_slack_message(f"[스킵] {channel_name}({channel}): 90초 내 receive cue 없음. 다음으로 이동.")
                break

        proc.terminate()
        stop_recording(recorder_proc, filename)

        if not found_receive_cue or not impression_logs:
            continue

        for i, log in enumerate(impression_logs):
            parsed = parse_impression_log(log)
            if parsed:
                print(f"\n[검증 시작] {i+1}번째 ImpressionLog")
                compare_impression_to_ads(parsed, ads)

        log_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = os.path.join(LOG_DIR, f"ImpressionLog_{log_time}.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(impression_logs))

        send_slack_file(log_path, "log")
        send_slack_message(f"[성공] {channel_name}({channel}) 광고 감시 및 검증 완료")
        return  # 한 개만 수행하고 종료

if __name__ == "__main__":
    device_id = send_post_request()
    schedule = load_ad_schedule()
    if not schedule:
        send_slack_message("[경고] 광고 스케줄 없음")
        exit()
    monitor_ads(device_id, schedule)
