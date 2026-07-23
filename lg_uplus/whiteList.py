import os
import time
import threading
from datetime import datetime, timedelta
import sys

WHITELIST_TERMINAL_LOG = r"D:\python_test\anypointmedia-QA\test_log\whitelist_terminal.log"


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()

# 현재 파일 기준으로 상위 폴더(stb-rpa) 경로를 sys.path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_devices
from component.save_logs import save_multiple_devices_logs

SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"
WHITELIST_DEVICE = "192.168.10.8:5555"
WHITELIST_LOG_FILENAME = "whitelist_test.log"


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def _normalize_channel_number(channel_number):
    if channel_number is None:
        return ""
    s = str(channel_number).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


# ✅ 화이트리스트 입력 함수 (채널번호)
def get_whitelist_channel_numbers():
    user_input = input("🎯 감시할 채널번호를 쉼표로 구분해서 입력하세요 (예: 320,321,322): ")
    whitelist = [
        _normalize_channel_number(ch)
        for ch in user_input.split(",")
        if ch.strip()
    ]
    print(f"[화이트리스트] 모니터링 대상 채널번호: {whitelist}")
    return whitelist

keyevent_map = {str(i): 7 + i for i in range(10)}  # 숫자 -> ADB keyevent 매핑

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

# ✅ 광고 감시 루프 (화이트리스트 적용)
def monitor_and_switch_channels_with_data(data, devices, whitelist):
    # 🔹 화이트리스트에 포함된 채널번호만 선별
    if whitelist:
        whitelist_set = set(whitelist)
        data = [
            row
            for row in data
            if _normalize_channel_number(row["채널번호"]) in whitelist_set
        ]

    watched = set()            # 오늘 이미 감시한 채널번호
    SIMILAR_WINDOW_SEC = 160   # 이 안에 겹치는 편성은 하나만 볼 수 있음(=광고 대기 시간)

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
                print(f"{current_time_str()} ✅ 광고 편성이 모두 끝났습니다. 프로그램을 종료합니다.")
                break

            # 🔹 지금 전환 시점(광고 30초 전)에 걸린 채널 수집
            triggered = []
            for row in data:
                try:
                    ad_time = datetime.strptime(row["광고편성 시간"], "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0)
                    switch_time = ad_time_today - timedelta(seconds=30)
                    if switch_time <= now < (ad_time_today - timedelta(seconds=15)):
                        triggered.append((ad_time_today, row))
                except Exception as e:
                    print(f"{current_time_str()} ❌ 오류 발생 ({row.get('채널명')}): {e}")

            if triggered:
                # 🔹 트리거 채널과 시간이 겹치거나 비슷한(≤SIMILAR_WINDOW_SEC) 편성 모두 수집
                #    한 번 전환하면 대기 동안 나머지를 놓치므로 같은 슬롯으로 취급
                base = min(t[0] for t in triggered)
                collision = []
                for row in data:
                    try:
                        ad_time = datetime.strptime(row["광고편성 시간"], "%H:%M:%S")
                        ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0)
                        if now <= ad_time_today <= base + timedelta(seconds=SIMILAR_WINDOW_SEC):
                            collision.append((ad_time_today, row))
                    except Exception:
                        continue

                # 🔹 겹치면 안 본 채널 우선. 안 본 채널이 없을 때만 기존처럼 전환.
                unwatched = [
                    c for c in collision
                    if _normalize_channel_number(c[1]["채널번호"]) not in watched
                ]
                pool = unwatched if unwatched else triggered
                chosen_time, chosen = min(pool, key=lambda c: c[0])

                channel_name = chosen["채널명"]
                channel_number = chosen["채널번호"]

                # 선택 채널이 아직 미래(비슷한 시간)면 광고 30초 전까지 대기
                lead = (chosen_time - timedelta(seconds=30) - datetime.now()).total_seconds()
                if lead > 0:
                    time.sleep(lead)

                tag = "안본채널" if _normalize_channel_number(channel_number) not in watched else "재시청"
                print(f"{current_time_str()} 📺 광고 예정 채널: {channel_name} ({channel_number}) [{tag}]")
                switch_channel_via_adb(channel_number, devices)
                print(f"{current_time_str()} ⏳ 광고 대기 및 재생")
                watched.add(_normalize_channel_number(channel_number))
                time.sleep(SIMILAR_WINDOW_SEC)

        except Exception as err:
            print(f"{current_time_str()} 💥 반복 중 오류 발생: {err}")
            time.sleep(30)


def main():
    if not connect_devices(WHITELIST_DEVICE):
        print(f"❌ ADB 연결 실패: {WHITELIST_DEVICE}")
        return
    device_ips = [WHITELIST_DEVICE]

    # 🔹 화이트리스트 입력 (채널번호)
    whitelist_channels = get_whitelist_channel_numbers()

    # 🔹 로그 저장 설정
    log_dir = "D:/python_test/anypointmedia-QA/test_log"
    os.makedirs(log_dir, exist_ok=True)
    terminal_fp = open(WHITELIST_TERMINAL_LOG, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, terminal_fp)
    filters = ["AnypointAD", "ANYPOINT_SDK"]
    save_multiple_devices_logs(
        device_ips,
        log_dir,
        filters=filters,
        log_filename=WHITELIST_LOG_FILENAME,
    )

    # 🔹 시트 데이터 불러오기
    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY)
    monitor_and_switch_channels_with_data(data, device_ips, whitelist_channels)

if __name__ == "__main__":
    main()
