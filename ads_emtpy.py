import subprocess
import time
import re
import json
from datetime import datetime

adb_path = r"adb"
device_ip = "192.168.10.10:5555"
endpoint_path = "http://192.168.10.150/UHD3/ads_empty"

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

# 감지할 키워드 및 값 저장 구조
certification_keywords = {
    r"fetched ads:": False,
    r"== target ads sync finished: ready=true, status=OK": False
}

# 로그 초기화
print(f"{current_time_str()} 로그 초기화")
subprocess.run([adb_path, "-s", device_ip, "logcat", "-c"])
time.sleep(1)

# endpoint 변경
print(f"{current_time_str()} endpoint 변경")
subprocess.run([adb_path, "-s", device_ip, "shell", "am", "broadcast", "-a", "tv.anypoint.agent.app.CHANGE_TEST_PROPERTY",
                "--es", "change.command", "CHANGE_API_ENDPOINT", "--es", "api.endpoint", endpoint_path], text=True)
time.sleep(5)

# log 모니터링 시작
print(f"{current_time_str()} 로그 모니터링 시작됨")
command = f'"{adb_path}" -s {device_ip} logcat -v time'
process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(3)

# updateAssetCommand
print(f"{current_time_str()} updateAssetCommand")
subprocess.run([adb_path, "-s", device_ip, "shell", "am", "broadcast", "-a", "tv.anypoint.sdk.AD_SYNC"], text=True)
time.sleep(3)


start_time = time.time()
timeout = 100

try:
    while True:
        if time.time() - start_time > timeout:
            print(f"{current_time_str()} 유효 시간 초과. 감시 종료.")
            break

        line = process.stdout.readline()
        if not line:
            continue
        # 필터링: 원하는 로그 태그만 통과              
        if not any(tag in line for tag in ["AnypointAD", "ANYPOINT_SDK", "AnypointAD_D"]):
            continue
                      
        # 키워드 감지 처리
        for keyword in certification_keywords:
            if not certification_keywords[keyword] and re.search(keyword, line, re.IGNORECASE):
                certification_keywords[keyword] = True
                print(f"\n감지됨: '{keyword}'")
                print(f"로그 내용: {line.strip()}")
                
                      

        if all(certification_keywords.values()):
            print(f"\n{current_time_str()} 모든 주요 키워드 감지 완료. 감시 종료.")
            break
                
except KeyboardInterrupt:
    print(f"{current_time_str()} 사용자에 의해 중단됨.")
    
