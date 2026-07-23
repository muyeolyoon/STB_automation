import subprocess
import time

# ADB 경로와 디바이스 IP
adb_path = r"adb"
device_ip = "192.168.10.59"

# 로그 기준 키워드
log_keyword = "current channel(sid) updated: DeviceChannel"


def send_key(keycode):
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "keyevent", str(keycode)])


def channel_up():
    send_key(166)


def channel_down():
    send_key(167)


def clear_logcat():
    subprocess.run([adb_path, "-s", device_ip, "logcat", "-c"])


# ✅ 로그에서 키워드 일부만 포함 되어도 PASS
def check_log_for_keyword(keyword, timeout=5):
    try:
        logcat_proc = subprocess.Popen(
            [adb_path, "-s", device_ip, "logcat", "-d"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, _ = logcat_proc.communicate(timeout=timeout)


        for line in stdout.splitlines():
            if keyword.lower() in line.lower():
                return True

    except subprocess.TimeoutExpired:
        print("⏰ 로그 확인 중 시간 초과")
    return False


# 테스트 실행
if __name__ == "__main__":
    clear_logcat()

    print("3초 후 채널UP")
    time.sleep(3)
    channel_up()

    print("채널UP 후 3초간 로그 확인 중...")
    time.sleep(3)

    if check_log_for_keyword(log_keyword):
        print("✅ 채널UP 테스트 통과")
    else:
        print("❌ 채널UP 테스트 실패")

    clear_logcat()

    print("3초 후 채널DOWN")
    time.sleep(3)
    channel_down()

    print("채널DOWN 후 3초간 로그 확인 중...")
    time.sleep(3)

    if check_log_for_keyword(log_keyword):
        print("✅ 채널DOWN 테스트 통과")
    else:
        print("❌ 채널DOWN 테스트 실패")