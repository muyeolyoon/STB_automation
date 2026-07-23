import subprocess
import time
import os
from datetime import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import threading


# 설정
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")CHANNEL_ID = "C08KBRUUVSS"  
client = WebClient(token=SLACK_BOT_TOKEN)

adb_path = "adb"
device_ip = "192.168.10.8:5555"
scheduled_time = "06:40:00"

timestamp = datetime.now().strftime("%y%m%d%H%M")
recording_file = f"{timestamp}.mp4"
log_file = f"{timestamp}.txt"
filtered_log_file = f"AddrAD_{timestamp}.log"

channel_keycodes = [14, 9, 66]

log_proc = None

# 1. AddrAD 로그 수집 시작
def start_log_capture():
    global log_proc
    print("AddrAD 로그 수집 시작")

    def capture_and_filter():
        with open(filtered_log_file, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                [adb_path, "-s", device_ip, "logcat"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            global log_proc
            log_proc = proc
            for line in proc.stdout:
                if "AddrAD" in line:
                    f.write(line)
                    f.flush()  # 즉시 디스크에 쓰기

    # 백그라운드 스레드로 로그 수집 실행
    thread = threading.Thread(target=capture_and_filter, daemon=True)
    thread.start()

#  로그 수집 종료 및 저장
def stop_log_capture():
    global log_proc
    print("AddrAD 로그 수집 종료")
    if log_proc:
        log_proc.terminate()
        log_proc.wait()
        time.sleep(2)  # 파일 버퍼 비우는 시간 살짝 주기

        # 로그 유효성 검사: 파일이 존재하고 10바이트 이상이면 Slack 전송
        if os.path.exists(filtered_log_file) and os.path.getsize(filtered_log_file) > 10:
            send_slack_file(filtered_log_file, "AddrAD 필터 로그")
        else:
            print("유효한 AddrAD 로그가 없어 Slack 전송 생략")
    else:
        print("로그 수집 프로세스가 존재하지 않음")

def send_key(keycode):
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "keyevent", str(keycode)])

def start_screen_recording():
    print("녹화 시작")
    subprocess.Popen([adb_path, "-s", device_ip, "shell", "screenrecord", f"/sdcard/{recording_file}"])

def stop_screen_recording():
    print("녹화 종료 및 파일 전송")
    subprocess.run([adb_path, "-s", device_ip, "shell", "pkill", "-l", "2", "screenrecord"])
    time.sleep(2)
    subprocess.run([adb_path, "-s", device_ip, "pull", f"/sdcard/{recording_file}", f"./{recording_file}"])

def send_slack_file(file_path, title):
    if not os.path.exists(file_path):
        print(f"파일 없음: {file_path}")
        return
    try:
        with open(file_path, "rb") as f:
            client.files_upload_v2(
                channel=CHANNEL_ID,
                file=f,
                filename=os.path.basename(file_path),
                title=title
            )
        print(f"Slack 업로드 완료: {file_path}")
    except SlackApiError as e:
        print(f"Slack 업로드 실패: {e.response['error']}")

def log_to_file(log_lines):
    with open(log_file, "a", encoding="utf-8") as f:
        for line in log_lines:
            f.write(line + "\n")

def change_channel():
    print("채널 변경 중...")
    for key in channel_keycodes:
        send_key(key)
        time.sleep(0.5)
    print("채널 변경 완료")

def monitor_logs():
    print("로그 감시 시작")
    subprocess.run([adb_path, "-s", device_ip, "logcat", "-c"])
    process = subprocess.Popen(
        [adb_path, "-s", device_ip, "logcat"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    cue_detected = playlist_detected = impression_detected = recording = False
    log_buffer = []
    start_time = time.time()

    try:
        for line in process.stdout:
            line = line.strip()
            log_buffer.append(line)
            lower_line = line.lower()

            if not cue_detected and "receive cue" in lower_line:
                cue_detected = True
                print(f"Cue 감지: {line}")
                if not recording:
                    start_screen_recording()
                    recording = True

            elif cue_detected and not playlist_detected and "addplaylistitem" in lower_line:
                playlist_detected = True
                print(f"Playlist 감지: {line}")
                send_key(166)

            elif playlist_detected and "impressionlog(deviceid=" in lower_line:
                impression_detected = True
                print(f"Impression 감지: {line}")
                log_buffer.append("FAIL - Impression 노출됨")
                break

            elif playlist_detected and "success to send impression-logs" in lower_line:
                log_buffer.append("FAIL - Impression 전송 완료 로그 감지")
                break

            # 3분 경과 시 PASS 처리
            if playlist_detected and not impression_detected:
                if time.time() - start_time > 180:
                    print("Impression 감지 → PASS 처리 (3분 경과)")
                    log_buffer.append("PASS - 3분 동안 Impression 미발생")
                    try:
                        client.chat_postMessage(
                            channel=CHANNEL_ID,
                            text="*PASS 처리되었습니다.* 3분간 Impression 로그가 감지되지 않았습니다."
                        )
                    except SlackApiError as e:
                        print(f"Slack 메시지 전송 실패: {e.response['error']}")
                    break

    except KeyboardInterrupt:
        print("사용자 중단됨.")
    finally:
        if recording:
            stop_screen_recording()

        log_to_file(log_buffer)
        send_slack_file(log_file, "광고 로그 텍스트")
        send_slack_file(recording_file, "광고 녹화 영상")
        process.terminate()

def wait_for_schedule(target_time):
    print(f"[{target_time}] 까지 대기 중...")
    while datetime.now().strftime("%H:%M:%S") < target_time:
        time.sleep(1)
    print("실행 시각 도달")

# 11. 전체 흐름
def main():
    start_log_capture()
    wait_for_schedule(scheduled_time)
    change_channel()
    monitor_logs()

    print("모든 동작 완료. 로그 수집 종료 및 저장")
    time.sleep(10)
    stop_log_capture()

if __name__ == "__main__":
    main()
