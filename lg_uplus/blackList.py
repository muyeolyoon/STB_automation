import os
import time
import threading
from datetime import datetime, timedelta
import sys

# 현재 파일 기준으로 상위 폴더(stb-rpa) 경로를 sys.path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs

SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def get_blacklist_channels():
    blacklist_input = input("⛔ 감시 제외할 채널명을 쉼표로 입력하세요 (예: 채널A,채널B): ").strip()
    blacklist = [ch.strip() for ch in blacklist_input.split(",") if ch.strip()]
    print(f"\n[블랙리스트] 제외할 채널 목록: {blacklist if blacklist else '없음'}\n")
    return blacklist


def filter_data_by_blacklist(data, blacklist):
    if not blacklist:
        return data  # 블랙리스트가 없으면 전체 감시
    return [row for row in data if row.get("채널명", "").strip() not in blacklist]


keyevent_map = {str(i): 7 + i for i in range(10)}  # 숫자 → ADB keyevent 매핑


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


def monitor_and_switch_channels_with_data(data, devices):
    already_switched = set()

    while True:
        try:
            now = datetime.now()
            ads_remaining_today = []

            for row in data:
                try:
                    ad_time = datetime.strptime(row["광고편성 시간"], "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second)
                    if now < ad_time_today:
                        ads_remaining_today.append(ad_time_today)
                except:
                    continue

            if not ads_remaining_today:
                print(f"{current_time_str()} ✅ 오늘 광고 편성이 모두 종료되었습니다. 프로그램을 종료합니다.")
                break

            for row in data:
                channel_name = row["채널명"]
                channel_number = row["채널번호"]
                ad_time_str = row["광고편성 시간"]

                if channel_name in already_switched:
                    continue  # 이미 전환한 채널은 건너뜀

                try:
                    ad_time = datetime.strptime(ad_time_str, "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0)
                    switch_time = ad_time_today - timedelta(seconds=30)

                    if switch_time <= now < (ad_time_today - timedelta(seconds=15)):
                        print(f"{current_time_str()} 📺 광고 예정 채널: {channel_name} ({channel_number})")
                        switch_channel_via_adb(channel_number, devices)
                        already_switched.add(channel_name)
                        print(f"{current_time_str()} ⏳ 광고 대기 및 재생")
                        time.sleep(160)
                        break

                except Exception as e:
                    print(f"{current_time_str()} ❌ 오류 발생 ({channel_name}): {e}")

        except Exception as err:
            print(f"{current_time_str()} 💥 반복 중 오류 발생: {err}")
            time.sleep(30)


def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결된 디바이스가 없습니다.")
        return

    log_dir = "D:/python_test/anypointmedia-QA/test_log"
    filters = ["AnypointAD", "ANYPOINT_SDK"]
    os.makedirs(log_dir, exist_ok=True)
    save_multiple_devices_logs(device_ips, log_dir, filters=filters)

    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY)

    # 🔹 블랙리스트 입력 및 필터링
    blacklist = get_blacklist_channels()
    filtered_data = filter_data_by_blacklist(data, blacklist)

    monitor_and_switch_channels_with_data(filtered_data, device_ips)


if __name__ == "__main__":
    main()
