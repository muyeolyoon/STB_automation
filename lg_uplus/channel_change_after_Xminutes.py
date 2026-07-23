import os
import time
import threading
import subprocess
from datetime import datetime, timedelta
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.device_connect_multiple import connect_multiple_devices


keyevent_map = {str(i): 7 + i for i in range(10)}  # 숫자 -> ADB keyevent 매핑


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def switch_channel_via_adb(channel_number, devices):
    def input_channel(device):
        for digit in str(channel_number):
            if digit in keyevent_map:
                os.system(f"adb -s {device} shell input keyevent {keyevent_map[digit]}")
                time.sleep(0.2)

    threads = []
    for device in devices:
        t = threading.Thread(target=input_channel, args=(device,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


def wait_and_switch_channel(devices, channel_number, minutes_to_wait):
    switch_time = datetime.now() + timedelta(minutes=minutes_to_wait)
    print(f"⏳ {minutes_to_wait}분 후인 {switch_time.strftime('%Y-%m-%d %H:%M:%S')}에 채널을 {channel_number}번으로 변경합니다.")

    seconds_to_wait = (switch_time - datetime.now()).total_seconds()
    try:
        time.sleep(seconds_to_wait)
    except KeyboardInterrupt:
        print("⛔ 사용자에 의해 중단됨.")
        return

    print(f"{current_time_str()} ▶ 채널 변경 중...")
    switch_channel_via_adb(channel_number, devices)
    time.sleep(0.2)
    print(f"{current_time_str()} ✅ 채널 변경 완료.")

def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결된 디바이스가 없습니다.")
        return
    try:
        
        minutes_to_wait = float(input("몇 분 후 채널을 변경할까요? (예: 30): "))
        channel_number = input("변경할 채널 번호를 입력하세요: ")

        wait_and_switch_channel(device_ips, channel_number, minutes_to_wait)

    except ValueError:
        print("⚠️ 잘못된 입력입니다. 숫자를 입력해야 합니다.")

if __name__ == "__main__":
    main()
