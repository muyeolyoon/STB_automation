import time
import subprocess
from datetime import datetime
import threading
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def switch_channel_up_down_synchronized(devices, up_duration=20, down_duration=20, run_duration=120, wait_duration=180):
    def sync_channel_loop():
        while True:
            print(f"[{current_time_str()}] ▶ 채널 업/다운 시작 (총 {run_duration}초)...")
            print(f"[{current_time_str()}] ⏳ {wait_duration}초 대기 시작...")
            time.sleep(wait_duration)  # 3분 대기

            start_time = time.time()  # ⬅️ 여기로 이동

            toggle = True
            while time.time() - start_time < run_duration:  # 2분 동안 반복
                keycode = 167 if toggle else 166
                label = '다운' if toggle else '업'

                for device in devices:
                    try:
                        subprocess.run(["adb", "-s", device, "shell", "input", "keyevent", str(keycode)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        print(f"[{current_time_str()}] ▶ 디바이스 {device} - 채널 {label}")
                    except Exception as e:
                        print(f"❌ 디바이스 {device} 명령 실패: {e}")

                toggle = not toggle
                time.sleep(up_duration if toggle else down_duration)

    thread = threading.Thread(target=sync_channel_loop, daemon=True)
    thread.start()



# ✅ 메인 함수
def main():
    from component.device_connect_multiple import connect_multiple_devices

    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결할 디바이스가 없습니다.")
        return

    # 3분 대기 → 2분 업/다운 반복 → 다시 대기
    switch_channel_up_down_synchronized(
        device_ips,
        up_duration=15,
        down_duration=5,
        run_duration=120,
        wait_duration=180
    )

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()