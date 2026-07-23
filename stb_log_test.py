import subprocess
import time

# ADB 경로 및 디바이스 IP
adb_path = r"C:\Users\yui32\AppData\Local\Android\Sdk\platform-tools\adb.exe"
device_ip = "192.168.10.59:5555"

# 감시할 키워드들
keywords_to_check = [
    "https://art-device-state.anypoint.tv/v3/device/state-logs", "POST https://art-device-state.anypoint.tv/v3/device/state-logs", "updated: DeviceChannel", "event=StateChangeEvent", "freeStorage",
]

# 감지된 키워드 추적용
detected_keywords = set()

# ADB logcat 실행
process = subprocess.Popen(
    [adb_path, "-s", device_ip, "logcat"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

print("로그 키워드 감시 시작...\n")

try:
    for line in process.stdout:
        line = line.strip()

        for keyword in keywords_to_check:
            if keyword in line:
                if keyword not in detected_keywords:
                    print(f"[감지됨] '{keyword}' 포함 로그:")
                    detected_keywords.add(keyword)
                else:
                    print(f"[반복 감지] {keyword}")
                print(line)
                print("-" * 80)

        # 감지 안 된 키워드들도 주기적으로 출력
        if int(time.time()) % 15 == 0:
            not_detected = [k for k in keywords_to_check if k not in detected_keywords]
            if not_detected:
                print(f"아직 감지되지 않은 키워드: {', '.join(not_detected)}\n")

except KeyboardInterrupt:
    print("\n사용자 중단")
finally:
    process.terminate()
    print("\n[최종 요약]")
    print(f"감지된 키워드: {', '.join(detected_keywords) if detected_keywords else '없음'}")
    not_detected = [k for k in keywords_to_check if k not in detected_keywords]
    print(f"감지되지 않은 키워드: {', '.join(not_detected) if not_detected else '없음'}")
