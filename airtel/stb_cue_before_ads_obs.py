import os 
import sys
import time
import subprocess
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
# 상위 경로 import
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from component.setting import AirtelSetting, Slack
from component.obs_recorder import OBSRecorder


client = WebClient(token=Slack.SLACK_BOT_TOKEN)

class SlackReporter:
    @staticmethod
    def send_file(file_path, title):
        if not os.path.exists(file_path):
            print(f"파일 없음: {file_path}")
            return
        try:
            with open(file_path, "rb") as f:
                client.files_upload_v2(
                    channel=Slack.CHANNEL_ID,
                    file=f,
                    filename=os.path.basename(file_path),
                    title=title
                )
            print(f"Slack 업로드 완료: {file_path}")
        except SlackApiError as e:
            print(f"Slack 업로드 실패: {e.response['error']}")

# 슬랙 설정
slack_client = WebClient(token=Slack.SLACK_BOT_TOKEN)

# 슬랙 메시지 전송
def send_slack_message(message):
    try:
        slack_client.chat_postMessage(channel=Slack.CHANNEL_ID, text=message)
    except SlackApiError as e:
        print(f"Slack 메시지 실패: {e.response['error']}")

# 슬랙 파일 전송
def send_slack_file(file_path, title):
    try:
        slack_client.files_upload_v2(
            channel=Slack.CHANNEL_ID,
            file=file_path,
            title=title
        )
    except SlackApiError as e:
        print(f"Slack 파일 업로드 실패: {e.response['error']}")

# POST 요청
def send_post_request():
    url = "http://uplus-device-app-prod2.ap-northeast-2.elasticbeanstalk.com/v3/devices/commands"
    headers = {"Content-Type": "application/json"}
    body = {
        "serverMessageType": "UpdateAssetCommand",
        "deviceIds": [47051029]
    }
    try:
        response = requests.post(url, headers=headers, json=body)
        response.raise_for_status()
        return body["deviceIds"][0]
    except requests.exceptions.RequestException as e:
        send_slack_message(f"[오류] POST 요청 실패: {e}")
        exit()

# 디렉토리 생성
LOG_DIR = "log"
RECORD_DIR = "recordings"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RECORD_DIR, exist_ok=True)

# 채널 전환
def switch_channel(channel_number):
    keyevent_map = {str(i): 7 + i for i in range(10)}
    for digit in str(channel_number):
        if digit in keyevent_map:
            subprocess.run(["adb", "-s", AirtelSetting.device_ip, "shell", "input", "keyevent", str(keyevent_map[digit])])
            time.sleep(0.5)

# 로그 초기화 및 수집
def clear_logcat():
    subprocess.run(["adb", "-s", AirtelSetting.device_ip, "logcat", "-c"])

def start_logcat():
    return subprocess.Popen(["adb", "-s", AirtelSetting.device_ip, "logcat", "-v", "time"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


# 스프레드시트 로드
def sheet_tab_name():
    return datetime.now().strftime("%y%m%d") + " skb모니터링"

def load_ad_schedule():
    try:
        from component.schedule_loader import load_ad_schedule as _load_schedule_rows
        rows = _load_schedule_rows(AirtelSetting.SERVICE_ACCOUNT_PATH, section="skb")
        ad_schedule = []
        for row in rows:
            ad_dt = row["ad_time"]
            if ad_dt < datetime.now():
                ad_dt += timedelta(days=1)
            ad_schedule.append(
                {
                    "channel_name": row["channel_name"],
                    "channel": str(row["channel"]),
                    "ad_time": ad_dt,
                }
            )
        return sorted(ad_schedule, key=lambda x: x["ad_time"])
    except Exception as e:
        send_slack_message(f"[오류] 광고 스케줄 로드 실패: {e}")
        exit()

# 로그 파싱
def parse_impression_log_section(lines):
    return [line for line in lines if 'ImpressionLog(' in line]

# 감시 루틴
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

        all_lines = []
        playlist_triggered = False
        cue_check_start_time = None
        cue_timeout_start = time.time()  # <- 추가됨

        while True:
            # 2분 타임아웃 검사
            if not playlist_triggered and (time.time() - cue_timeout_start > 120):
                print(f"{channel_name}({channel}) 채널 - 2분 동안 receive cue 감지되지 않음")
                break

            line = proc.stdout.readline()
            if not line:
                break
            decoded_line = line.decode('utf-8', errors='ignore').strip()
            all_lines.append(decoded_line)

            if r'appendAdPlaylist' in decoded_line and not playlist_triggered:
                playlist_triggered = True
                print(f"[정보] appendAdPlaylist 감지됨. 채널 업 동작 실행.")
                subprocess.run(["adb", "-s", AirtelSetting.device_ip, "shell", "input", "keyevent", "166"])
                cue_check_start_time = time.time()

            if playlist_triggered and (time.time() - cue_check_start_time) > 60:
                break

        proc.terminate()

        if not playlist_triggered:
            continue  # PASS 처리로 이미 슬랙 메시지 전송됨

        impression_logs = parse_impression_log_section(all_lines)
        if impression_logs:
            send_slack_message(f"[FAIL] {channel_name}({channel}) 채널 - 광고 재생전 채널 변경 결과 : FAIL")
        else:
            send_slack_message(f"[PASS] {channel_name}({channel}) 채널 - 광고 재생전 채널 변경 PASS")
        return
# 메인 실행
if __name__ == "__main__":
    date_str = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    video_filename = f"{date_str}.mkv"
    recorder = OBSRecorder(password="123456")
    recorder.connect()
    print("obs 연결")

    recorder.start_recording()
    device_id = send_post_request()
    schedule = load_ad_schedule()
    if not schedule:
        send_slack_message("[경고] 광고 스케줄 없음")
        exit()
    monitor_ads(device_id, schedule)
    recorder.stop_recording()  # 녹화 종료
    obs_video_path = os.path.join("G:/공유 드라이브/02.기술본부/30. QA/11. 셋탑 QA/U+/test", video_filename)
    SlackReporter.send_file(obs_video_path, f"OBS 녹화 영상: {video_filename}")