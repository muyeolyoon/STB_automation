import subprocess
import time
import re
import json
import requests
from datetime import datetime
from component.device_connect_multiple import get_device_ip, connect_device 

adb_path = r"C:\Program Files\platform-tools\adb.exe"

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# 감지할 키워드 및 상태 저장 구조
certification_keywords = {
    "sdkVersion": {"found": False, "value": None},
    "uuid": {"found": False, "value": None},
    "usePersonalizedAd": {"found": False, "value": None},
    "fullFirmwareVer": {"found": False, "value": None},
    "modelName": {"found": False, "value": None},
    "platformAdId": {"found": False, "value": None},
    "deviceTypeId": {"found": False, "value": None},
    "deviceId": {"found": False, "value": None},
    "appVersion": {"found": False, "value": None},
    "SSID": {"found": False, "value": None},
    "endpoints": {"found": False, "value": None},
    "fingerprint": {"found": False, "value": None},
    "baseDir": {"found": False, "value": None},
    "AdConfig": {"found": False, "value": None}
}

def parse_key_value_line(line, keys_to_find):
    detected = {}
    for key in keys_to_find:
        if not certification_keywords[key]["found"] and key in line:
            match = re.search(rf"{key}\s*=\s*(.*?)(?=,?\s+\w+=|$)", line)
            if match:
                value = match.group(1).strip().rstrip(",")
                detected[key] = value
    return detected

def parse_ssid_from_line(line):
    match = re.search(r"SSID:\s*(\S+)", line)
    if match:
        return match.group(1).strip()
    return None

def parse_fingerprint_from_json(line):
    try:
        json_match = re.search(r'({.*?})', line)
        if json_match:
            data = json.loads(json_match.group(1))
            if "fingerPrint" in data:
                return data["fingerPrint"]
    except json.JSONDecodeError:
        pass
    return None

def main():
    device_ip = get_device_ip()
    if not connect_device(device_ip):
        print("프로그램을 종료합니다.")
        return
    
    print("셋탑박스 재부팅 시작...")
    subprocess.run([adb_path, "-s", device_ip, "reboot"])
    print("재부팅 중... 약 180초 대기")
    time.sleep(180)
    
    print(f"{current_time_str()} 로그 모니터링 시작됨")
    command = f'"{adb_path}" -s {device_ip} logcat -v time'
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
    start_time = time.time()
    timeout = 100

    try:
        while True:
            line = process.stdout.readline()
            if not line:
                continue

            if "AnypointAD" not in line and "ANYPOINT_SDK" not in line:
                continue

            # SSID 추출
            if "SSID" in line and not certification_keywords["SSID"]["found"]:
                ssid = parse_ssid_from_line(line)
                if ssid:
                    certification_keywords["SSID"]["found"] = True
                    certification_keywords["SSID"]["value"] = ssid
                    print(f"\n[SSID] = {ssid}")

            # fingerprint 추출 (JSON 형식)
            fingerprint = parse_fingerprint_from_json(line)
            if fingerprint and not certification_keywords["fingerprint"]["found"]:
                certification_keywords["fingerprint"]["found"] = True
                certification_keywords["fingerprint"]["value"] = fingerprint
                print(f"\n[fingerprint] = {fingerprint}")

            # AdConfig / endpoints 줄 전체 감지
            if not certification_keywords["AdConfig"]["found"] and "AdConfig" in line:
                certification_keywords["AdConfig"]["found"] = True
                certification_keywords["AdConfig"]["value"] = line.strip()
                print(f"\n[AdConfig] = {line.strip()}")

            elif not certification_keywords["endpoints"]["found"] and "endpoints" in line:
                certification_keywords["endpoints"]["found"] = True
                certification_keywords["endpoints"]["value"] = line.strip()
                print(f"\n[endpoints] = {line.strip()}")

            # JSON 객체 파싱
            json_match = re.search(r'({.*})', line)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    for key in certification_keywords:
                        if key in ("AdConfig", "endpoints", "SSID", "fingerprint"):
                            continue
                        if not certification_keywords[key]["found"] and key in data:
                            certification_keywords[key]["found"] = True
                            certification_keywords[key]["value"] = data[key]
                            print(f"\n[{key}] = {data[key]}")
                except json.JSONDecodeError:
                    pass

            # key=value 로그 파싱
            parsed = parse_key_value_line(line, certification_keywords.keys())
            for key, value in parsed.items():
                if key in ("AdConfig", "endpoints", "SSID", "fingerprint"):
                    continue
                certification_keywords[key]["found"] = True
                certification_keywords[key]["value"] = value
                print(f"\n[{key}] = {value}")

            # 전체 키 감지 시 종료
            if all(info["found"] for info in certification_keywords.values()):
                print(f"\n{current_time_str()} ✅ 모든 키워드 감지 완료. 감시 종료.")
                break

            # 타임아웃
            if time.time() - start_time > timeout:
                print(f"{current_time_str()} 유효 시간 초과. 감시 종료.")
                break

    except KeyboardInterrupt:
        print(f"{current_time_str()} 사용자에 의해 중단됨.")

    finally:
        process.terminate()

if __name__ == "__main__":
    main()
