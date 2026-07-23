import os
import time

# ADB 명령 실행
def run_adb_command(command):
    return os.popen(command).read().strip()

# IP 입력받고 연결
def get_and_connect_devices():
    while True:
        try:
            count = int(input("🔢 몇 대의 디바이스에 연결할까요? "))
            if count > 0:
                break
        except ValueError:
            pass
        print("❌ 숫자를 정확히 입력해주세요.")

    devices = []
    for i in range(count):
        ip = input(f"📡 [{i+1}]번 디바이스 IP 입력: ").strip()
        result = run_adb_command(f"adb connect {ip}")
        if "connected" in result or "already connected" in result:
            print(f"✅ 연결 성공: {ip}")
            devices.append(ip)
        else:
            print(f"❌ 연결 실패: {ip} ({result})")

    return devices

# 채널 숫자 입력을 keyevent로 전송
def send_channel_number(devices, channel):
    keyevent_map = {
        '0': 7, '1': 8, '2': 9, '3': 10, '4': 11,
        '5': 12, '6': 13, '7': 14, '8': 15, '9': 16
    }

    for digit in channel:
        if digit in keyevent_map:
            keycode = keyevent_map[digit]
            for device in devices:
                run_adb_command(f"adb -s {device} shell input keyevent {keycode}")
            time.sleep(0.2)  # 숫자 사이 딜레이

    # 입력된 채널 번호가 1자리 또는 2자리면 OK 입력
    if len(channel) <= 2:
        time.sleep(0.2)
        for device in devices:
            run_adb_command(f"adb -s {device} shell input keyevent 23")

# 모든 디바이스에 명령 실행
def send_keyevent_to_all(devices, keycode):
    for device in devices:
        run_adb_command(f"adb -s {device} shell input keyevent {keycode}")

# 리모컨 메뉴
def remote_menu(devices):
    while True:
        print("\n🎮 리모컨 메뉴:")
        print("1. 전원 (Power On/Off)")
        print("2. 홈 (Home)")
        print("3. 채널 번호 입력")
        print("4. 볼륨 업")
        print("5. 볼륨 다운")
        print("6. OK")
        print("0. 종료")

        choice = input("👉 번호를 입력하세요: ").strip()

        if choice == "1":
            send_keyevent_to_all(devices, 26)
        elif choice == "2":
            send_keyevent_to_all(devices, 3)
        elif choice == "3":
            channel = input("🔢 이동할 채널 번호 입력: ").strip()
            if channel.isdigit():
                send_channel_number(devices, channel)
            else:
                print("❌ 숫자만 입력해주세요.")
        elif choice == "4":
            send_keyevent_to_all(devices, 24)
        elif choice == "5":
            send_keyevent_to_all(devices, 25)
        elif choice == "6":
            send_keyevent_to_all(devices, 66)
        elif choice == "0":
            print("👋 리모컨 종료\n")
            break
        else:
            print("❌ 잘못된 입력입니다. 다시 선택해주세요.")

# 메인 실행
if __name__ == "__main__":
    print("📱 멀티 ADB 리모컨 시작")
    connected_devices = get_and_connect_devices()
    if connected_devices:
        print(f"\n✅ 총 {len(connected_devices)}대 디바이스 제어 준비 완료.")
        remote_menu(connected_devices)
    else:
        print("❌ 연결된 디바이스가 없습니다. 종료합니다.")
