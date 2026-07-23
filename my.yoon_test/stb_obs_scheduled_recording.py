import time
from datetime import datetime, timedelta
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from component.obs_recorder import OBSRecorder

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def scheduled_recording(start_time_str, end_time_str):
    # OBS 연결
    obs = OBSRecorder()
    obs.connect()
    print(f"[{current_time_str()}] OBS 연결 완료")

    try:
        # 시작 시간 파싱 (오늘 날짜로 가정)
        today = datetime.now().date()
        start_time = datetime.strptime(start_time_str, "%H:%M").replace(year=today.year, month=today.month, day=today.day)
        end_time = datetime.strptime(end_time_str, "%H:%M").replace(year=today.year, month=today.month, day=today.day)

        # 종료 시간이 시작 시간보다 빠르면 다음 날로 설정
        if end_time <= start_time:
            end_time += timedelta(days=1)

        print(f"[{current_time_str()}] 녹화 시작 예정: {start_time}")
        print(f"[{current_time_str()}] 녹화 종료 예정: {end_time}")

        # 시작 시간까지 대기
        while datetime.now() < start_time:
            remaining = (start_time - datetime.now()).total_seconds()
            print(f"[{current_time_str()}] 녹화 시작까지 {int(remaining)}초 남음")
            time.sleep(min(60, remaining))  # 1분마다 체크

        # 녹화 시작
        obs.start_recording()
        print(f"[{current_time_str()}] 녹화 시작")

        # 종료 시간까지 대기
        while datetime.now() < end_time:
            remaining = (end_time - datetime.now()).total_seconds()
            print(f"[{current_time_str()}] 녹화 종료까지 {int(remaining)}초 남음")
            time.sleep(min(60, remaining))  # 1분마다 체크

        # 녹화 중지
        obs.stop_recording()
        print(f"[{current_time_str()}] 녹화 종료")

    except Exception as e:
        print(f"❌ 오류 발생: {e}")
    finally:
        # OBS 해제
        obs.disconnect()
        print(f"[{current_time_str()}] OBS 연결 해제")

def main():
    # 시간 입력 받기
    start_time = input("녹화 시작 시간 (HH:MM): ")
    end_time = input("녹화 종료 시간 (HH:MM): ")

    scheduled_recording(start_time, end_time)

if __name__ == "__main__":
    main()