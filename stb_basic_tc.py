import subprocess
import time
import os
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Slack API 설정
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL_ID = "C08KBRUUVSS"  # Slack 채널 ID

client = WebClient(token=SLACK_BOT_TOKEN)

adb_path = r"adb"
device_ip = "192.168.10.8:5555"
search_text = "test"
recording_file = datetime.now().strftime("%y%m%d%H%M") + ".mp4"
log_file = "scenario_log.txt"


def send_key(keycode):
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "keyevent", str(keycode)])


def tap(x, y):
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "tap", str(x), str(y)])


def start_screen_recording():
    print("▶ 녹화 시작")
    subprocess.Popen([
        adb_path, "-s", device_ip, "shell", "screenrecord", f"/sdcard/{recording_file}"
    ])


def stop_screen_recording():
    print("⏹ 녹화 종료 및 파일 전송")
    subprocess.run([adb_path, "-s", device_ip, "shell", "pkill", "-l", "2", "screenrecord"])
    time.sleep(2)
    subprocess.run([adb_path, "-s", device_ip, "pull", f"/sdcard/{recording_file}", f"./{recording_file}"])


def send_slack_files(*file_paths):
    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    response = client.files_upload_v2(
                        channel=CHANNEL_ID,
                        file=f,
                        filename=os.path.basename(file_path),
                        title=os.path.basename(file_path)
                    )
                print(f"✅ Slack 전송 성공: {file_path}")
            except SlackApiError as e:
                print(f"❌ Slack 전송 실패: {e.response['error']}")
        else:
            print(f"📂 존재하지 않아 전송 제외: {file_path}")
            
def log_event(event):
    with open(log_file, "a") as f:
        f.write(f"{datetime.now()} - {event}\n")
    print(f"로그 기록: {event}")

def run_scenario():
        # 녹화 시작
    start_screen_recording()
    print("1. 음량 up/down 버튼 작동")
    log_event("음량 up/down 버튼 작동")
    send_key(24)  # Volume Up
    time.sleep(1)
    send_key(25)  # Volume Down
    time.sleep(1)

    print("2. 음소거 버튼 작동")
    log_event("음소거 버튼 작동")
    send_key(164)
    time.sleep(1)

    print("3. 옵션 > 전체채널 편성표")
    log_event("옵션 > 전체채널 편성표")
    send_key(82)
    time.sleep(1)
    send_key(20)
    time.sleep(.2)
    send_key(20)
    time.sleep(.2)
    send_key(20)
    time.sleep(.2)
    send_key(23)
    time.sleep(.2)
    send_key(4)
    time.sleep(2)

    print("4. OK 버튼 작동")
    log_event("OK 버튼 작동")
    send_key(23)
    time.sleep(0.5)


    # print("5. 옵션 > 4(2)채널 동시보기")
    # log_event("옵션 > 4(2)채널 동시보기")
    # send_key(82)
    # time.sleep(1)
    # time.sleep(.2)
    # send_key(20)
    # time.sleep(.2)
    # send_key(20)
    # time.sleep(.2)
    # send_key(23)
    # time.sleep(.2)
    # send_key(4)
    # time.sleep(1)

    print("7. 홈 버튼 작동")
    log_event("홈 버튼 작동")
    send_key(3)
    time.sleep(1)

    print("8. 채널 업/다운 이동")
    log_event("채널 업/다운 이동")
    send_key(166)  # 채널 업
    time.sleep(0.3)
    send_key(167)  # 채널 다운
    time.sleep(0.3)

    print("9. 슬립 on/off")
    log_event("슬립 on/off")
    send_key(223)  # 슬립
    time.sleep(1)
    send_key(224)  # 웨이크업
    time.sleep(1)

    # 아이들 나라 진입 플로우
    # log_event("아이들 나라 진입 플로우 시작")
    # send_key(3)
    # time.sleep(7)
    # send_key(19)
    # time.sleep(.5)
    # send_key(22)
    # time.sleep(.5)
    # send_key(23)
    # time.sleep(10)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(.5)
    # send_key(4)
    # time.sleep(5)
    # send_key(84)
    # time.sleep(2)
    # send_key(4)
    # time.sleep(2)
    # send_key(84)
    # time.sleep(1)

    # 검색 텍스트 입력
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "text", search_text])
    time.sleep(0.5)
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "keyevent", "66"])
    time.sleep(2)

    # 종료 작업
    send_key(4)
    send_key(4)

    # 녹화 종료
    stop_screen_recording()

    # Slack으로 파일 전송
    send_slack_files(log_file, recording_file)


run_scenario()
