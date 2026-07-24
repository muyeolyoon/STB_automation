import subprocess
import time
import os
from datetime import datetime
import threading


# 설정

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

        # 로그 유효성 검사: 파일이 존재하고 10바이트 이상이면 알림
        if os.path.exists(filtered_log_file) and os.path.getsize(filtered_log_file) > 10:
            notify_file(filtered_log_file, "AddrAD 필터 로그")
        else:
            print("유효한 AddrAD 로그가 없어 알림 생략")
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

def notify_file(file_path, title=None):
    print(f"[notify skipped] file={file_path} title={title}")



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

    cue_detected = playlist_detected = recording = False
    required_keywords = [
        "ImpressionLog",
        "impressionTime",
        "POST https://art-stats-api.anypoint.tv/v3/device/impression-logs",
        "200 https://art-stats-api.anypoint.tv/v3/device/impression-logs",
        "impression log size"
    ]
    detected_keywords = set()
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
                time.sleep(50)
                send_key(166)
                start_time = time.time()  # 키워드 감지 타이머 시작

            elif playlist_detected:
                for keyword in required_keywords:
                    if keyword.lower() in lower_line and keyword not in detected_keywords:
                        print(f"필수 키워드 감지됨: {keyword}")
                        detected_keywords.add(keyword)
                        log_buffer.append(f"감지됨 - {keyword}: {line}")

                if len(detected_keywords) == len(required_keywords):
                    log_buffer.append("pass - ImpressionLog / impressionTime / POST / 200 / impression log size 감지 완료")
                    break

                elif time.time() - start_time > 360:
                    log_buffer.append("fail - 일부 키워드 감지 실패")
                    break

    except KeyboardInterrupt:
        print("사용자 중단됨.")
    finally:
        if recording:
            stop_screen_recording()

        log_to_file(log_buffer)
        notify_file(log_file, "광고 로그 텍스트")
        notify_file(recording_file, "광고 녹화 영상")

        try:
            result_msg = "*PASS* - ImpressionLog / impressionTime / POST / 200 / impression log size 감지 완료" if len(detected_keywords) == len(required_keywords) else "*FAIL* - 감지 실패된 키워드 있음 ❌"
            client.chat_postMessage(
                channel=CHANNEL_ID,
                text=result_msg
            )

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
