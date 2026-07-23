import os
import time
import threading
from datetime import datetime
import sys
import subprocess

# 현재 파일 기준으로 상위 폴더(stb-rpa) 경로를 sys.path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(stbrpa_dir)
from component.device_connect_multiple import connect_multiple_devices


def is_app_running(package_name: str, device_ip: str) -> bool:
    try:
        result = subprocess.check_output(
            ['adb', '-s', device_ip, 'shell', 'pidof', package_name],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return bool(result)
    except subprocess.CalledProcessError:
        return False


def monitor_app(package_name: str, device_ip: str, interval: int = 5):
    print(f"📡 Monitoring {package_name} on {device_ip}")
    while True:
        running = is_app_running(package_name, device_ip)
        if not running:
            try:
                subprocess.run(['adb', '-s', device_ip, 'shell', 'monkey', '-p', package_name, '1'], check=True, capture_output=True)
                print(f"[{time.strftime('%H:%M:%S')}] Started {package_name} on {device_ip}")
            except subprocess.CalledProcessError:
                print(f"[{time.strftime('%H:%M:%S')}] Failed to start {package_name} on {device_ip}")
        status = "🟢 RUNNING" if running else "🔴 NOT RUNNING"
        print(f"[{time.strftime('%H:%M:%S')}] {package_name} on {device_ip} is {status}")
        time.sleep(interval)


def main():
    device_ips = connect_multiple_devices()
    if not device_ips:
        print("연결된 디바이스가 없습니다.")
        return

    # 앱 패키지명 입력
    package_to_monitor = input("모니터링할 앱의 패키지명을 입력하세요 (예: com.example.app): ").strip()
    if not package_to_monitor:
        print("패키지명을 입력하지 않았습니다.")
        return

    # 모니터링 주기 입력
    try:
        interval = int(input("모니터링 주기(초)를 입력하세요 (예: 5): ").strip())
        if interval <= 0:
            raise ValueError
    except ValueError:
        print("유효한 숫자를 입력하세요 (1 이상의 정수).")
        return

    print("\n모든 디바이스에 대해 앱 모니터링을 시작합니다...\n")

    # 모든 디바이스에 대해 모니터링 스레드 실행
    threads = []
    for device_ip in device_ips:
        t = threading.Thread(target=monitor_app, args=(package_to_monitor, device_ip, interval))
        t.daemon = True  # 메인 종료 시 함께 종료
        t.start()
        threads.append(t)

    # 메인 스레드는 종료되지 않도록 유지
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n모니터링을 종료합니다.")


if __name__ == "__main__":
    main()
