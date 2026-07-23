import os
import time
import threading
from datetime import datetime, timedelta
import sys
import re

# 현재 파일 기준으로 상위 폴더(stb-rpa) 경로를 sys.path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs, print_impression_log_counts
from component.ad_sync_recovery import on_log_line_ad_not_ready_recovery
from component.channel_mapping import get_channel_number  # 채널명 → 번호 매핑

SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


keyevent_map = {str(i): 7 + i for i in range(10)}  # 숫자 -> ADB keyevent 매핑


def switch_channel_via_adb(channel_number, devices):
    def input_channel(device, channel_str):
        for digit in channel_str:
            if digit in keyevent_map:
                os.system(f"adb -s {device} shell input keyevent {keyevent_map[digit]}")
                time.sleep(0.2)
        if len(channel_str) <= 2:
            time.sleep(0.2)
        os.system(f"adb -s {device} shell input keyevent 23")

    threads = []
    channel_str = str(channel_number)

    for device in devices:
        t = threading.Thread(target=input_channel, args=(device, channel_str))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


def monitor_and_switch_channels_with_data(data, devices):
    while data:
        try:
            now = datetime.now()
            next_index = None
            next_ad_time = None

            for i in reversed(range(len(data))):
                row = data[i]
                try:
                    if "광고편성 시간" not in row or "채널명" not in row:
                        data.pop(i)
                        continue

                    # 채널명 → 번호 변환
                    channel_number = get_channel_number(row["채널명"])
                    if not channel_number:
                        print(f"{current_time_str()} 채널번호 없음: {row['채널명']}")
                        data.pop(i)
                        continue
                    row["채널번호"] = channel_number  # 변환된 번호 저장

                    ad_time = datetime.strptime(row["광고편성 시간"], "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second)

                    if now > ad_time_today + timedelta(seconds=180):
                        print(f"{current_time_str()} 지난 광고 삭제: {row['채널명']} {row['광고편성 시간']}")
                        data.pop(i)
                        continue

                    if ad_time_today > now:
                        if next_ad_time is None or ad_time_today < next_ad_time:
                            next_ad_time = ad_time_today
                            next_index = i
                except Exception as e:
                    print(f"{current_time_str()} 파싱 오류: {e}")
                    data.pop(i)
                    continue

            if next_index is None:
                print(f"{current_time_str()} 광고 스케줄 모두 완료. 프로그램을 종료합니다.")
                break

            row = data[next_index]
            channel_name = row["채널명"]
            channel_number = row["채널번호"]
            ad_time_str = row["광고편성 시간"]

            switch_time = next_ad_time - timedelta(seconds=30)

            if switch_time <= now < (next_ad_time - timedelta(seconds=15)):
                print(f"{current_time_str()} 광고 예정 채널: {channel_name} ({channel_number})")
                switch_channel_via_adb(channel_number, devices)
                print(f"{current_time_str()} 광고 대기 및 재생")

                time.sleep(160)

                print(f"{current_time_str()} 실행된 row 제거: {channel_name} {ad_time_str}")
                data.pop(next_index)
            else:
                time.sleep(1)

        except Exception as err:
            print(f"{current_time_str()} 반복 중 오류 발생: {err}")
            time.sleep(30)


def count_impression_logs(log_dir, filenames_dict):
    counts = {}
    total_count = 0

    for device, filename in filenames_dict.items():
        log_path = os.path.join(log_dir, filename)
        counts[device] = {"count": 0, "last_time": None}

        if not os.path.exists(log_path):
            continue

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "impression log size" in line:
                        counts[device]["count"] += 1
                        total_count += 1

                        m = re.match(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
                        if m:
                            log_time_str = m.group(1)
                            try:
                                log_time = datetime.strptime(
                                    f"{datetime.now().year}-{log_time_str}",
                                    "%Y-%m-%d %H:%M:%S.%f"
                                )
                                counts[device]["last_time"] = log_time
                            except:
                                pass
        except Exception as e:
            print(f"로그 파일 읽기 오류: {log_path}, {e}")

    return counts, total_count


def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("연결된 디바이스가 없습니다.")
        return

    # 디바이스별 마지막 채널 입력
    final_channels = {}
    for device_id in device_ips:
        while True:
            ch = input(f"디바이스 {device_id}의 마지막 채널 번호를 입력하세요: ")
            if ch.isdigit():
                final_channels[device_id] = ch.strip()
                break
            print("숫자만 입력해주세요.")

    log_dir = "D:/python_test/anypointmedia-QA/test_log"
    filters = ["AnypointAD", "ANYPOINT_SDK"]
    os.makedirs(log_dir, exist_ok=True)

    threads, log_files = save_multiple_devices_logs(
        device_ips, log_dir, filters=filters, on_log_line=on_log_line_ad_not_ready_recovery
    )

    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY)

    monitor_and_switch_channels_with_data(data, device_ips)

    # 마지막 채널로 복귀 (모니터링 중과 동일: input text는 포커스 없으면 무시되는 경우가 많음)
    time.sleep(2)
    for device_id, channel in final_channels.items():
        print(f"\n디바이스 {device_id} → 마지막 채널 {channel} 으로 전환 중...")
        switch_channel_via_adb(channel, [device_id])

    # 로그 카운트
    counts, total_count = count_impression_logs(log_dir, log_files)
    print_impression_log_counts(counts)
    print(f"총 합계: {total_count}회")


if __name__ == "__main__":
    main()
