import os
import time
import threading
from datetime import datetime, timedelta
import sys
import re

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)

from component.obs_recorder import OBSRecorder
from component.device_connect_multiple import connect_multiple_devices
from component.save_logs import save_multiple_devices_logs

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def monitor_scte35_logs(devices, recorder, log_dir="D:/python_test/anypointmedia-QA/test_log"):
    """
    SCTE35 로그를 모니터링하여 "Splice Insert" 명령어가 감지되면 OBS 녹화를 시작/종료
    """
    os.makedirs(log_dir, exist_ok=True)
    filters = ["AnypointAD", "ANYPOINT_SDK"]

    recording_active = False
    recording_start_time = None

    def on_log_line_received(device, line):
        nonlocal recording_active, recording_start_time

        # "Splice Insert" 명령어 감지
        if "tv.anypoint.impl.scte35.Scte35DecoderImpl.decode35(295) - Splice Insert" in line:
            print(f"{current_time_str()} 🎬 SCTE35 Splice Insert 감지 - 녹화 시작")

            if not recording_active:
                recorder.start_recording()
                recording_active = True
                recording_start_time = datetime.now()
                print(f"{current_time_str()} 🎥 OBS 녹화 시작")

        # 녹화 중지 로직 (필요시 추가 조건)
        # 예: 일정 시간 후 자동 중지 또는 다른 신호 감지 시
        if recording_active and recording_start_time:
            elapsed = datetime.now() - recording_start_time
            if elapsed.total_seconds() > 120:  # 2분 후 자동 중지
                recorder.stop_recording()
                recording_active = False
                recording_start_time = None
                print(f"{current_time_str()} ⏹️ OBS 녹화 자동 중지 (2분 경과)")

    # 로그 저장 및 모니터링 시작
    threads, filenames = save_multiple_devices_logs(devices, log_dir, filters=filters, on_impression_detected=on_log_line_received)

    print(f"{current_time_str()} 📊 SCTE35 로그 모니터링 시작...")

    # 메인 루프
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"{current_time_str()} 🛑 모니터링 중단")
        if recording_active:
            recorder.stop_recording()
            print(f"{current_time_str()} ⏹️ 녹화 강제 중지")

def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("❌ 연결된 디바이스가 없습니다.")
        return

    recorder = OBSRecorder(password="123456")
    recorder.connect()

    monitor_scte35_logs(device_ips, recorder)

if __name__ == "__main__":
    main()