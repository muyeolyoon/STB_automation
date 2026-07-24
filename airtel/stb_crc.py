import subprocess 
import time
import re
import requests
import threading
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import json
from datetime import datetime
from component.setting import AirtelSetting

timestamp = datetime.now().strftime("%y%m%d%H%M")
filtered_log_file = f"AddrAD_{timestamp}.log"



def notify_file(file_path, title=None):
    print(f"[notify skipped] file={file_path} title={title}")



def notify(message):
    print(f"[notify] {message}")



def start_log_capture():
    global log_proc
    print("AddrAD 로그 수집 시작")
    print("이전 logcat 로그 초기화...")
    subprocess.run(
        [AirtelSetting.adb_path, "-s", AirtelSetting.device_ip, "logcat", "-c"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    def capture_and_filter():
        with open(filtered_log_file, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                [AirtelSetting.adb_path, "-s", AirtelSetting.device_ip, "logcat"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            global log_proc
            log_proc = proc
            for line in proc.stdout:
                if any(keyword in line for keyword in ["AddrAD", "download try count:", "valid crc32"]):
                    f.write(line)
                    f.flush()

    thread = threading.Thread(target=capture_and_filter, daemon=True)
    thread.start()


def stop_log_capture():
    global log_proc
    print("AddrAD 로그 수집 종료")
    if log_proc:
        log_proc.terminate()
        log_proc.wait()
        time.sleep(2)
        if os.path.exists(filtered_log_file) and os.path.getsize(filtered_log_file) > 10:
            notify_file(filtered_log_file, "AddrAD 필터 로그")
        else:
            print("유효한 AddrAD 로그가 없어 알림 생략")
    else:
        print("로그 수집 프로세스가 존재하지 않음")


def run_adb_command(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return result.stdout
    except Exception as e:
        print(f"ADB 명령 실행 오류: {e}")
        return ""


def send_api_endpoint(endpoint):
    command = f"{AirtelSetting.adb_path} shell am broadcast -a tv.anypoint.agent.app.CHANGE_TEST_PROPERTY --es change.command CHANGE_API_ENDPOINT --es api.endpoint {endpoint}"
    print(f"endpoint 변경: {endpoint}")
    run_adb_command(command)


def reboot_device():
    print("디바이스 재부팅 중...")
    run_adb_command(f"{AirtelSetting.adb_path} reboot")
    time.sleep(10)
    wait_for_device()


def wait_for_device():
    print("디바이스 연결 대기 중...")
    while True:
        output = run_adb_command(f"{AirtelSetting.adb_path} get-state")
        if "device" in output:
            print("디바이스 연결 완료")
            break
        time.sleep(2)


def send_asset_update_command(device_id=None):
    command = f"{AirtelSetting.adb_path} -s {AirtelSetting.device_ip} shell am broadcast -a tv.anypoint.sdk.AD_SYNC"
    print(f"ADB 브로드캐스트 전송 중: {command}")
    output = run_adb_command(command)
    print(f"ADB 브로드캐스트 결과:\n{output}")


def find_asset_by_crc(crc_value):
    ads_file = "C:\\Apache24\\htdocs\\v3\\device\\ads"  # 해당 pc의 ads 경로 수정해주세요~~

    try:
        with open(ads_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            ads = data.get("ads", [])
            for ad in ads:
                asset = ad.get("asset")
                if asset and asset.get("crc") == crc_value:
                    asset_id = asset.get("assetId")
                    print(f"매칭된 CRC: {crc_value}, assetId: {asset_id}")
                    return asset_id, crc_value
    except Exception as e:
        print(f"ads 파일 읽기 오류: {e}")

    print(f"[STB CRC 결과] CRC {crc_value}에 해당하는 asset 정보 없음")
    return None

def check_crc_log():
    print("실시간 logcat 감시 시작 (최대 6분)...")
    start_time = time.time()
    max_duration = 9 * 60

    process = subprocess.Popen(
        [AirtelSetting.adb_path, "logcat"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    found_try_count = False
    found_crc_log = False
    crc_value = None

    try:
        for line in process.stdout:
            elapsed = int(time.time() - start_time)
            if elapsed > max_duration:
                print("9분 초과, 로그 감시 종료")
                break

            if not found_try_count:
                if re.search(r'download try count:\s*3', line):
                    found_try_count = True
                    notify("[1단계] 'download try count: 3' 감지됨")
            else:
                crc_match = re.search(r'but valid crc32:\s*(\d+)', line)
                if crc_match:
                    found_crc_log = True
                    crc_value = crc_match.group(1)
                    notify(f"[2단계] CRC 로그 감지됨, CRC: {crc_value}")
                    break

    except Exception as e:
        print(f"logcat 감시 중 오류: {e}")
    finally:
        process.terminate()

    return found_try_count and found_crc_log, crc_value


def main():
    device_id = 57125576
    send_asset_update_command(device_id)
    start_log_capture()

    success, crc_value = check_crc_log()

    if success and crc_value:
        asset_info = find_asset_by_crc(crc_value)
        if asset_info:
            asset_id, asset_crc = asset_info
            message = (
                f"[STB airtel CRC 결과]\n"
                f"CRC 소재 다운로드 실패 감지\n"
                f"Asset ID: {asset_id}\n"
                f"CRC: {asset_crc}"
            )
        else:
            message = f"[STB CRC 결과] CRC {crc_value}에 해당하는 asset 정보 없음"
    else:
        message = "[STB CRC 결과] 다운로드 실패 소재 없음"

    notify(message)
    print("모든 동작 완료. 로그 수집 종료 및 저장")
    time.sleep(5)
    stop_log_capture()


if __name__ == "__main__":
    main()
