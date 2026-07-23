import os, sys
import subprocess
import time
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# 내부 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from component.setting import AirtelSetting, Slack

client = WebClient(token=Slack.SLACK_BOT_TOKEN)

action_sequence = ["default", "channel_updown", "sleep"]

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def date_check():
    return datetime.now().strftime("%Y%m%d")

def send_slack_message(message):
    try:
        client.chat_postMessage(channel=Slack.CHANNEL_ID, text=message)
    except SlackApiError as e:
        print(f"Slack 메시지 실패: {e.response['error']}")

class Recorder:
    @staticmethod
    def start(device_ip, output_path):
        print(f"[{current_time_str()}] 녹화 시작: {output_path}")
        subprocess.Popen(
            [AirtelSetting.adb_path, "-s", device_ip, "shell", "screenrecord", f"/sdcard/{output_path}"],
            stdout=subprocess.DEVNULL
        )

    @staticmethod
    def stop(device_ip, output_path):
        print(f"[{current_time_str()}] 녹화 중지 및 pull 시작: {output_path}")
        subprocess.run([AirtelSetting.adb_path, "-s", device_ip, "shell", "pkill", "-l", "2", "screenrecord"])
        time.sleep(2)
        subprocess.run([AirtelSetting.adb_path, "-s", device_ip, "pull", f"/sdcard/{output_path}", output_path])

class SlackReporter:
    @staticmethod
    def send_file(file_path, title):
        if not os.path.exists(file_path):
            print(f"[SlackReporter] 파일 없음: {file_path}")
            return
        try:
            with open(file_path, "rb") as f:
                client.files_upload_v2(
                    channel=Slack.CHANNEL_ID,
                    file=f,
                    filename=os.path.basename(file_path),
                    title=title
                )
            print(f"[SlackReporter] Slack 업로드 완료: {file_path}")
        except SlackApiError as e:
            print(f"[SlackReporter] Slack 업로드 실패: {e.response['error']}")

class PostAdAction:
    @staticmethod
    def execute(device_ip, action_type="default"):
        print(f"[{current_time_str()}] 광고 이후 동작 실행: {action_type}")
        action_map = {
            "default": [(24, "음량 up"), (25, "음량 down"), (164, "음소거"), (3, "홈"), (4, "이전"), (4, "이전")],
            "channel_updown": [(166, "채널 up"), (167, "채널 down")],
            "sleep": [(223, "슬립 ON"), (224, "슬립 OFF")]
        }
        actions = action_map.get(action_type, [])
        for key, desc in actions:
            print(f"  입력: {desc}")
            subprocess.run([AirtelSetting.adb_path, "-s", device_ip, "shell", "input", "keyevent", str(key)])
            time.sleep(3)

def monitor_ad_and_execute(action_type):
    device_ip = AirtelSetting.device_ip
    recording_file = f"{date_check()}_{action_type}_ad_detected_record.mp4"

    # 로그 초기화
    subprocess.run([AirtelSetting.adb_path, "-s", device_ip, "logcat", "-c"])
    process = subprocess.Popen(
        [AirtelSetting.adb_path, "-s", device_ip, "logcat"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="ignore"
    )

    print(f"[{current_time_str()}] 'AdPlayItem' 로그 감시 시작... (액션: {action_type})")
    start_time = time.time()

    try:
        for line in process.stdout:
            if "AdPlayItem" in line:
                print(f"[{current_time_str()}] AdPlayItem 감지됨 → 녹화 및 액션 시작")
                # Recorder.start(device_ip, recording_file)
                time.sleep(15)  # 광고 일부 녹화
                PostAdAction.execute(device_ip, action_type)
                # Recorder.stop(device_ip, recording_file)
                SlackReporter.send_file(recording_file, title=f"{action_type} 광고 녹화")
                send_slack_message(f"[완료] '{action_type}' 동작이 광고 감지 후 실행되었습니다.")
                return  # 한 번만 실행
            if time.time() - start_time > 300:
                print(f"[{current_time_str()}] 5분 내 감지 실패. 다음 액션으로 넘어감.")
                return
    finally:
        process.terminate()

def main():
    for action in action_sequence:
        print(f"\n=== [대기: {action}] ===")
        monitor_ad_and_execute(action)
        print(f"=== [완료: {action}] ===\n")
        time.sleep(5)

if __name__ == "__main__":
    main()
