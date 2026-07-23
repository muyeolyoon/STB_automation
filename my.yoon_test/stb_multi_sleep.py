import time
import subprocess
from datetime import datetime
import threading
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# ✅ Power 버튼(26) 누르고 대기
def press_power_and_wait_synchronized(devices, wait_duration=20, repeat_interval=300):
    def power_loop():
        while True:
            print(f"[{current_time_str()}] ▶ Power 버튼(keyevent 26) 전송 중...")
            for device in devices:
                try:
                    subprocess.run(["adb", "-s", device, "shell", "input", "keyevent", "26"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    print(f"[{current_time_str()}] ▶ 디바이스 {device} - Power 버튼 전송 완료")
                except Exception as e:
                    print(f"❌ 디바이스 {device} 명령 실패: {e}")

            print(f"[{current_time_str()}] ⏳ {wait_duration}초 대기 중...")
            time.sleep(wait_duration)
            print(f"[{current_time_str()}] ⏳ sleep mode off 및 대기\n")
            subprocess.run(["adb", "-s", device, "shell", "input", "keyevent", "26"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(300)

    thread = threading.Thread(target=power_loop, daemon=True)
    thread.start()


# ✅ 메인 함수
def main():
    from component.device_connect_multiple import connect_multiple_devices

    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결할 디바이스가 없습니다.")
        return

    # 전원 버튼 누르고 20초 대기 → 300초마다 반복
    press_power_and_wait_synchronized(
        device_ips,
        wait_duration=20,
        repeat_interval=300
    )

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
