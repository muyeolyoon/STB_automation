import subprocess

# ADB 연결 함수
def connect_devices(device_ip):
    result = subprocess.run(
        ["adb", "devices"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    connected_devices = result.stdout or ""

    if device_ip.split(":")[0] in connected_devices:
        print(f"ℹ이미 연결됨: {device_ip}")
        return True

    result = subprocess.run(
        ["adb", "connect", device_ip],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout.strip().lower()

    if "connected to" in output:
        print(f"ADB 연결 성공: {device_ip}")
        return True
    elif "unable" in output or "failed" in output:
        print(f"ADB 연결 실패: {device_ip} -> {output}")
        return False
    else:
        print(f"예상치 못한 응답: {output}")
        return False

# device_ip를 입력받는 함수 (여러 대 연결 처리)
def get_device_ips():
    try:
        device_count = int(input("연결할 ADB 디바이스 수를 입력하세요 (예: 1이면 1대 연결, 4면 4대 연결): ").strip())
        if device_count < 1:
            print("디바이스 수는 1개 이상이어야 합니다.")
            return []
        
        device_ips = []
        for i in range(device_count):
            device_ip = input(f"연결할 {i+1}번째 ADB 디바이스 IP를 입력하세요 (예: 192.168.10.153:5555): ").strip()
            device_ips.append(device_ip)
        
        return device_ips
    except ValueError:
        print("잘못된 입력입니다. 숫자를 입력해주세요.")
        return []

# 여러 디바이스에 연결 시도
def connect_multiple_devices():
    device_ips = get_device_ips()
    if not device_ips:
        return []

    for device_ip in device_ips:
        connect_devices(device_ip)

    return device_ips
