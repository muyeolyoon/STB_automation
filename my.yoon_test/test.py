import os
import time
import threading
from datetime import datetime, timedelta
import sys

# 현재 파일 기준 상위 폴더(stb-rpa) 경로를 sys.path에 추가해 모듈 임포트 가능하게 함
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

# 필요한 커스텀 모듈 임포트
from component.gspread_reader import load_sheet_data
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs
from component.whitelist import get_whitelist_channels, is_whitelisted

# 구글 스프레드시트 키와 서비스 계정 경로
SPREADSHEET_KEY = "1LTex75-xh9YgcwLiXDmYq8I-fZhQM5wx17mxHX8D88o"
SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"

# 유틸 함수: 현재 시간 포맷 문자열 반환
def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# ADB keyevent 번호 매핑 (숫자 키)
keyevent_map = {str(i): 7 + i for i in range(10)}

# ADB 명령으로 채널 번호 입력 함수 (멀티 디바이스 지원)
def switch_channel_via_adb(channel_number, devices):
    def input_channel(device):
        for digit in str(channel_number):
            if digit in keyevent_map:
                os.system(f"adb -s {device} shell input keyevent {keyevent_map[digit]}")
                time.sleep(0.2)  # 딜레이

    threads = []
    for device in devices:
        t = threading.Thread(target=input_channel, args=(device,))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

# 광고 감시 및 채널 전환 함수
def monitor_and_switch_channels_with_data(data, devices, whitelist_channels):
    ignored_channels = set()  # 한 번 무시한 채널을 저장해서 중복 출력 방지
    while True:
        try:
            now = datetime.now()
            ads_remaining_today = []

            # 앞으로 1시간 내 광고 스케줄만 필터링
            for row in data:
                try:
                    ad_time = datetime.strptime(row["광고편성 시간"], "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second)
                    if timedelta(seconds=0) < (ad_time_today - now) < timedelta(hours=1):
                        ads_remaining_today.append(ad_time_today)
                except:
                    continue

            # 광고 스케줄 없으면 종료
            if not ads_remaining_today:
                print(f"{current_time_str()} ✅ 광고 편성이 모두 끝났습니다. 프로그램을 종료합니다.")
                break

            for row in data:
                channel_name = row["채널명"]
                channel_number = row["채널번호"]
                ad_time_str = row["광고편성 시간"]

                try:
                    ad_time = datetime.strptime(ad_time_str, "%H:%M:%S")
                    ad_time_today = now.replace(hour=ad_time.hour, minute=ad_time.minute, second=ad_time.second, microsecond=0)
                    switch_time = ad_time_today - timedelta(seconds=30)

                    # 화이트리스트에 없으면 건너뛰고, 한 번만 무시 메시지 출력
                    if not is_whitelisted(channel_name, whitelist_channels):
                        if channel_name not in ignored_channels:
                            print(f"[무시됨] '{channel_name}'은 화이트리스트에 없으므로 건너뜁니다.")
                            ignored_channels.add(channel_name)
                        continue

                    # 스위치 시간 범위 안이면 채널 전환
                    if switch_time <= now < (ad_time_today - timedelta(seconds=15)):
                        print(f"{current_time_str()} 📺 광고 예정 채널(화이트리스트 확인됨): {channel_name} ({channel_number})")
                        switch_channel_via_adb(channel_number, devices)
                        print(f"{current_time_str()} ⏳ 광고 대기 및 재생")
                        time.sleep(160)  # 광고 재생 시간 대기
                        break

                except Exception as e:
                    print(f"{current_time_str()} ❌ 오류 발생 ({channel_name}): {e}")

        except Exception as err:
            print(f"{current_time_str()} 💥 반복 중 오류 발생: {err}")
            time.sleep(30)

def main():
    # 여러 디바이스 연결
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결된 디바이스가 없습니다.")
        return

    # 화이트리스트 채널 가져오기
    whitelist_channels = get_whitelist_channels()

    # 로그 저장 세팅
    log_dir = "D:/python_test/anypointmedia-QA/test_log"
    filters = ["AnypointAD", "ANYPOINT_SDK"]
    os.makedirs(log_dir, exist_ok=True)

    # 로그 저장 스레드 시작
    log_threads = save_multiple_devices_logs(device_ips, log_dir, filters=filters)

    # 스프레드시트 데이터 불러오기
    data = load_sheet_data(SERVICE_ACCOUNT_PATH, SPREADSHEET_KEY)

    # 광고 감시 및 채널 전환 시작 (화이트리스트 채널만 이동)
    monitor_and_switch_channels_with_data(data, device_ips, whitelist_channels)

if __name__ == "__main__":
    main()
