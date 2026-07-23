import os
import time
import threading
import re
from datetime import datetime, timedelta
import sys

# 현재 파일 기준으로 상위 폴더(stb-rpa) 경로를 sys.path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs, print_impression_log_counts
from component.ad_sync_recovery import on_log_line_ad_not_ready_recovery

SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"


# 유틸 함수
def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def sheet_tab_name():
    return datetime.now().strftime("%y%m%d") + " skb모니터링"


keyevent_map = {str(i): 7 + i for i in range(10)}  # 숫자 -> ADB keyevent 매핑


def normalize_channel_number(channel_number):
    if channel_number is None:
        return ""
    s = str(channel_number).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def parse_ad_datetime(now, ad_time_str):
    """광고 편성 시각을 '가장 가까운 실제 시각' datetime으로 변환."""
    ad_time = datetime.strptime(ad_time_str.strip(), "%H:%M:%S")
    ad_dt = now.replace(
        hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0
    )
    if ad_dt <= now:
        ad_dt -= timedelta(days=1)
    # 자정 직후: 남은 23:xx 편성이 '오늘 밤'으로 잡혀 24시간 대기하는 문제 방지
    if ad_dt > now + timedelta(hours=18):
        ad_dt -= timedelta(days=1)
    return ad_dt


def switch_channel_via_adb(channel_number, devices):
    def input_channel(device, channel_str):
        for i, digit in enumerate(channel_str):
            if digit in keyevent_map:
                os.system(f"adb -s {device} shell input keyevent {keyevent_map[digit]}")
                delay = 0.5 if len(channel_str) >= 3 else 0.35
                time.sleep(delay)
                if len(channel_str) >= 3 and i == 1:
                    time.sleep(0.15)

    threads = []
    channel_str = normalize_channel_number(channel_number)
    print(f"{current_time_str()} 채널 입력: {channel_str}")

    for device in devices:
        t = threading.Thread(target=input_channel, args=(device, channel_str))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


def monitor_and_switch_channels_with_data(data, devices):
    print(f"{current_time_str()} 모니터링 시작, data 길이: {len(data)}")
    while data:
        try:
            now = datetime.now()
            next_row = None
            next_ad_time = None

            # 1) 만료·잘못된 row만 제거 (pop 중 인덱스 꼬임 방지)
            for i in reversed(range(len(data))):
                row = data[i]
                try:
                    if "광고편성 시간" not in row or "채널명" not in row or "채널번호" not in row:
                        data.pop(i)
                        continue

                    ad_dt = parse_ad_datetime(now, row["광고편성 시간"])
                    if now > ad_dt + timedelta(seconds=180):
                        print(f"{current_time_str()} 지난 광고 삭제: {row['채널명']} {row['광고편성 시간']}")
                        data.pop(i)
                except Exception as e:
                    print(f"{current_time_str()} 파싱 오류: {e}")
                    data.pop(i)

            # 2) 다음 광고 선택 (row 객체 기준 — 인덱스 stale 방지)
            for row in data:
                try:
                    ad_dt = parse_ad_datetime(now, row["광고편성 시간"])
                    if ad_dt > now and (next_ad_time is None or ad_dt < next_ad_time):
                        next_ad_time = ad_dt
                        next_row = row
                except Exception:
                    continue

            if next_row is None:
                print(f"{current_time_str()} 광고 스케줄 모두 완료, 모니터링 종료.")
                break

            channel_name = next_row["채널명"]
            channel_number = normalize_channel_number(next_row["채널번호"])
            ad_time_str = next_row["광고편성 시간"]
            switch_time = next_ad_time - timedelta(seconds=30)

            if switch_time <= now < (next_ad_time - timedelta(seconds=15)):
                print(f"{current_time_str()} 광고 예정 채널: {channel_name} ({channel_number})")
                switch_channel_via_adb(channel_number, devices)
                print(f"{current_time_str()} 광고 채널 전환 완료")
                print(f"{current_time_str()} 광고 대기 및 재생")
                time.sleep(160)
                print(f"{current_time_str()} 실행된 row 제거: {channel_name} {ad_time_str}")
                data.remove(next_row)
            else:
                time.sleep(1)

        except Exception as err:
            print(f"{current_time_str()} 반복 중 오류 발생: {err}")
            time.sleep(30)


# impression log size 카운트
def count_impression_logs(log_dir, filenames_dict):
    counts = {}
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

    return counts


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
    filters = ["AnypointAD", "ANYPOINT_SDK", "not yet ready"]
    os.makedirs(log_dir, exist_ok=True)

    # 로그 저장 시작
    threads, log_files = save_multiple_devices_logs(
        device_ips, log_dir, filters=filters, on_log_line=on_log_line_ad_not_ready_recovery
    )

    # 스프레드시트 데이터 로드
    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY, sheet_name=sheet_tab_name())

    # 광고 감시 실행
    monitor_and_switch_channels_with_data(data, device_ips)

    # 마지막 채널로 복귀 (광고 재생 직후 UI가 숫자 입력을 받을 때까지 대기)
    time.sleep(5)
    for device_id, channel in final_channels.items():
        print(f"\n디바이스 {device_id} → 마지막 채널 {channel} 으로 전환 중...")
        switch_channel_via_adb(channel, [device_id])

    # 로그 카운트
    counts = count_impression_logs(log_dir, log_files)
    print_impression_log_counts(counts)


if __name__ == "__main__":
    main()
