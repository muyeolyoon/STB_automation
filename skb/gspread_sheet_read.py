import os
import time
import threading
from datetime import datetime, timedelta
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.obs_recorder import OBSRecorder
from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs

SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"

keyevent_map = {str(i): 7 + i for i in range(10)}

def normalize_channel_number(channel_number):
    if channel_number is None:
        return ""
    s = str(channel_number).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s

def sheet_tab_name():
    return datetime.now().strftime("%y%m%d") + " skb모니터링"

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def switch_channel_via_adb(channel_number, devices):
    channel_str = normalize_channel_number(channel_number)
    print(f"{current_time_str()} 채널 입력: {channel_str}")

    def input_channel(device):
        for i, digit in enumerate(channel_str):
            if digit in keyevent_map:
                os.system(f"adb -s {device} shell input keyevent {keyevent_map[digit]}")
                delay = 0.5 if len(channel_str) >= 3 else 0.35
                time.sleep(delay)
                if len(channel_str) >= 3 and i == 1:
                    time.sleep(0.15)

    threads = []
    for device in devices:
        t = threading.Thread(target=input_channel, args=(device,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

def monitor_and_switch_channels_with_data(data, devices, recorder):
    log_dir = "D:/python_test/anypointmedia-QA/test_log"
    filters = ["AnypointAD", "ANYPOINT_SDK"]
    os.makedirs(log_dir, exist_ok=True)
    save_multiple_devices_logs(devices, log_dir, filters=filters)

    while True:
        try:
            now = datetime.now()
            ads_remaining_today = []

            for i, row in enumerate(data):
                try:
                    ad_time_str = row["광고편성 시간"]
                    channel_number = normalize_channel_number(row["채널번호"])

                    ad_time = datetime.strptime(ad_time_str, "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second)

                    if timedelta(seconds=0) < (ad_time_today - now) < timedelta(hours=1):
                        ads_remaining_today.append((i, row, ad_time_today))
                except:
                    continue

            if not ads_remaining_today:
                print(f"{current_time_str()} ✅ 광고 편성이 모두 끝났습니다. 프로그램을 종료합니다.")
                break

            for i, row, ad_time_today in ads_remaining_today:
                channel_name = row["채널명"]
                channel_number = normalize_channel_number(row["채널번호"])
                switch_time = ad_time_today - timedelta(seconds=30)

                if switch_time <= now < (ad_time_today - timedelta(seconds=15)):
                    print(f"{current_time_str()} 📺 광고 예정 채널: {channel_name} ({channel_number})")
                    switch_channel_via_adb(channel_number, devices)
                    print(f"{current_time_str()} ⏳ 광고 대기 및 재생")

                    recorder.start_recording()
                    print(f"{current_time_str()} 🎥 녹화 시작 (최대 180초)")

                    time.sleep(180)

                    recorder.stop_recording()
                    print(f"{current_time_str()} ⏹️ 녹화 종료 (타이머 기준)")

                    del data[i]
                    break

        except Exception as err:
            print(f"{current_time_str()} 💥 반복 중 오류 발생: {err}")
            time.sleep(30)

def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결된 디바이스가 없습니다.")
        return

    recorder = OBSRecorder(password="123456")
    recorder.connect()

    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY, sheet_name=sheet_tab_name())
    monitor_and_switch_channels_with_data(data, device_ips, recorder)

if __name__ == "__main__":
    main()
