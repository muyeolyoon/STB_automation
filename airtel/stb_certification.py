import subprocess
import time
import requests
import re
import hashlib
import os, sys

# 내부 모듈 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from component.setting import AirtelSetting

timeout = 800          # 전체 타임아웃(초)
timeout02 = 30          # kid=true id 감지 후 추가 대기(초)

extracted_values = {
    r"fingerPrint": None,
    r"soId": None,
    r"uuid": None,
    r"firmwareVer": None,  
    r"usePersonalizedAd": None,
    r"freeStorage": None,
    r"usedStorage": None,
    r"cachedStorage": None,
    r"modelName": None,
    r"zipcode": None,
    r"deviceId": None,
    r"deviceTypeId": None
}

# dynamic_hashed_ssid = None
# fingerprint_result = "비교 실패"

certification_keywords = {
    r"deviceId": False,
    r"deviceTypeId": False,
    r"uuid": False,
    r"monitoringInterval": False,
    r"auth": False,
    r"requestAds": False,
    r"adSyncResult": False,
    r"appLog": False,
    r"event": False,
    r"pushServers": False,
    r"stateLog": False,
    r"impressionLog": False,
    r"ntpServers": False,
    r"proxyAdLog": False,
    r"assetRequest": False,
    r"id": False,
    r"delay": False,
    r"serviceId": False,
    r"placementIds": False,
    r"testPlacementIds": False,
    r"maxDownloadBandwidth": False,
    r"appPath": False,
    r"maxUsableStorage": False,
    r"minFreeStorage": False,
    r"trackingRetryInterval": False,
    r"trackingRetryCount": False,
    r"remnantTimeThreshold": False,
    r"maxEndAdPlaytime": False,
    r"transitionDelay": False,
    r"overPlayTimeThreshold": False,
    r"videoPlayMode": False,
    r"videoMediaType": False,
    r"startDelay": False,
    r"stopDelay": False,
    r"startRenderDelay": False,
    r"stopRenderDelay": False,
    r"chViewMinTime": False,
    r"crc": False,
    r"left": False,
    r"top": False,
    r"width": False,
    r"height": False,
    r"accessToken": False,
    r"pushSecretKey": False,
}

# --------[ 3) 상태 플래그 ]--------
kid_true_ids          = []
monitoring_started    = False
monitoring_finished   = False
monitoring_start_time = None
monitoring_end_time   = None
invalid_logs_detected = False

def double_sha256(value: str) -> str:
    """두 번 SHA-256"""
    return hashlib.sha256(hashlib.sha256(value.encode()).hexdigest().encode()).hexdigest()

def notify_summary(summary: str):
    print(f"[notify] {summary}")


def reboot_and_restart():
    print("셋탑박스 재부팅 중...")
    subprocess.run([AirtelSetting.adb_path, "-s", AirtelSetting.device_ip, "reboot"])
    time.sleep(60)
    print("스크립트 재시작...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)

print("셋탑박스 재부팅 시작...")
subprocess.run([AirtelSetting.adb_path, "-s", AirtelSetting.device_ip, "reboot"])
time.sleep(60)

print("AddrAD 로그 감시 시작")
process = subprocess.Popen(
    [AirtelSetting.adb_path, "-s", AirtelSetting.device_ip, "logcat"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    errors="ignore"
)

start_time       = time.time()
kid_detected_time = None

try:
    while True:
        # ----------[ 전역 종료 조건 ]----------
        if monitoring_finished:
            print("모니터링 종료 로그 감지됨 → 즉시 다음 단계로 이동")
            break
        if time.time() - start_time > timeout:
            print("타임아웃 종료")
            break

        line = process.stdout.readline()
        if not line or "AddrAD" not in line:
            continue

        if "failed to authenticate" in line.lower():
            print("인증 실패 감지됨! 재부팅 및 재시작")
            notify_summary("인증 실패 감지됨. 셋탑박스를 재부팅하고 스크립트를 재시작합니다.")
            process.terminate()
            reboot_and_restart()

        if "start monitoring" in line:
            monitoring_started    = True
            monitoring_start_time = time.time()
            print("모니터링 시작 감지")

        if "monitoring finished" in line:
            monitoring_finished  = True
            monitoring_end_time  = time.time()
            print("모니터링 종료 감지")

        if monitoring_started and not monitoring_finished:
            if time.time() - monitoring_start_time > 1500:
                monitoring_finished = True
                monitoring_end_time = time.time()
                print("모니터링 종료 로그 미감지 → 15분 경과로 종료 처리")

        if "push server" in line.lower():
            print("push server 연결 확인됨")

        if ("topActivityClassName: com.lguplus.android.tv.pineone.UplusMainActivity" in line
            and "last channel sid:" in line):
            invalid_logs_detected = True
            print("[오류] last channel 정보가 노출됨")

        # if "ssid" in line.lower() and not dynamic_hashed_ssid:
        #     m = re.search(r"ssid[:=]\s*([^\s,\"']+)", line, re.IGNORECASE)
        #     if m:
        #         dynamic_hashed_ssid = double_sha256(m.group(1))
        #         print(f"SSID 해시값: {dynamic_hashed_ssid}")

        for key in extracted_values:
            if extracted_values[key] is None:
                m = re.search(rf'"?{re.escape(key)}"?[:=]\s*"?([^\s",}}]+)"?', line, re.IGNORECASE)
                if m:
                    extracted_values[key] = m.group(1)
                    print(f"{key} : {extracted_values[key]}")

        # key가 정규식 전체 문자열인지, 일반 key:value인지 구분
        for key in certification_keywords:
            if not certification_keywords[key]:
                if re.fullmatch(r".*\(.*\).*|.*:.*", key):  # 괄호나 콜론 포함된 정규식일 경우
                    if key in line:
                        certification_keywords[key] = True
                else:
                    if re.search(rf"{key}\s*[:=]\s*[^\s,)\]]+", line):
                        certification_keywords[key] = True


        m = re.search(r'ProgramProviderChannel\((.*?)\)', line)
        # if m:
        #     inner = m.group(1)
        #     if "kid=true" in inner:
        #         id_m = re.search(r'id\s*=\s*(\d+)', inner)
        #         if id_m:
        #             kid_id = id_m.group(1)
        #             if kid_id not in kid_true_ids:
        #                 kid_true_ids.append(kid_id)
        #                 print(f"kid=true id : {kid_id}")
        #                 if kid_detected_time is None:
        #                     kid_detected_time = time.time()

        #         if (
        #             all(certification_keywords.values()) and
        #             all(extracted_values.values()) and
        #             # dynamic_hashed_ssid and
        #             kid_detected_time is not None and
        #             time.time() - kid_detected_time >= timeout02 and
        #             monitoring_finished
        #         ):
        #             print("모든 조건 충족, 종료합니다.")
        #             break

except KeyboardInterrupt:
    print("사용자 중단")

finally:
    process.terminate()

    # if extracted_values["fingerPrint"] and dynamic_hashed_ssid:
    #     fingerprint_result = ("PASS" if extracted_values["fingerPrint"] == dynamic_hashed_ssid
    #                           else "FAIL")

    summary_lines = ["**Airtel 인증 항목 감지 결과 요약**"]

    summary_lines.append("\n[추출된 값]")
    for k, v in extracted_values.items():
        summary_lines.append(f"{k}: {v}")

    missing_keys = [k for k, v in certification_keywords.items() if not v]
    if not missing_keys:
        summary_lines.append("\n[certification 키워드 감지] 모든 키워드 감지됨")
    else:
        summary_lines.append("\n[certification 키워드 누락 항목]")
        for k in missing_keys:
            summary_lines.append(f"fail: {k}")

    # if kid_true_ids:
    #     summary_lines.append("\n[kid=true ID 감지]")
    #     for kid_id in kid_true_ids:
    #         summary_lines.append(f"kid=true : id={kid_id}")
    # else:
    #     summary_lines.append("\n[kid=true ID 감지] 없음")

    # summary_lines.append(f"\n[SSID → FingerPrint 비교 결과] → {fingerprint_result}")

    if monitoring_started and monitoring_finished:
        summary_lines.append("\n[Monitoring 감지] 시작됨 → 종료됨 정상적으로 동작")
    else:
        summary_lines.append("\n[Monitoring 감지] 시작/종료 로그 없음 또는 미완료")

    if invalid_logs_detected:
        summary_lines.append("\n[경고] last channel 정보가 로그에 노출되었습니다.")
    else:
        summary_lines.append("\n[확인] last channel 정보 노출 없음")

    summary_lines.append(f"\n종료 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 알림
    notify_summary("\n".join(summary_lines))
    print("알림 및 종료 완료")
