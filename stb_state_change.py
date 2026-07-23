import subprocess
import time
import locale

adb_path = "/opt/homebrew/bin/adb"
device_ip = "192.168.10.11"

def send_key(keycode):
    subprocess.run([adb_path, "-s", device_ip, "shell", "input", "keyevent", str(keycode)])

def GO_HOME():
    send_key(3)

def GO_LIVE():
    send_key(4)

def GO_Sleep():
    send_key(26)

def clear_logcat():
    subprocess.run([adb_path, "-s", device_ip, "logcat", "-c"])

# 모든 키워드가 있어야 통과 (AND)
def check_log_contains_all_keywords(required_keywords, timeout=5):
    detected = {keyword: False for keyword in required_keywords}
    try:
        logcat_proc = subprocess.Popen(
            [adb_path, "-s", device_ip, "logcat", "-d"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout_bytes, _ = logcat_proc.communicate(timeout=timeout)
        stdout = stdout_bytes.decode(locale.getpreferredencoding(), errors="replace")

        for line in stdout.splitlines():
            for keyword in required_keywords:
                if keyword.lower() in line.lower():
                    if not detected[keyword]:
                        print(f"감지된 키워드 [{keyword}]: {line.strip()}")
                        detected[keyword] = True

        return all(detected.values())

    except subprocess.TimeoutExpired:
        print("로그 확인 중 시간 초과")
    return False

# keyword 포함되면 통과
def check_log_contains_any_keyword(keywords, timeout=5):
    try:
        logcat_proc = subprocess.Popen(
            [adb_path, "-s", device_ip, "logcat", "-d"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout_bytes, _ = logcat_proc.communicate(timeout=timeout)
        stdout = stdout_bytes.decode(locale.getpreferredencoding(), errors="replace")

        for line in stdout.splitlines():
            for keyword in keywords:
                if keyword.lower() in line.lower():
                    print(f"감지된 로그: {line.strip()}")
                    return True
    except subprocess.TimeoutExpired:
        print("로그 확인 중 시간 초과")
    return False

# 테스트 실행
if __name__ == "__main__":
    result_lines = []

    # 테스트1: 홈 → 뒤로가기
    print("\n[테스트 1] 홈 화면 진입 후 이전 채널 복귀")

    clear_logcat()
    print("홈 화면 이동 중...")
    GO_HOME()
    time.sleep(3)

    # 홈 진입 로그 체크
    home_keywords = [
        "tv event action: tv.anypoint.sdk.SDK_STATE_CHANGE",
        "new state: APP_START"
    ]
    home_pass = check_log_contains_all_keywords(home_keywords)

    clear_logcat()
    print("이전 채널로 복귀 중...")
    GO_LIVE()
    time.sleep(5)

    # 이전 채널 복귀 로그 체크
    back_pass = check_log_contains_all_keywords(["enter tv state"])

    if home_pass and back_pass:
        result_lines.append("테스트 1 통과: 홈 → 이전 채널 복귀 성공")
    else:
        if not home_pass:
            result_lines.append("테스트1 실패: 홈 진입 키워드 미감지")
        if not back_pass:
            result_lines.append("테스트1 실패: 이전 채널 키워드 미감지")
    print("\n⌛ 테스트 2 시작 전 10초 대기 중...")
    time.sleep(10)

# <<<<<<< HEAD
    # ✅ 테스트 2: 슬립 모드 진입 후 해제
# =======
    # 테스트2: 슬립 모드 진입 후 해제
# >>>>>>> b32839d (셋탑 상태변경 작업)
    print("\n[테스트 2] 슬립 모드 진입 후 해제")
    clear_logcat()
    print("슬립 모드 진입 중...")
    GO_Sleep()
    time.sleep(5)

    print("슬립 모드 해제 중...")
    GO_Sleep()
    time.sleep(5)

    sleep_keywords = [
        "tv event action: tv.anypoint.sdk.SDK_STATE_CHANGE",
        "new state: SLEEP_MODE_START",
        "tv event action: android.intent.action.SCREEN_OFF",
        "enter tv state",
        "posted event. event=StateChangeEvent"
    ]

    if check_log_contains_any_keyword(sleep_keywords):
        result_lines.append("테스트2 통과: 슬립 모드 해제 후 정상 상태")
    else:
        result_lines.append("테스트2 실패: 로그 키워드 미감지")

    # 결과 출력
    print("\n테스트 결과 요약:")
    for line in result_lines:
<<<<<<< HEAD
        print(line)
=======
        print(line)

>>>>>>> c2271ea (셋탑 상태변경 작업)
