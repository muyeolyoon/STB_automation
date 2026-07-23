import os , sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import re
import time
import subprocess
from datetime import datetime, timedelta
import threading
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from component.setting import SkbSetting, Slack
# 기본 설정값
LOG_DIR = "cue_basic_motion_log"

client = WebClient(token=Slack.SLACK_BOT_TOKEN)
action_sequence = [
    "default", "search", "home", "channel_updown", "sleep"
]
CHANNEL_CHANGE_RE = re.compile(
    r"\[TvEventExtHandler\.enterChannel\]\[\d+\] current channel\(sid\) updated: .*"
)

STATE_POST_RE = re.compile(r"POST .*?/v3/device/state-logs")
STATE_200_RE  = re.compile(r"200 .*?/v3/device/state-logs")

def send_slack_message(message):
    try:
        client.chat_postMessage(channel=Slack.CHANNEL_ID, text=message)
    except SlackApiError as e:
        print(f"Slack 메시지 실패: {e.response['error']}")


class ADBLogSaver:
    def __init__(self, output_path, filters=None):
        self.output_path = output_path
        self.filters = filters if filters else ["AnypointAD", "ANYPOINT_SDK"]

    def line_matches_filter(self, line):
        return any(keyword in line for keyword in self.filters)

    def save_filtered_logs(self):
        print("adb logcat 실행 중... 필터링 저장 시작!")
        with open(self.output_path, 'w', encoding='utf-8') as outfile:
            process = subprocess.Popen(
                ["adb", "-s", SkbSetting.device_ip, "logcat", "-v", "time"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore',
                bufsize=1
            )
            try:
                for line in process.stdout:
                    if self.line_matches_filter(line):
                        outfile.write(line)
                        outfile.flush()
            except KeyboardInterrupt:
                print("사용자 중단")
                process.terminate()
            except Exception as e:
                print(f"에러 발생: {e}")
                process.terminate()

class Recorder:
    @staticmethod
    def start(device_ip, output_path):
        print(f"녹화 시작: {output_path}")
        subprocess.Popen([SkbSetting.adb_path, "-s", device_ip, "shell", "screenrecord", f"/sdcard/{output_path}"], stdout=subprocess.DEVNULL)

    @staticmethod
    def stop(device_ip, output_path):
        print(f"녹화 중지: {output_path}")
        subprocess.run([SkbSetting.adb_path, "-s", device_ip, "shell", "pkill", "-l", "2", "screenrecord"])
        time.sleep(2)
        subprocess.run([SkbSetting.adb_path, "-s", device_ip, "pull", f"/sdcard/{output_path}", output_path])

class SlackReporter:
    @staticmethod
    def send_file(file_path, title):
        if not os.path.exists(file_path):
            print(f"파일 없음: {file_path}")
            return
        try:
            with open(file_path, "rb") as f:
                client.files_upload_v2(
                    channel=Slack.CHANNEL_ID,
                    file=f,
                    filename=os.path.basename(file_path),
                    title=title
                )
            print(f"Slack 업로드 완료: {file_path}")
        except SlackApiError as e:
            print(f"Slack 업로드 실패: {e.response['error']}")

class PostAdAction:

    @staticmethod
    def _verify_channel_switch(device_ip: str, timeout: int = 15):
        proc = subprocess.Popen(
            [SkbSetting.adb_path, "-s", device_ip, "logcat", "-v", "time"],
            stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore"
        )
        deadline = time.time() + timeout
        post_seen = ok_seen = False
        change_line = None

        for line in proc.stdout:
            if CHANNEL_CHANGE_RE.search(line):
                change_line = line.rstrip()               
            elif STATE_POST_RE.search(line):
                post_seen = True
            elif STATE_200_RE.search(line):
                ok_seen = True

            if change_line and post_seen and ok_seen:
                proc.terminate()
                return True, change_line                    
            if time.time() > deadline:
                proc.terminate()
                return False, None

    @staticmethod
    def execute(device_ip, action_type="default"):
        print(f"광고 이후 동작 실행: {action_type}")
        action_map = {
            "default": [(24, "음량 up"), (25, "음량 down"), (164, "음소거"), (23, "확인"),(21, "left"), (66, "확인"), (4, "이전")],
            # "option_channel_list": [(170, "epg"),],
            "search": [(231, "검색"), (84, "검색"), (4, "이전")],
            "home": [(3, "홈"), (4, "이전"), (4, "이전")],
            "channel_updown": [(166, "채널 up"), (167, "채널 down")],
            "sleep": [(223, "슬립 ON"), (224, "슬립 OFF")]
        }
        actions = action_map.get(action_type, [])
        for key, desc in actions:
            print(f"입력: {desc}")
            subprocess.run([SkbSetting.adb_path, "-s", device_ip, "shell", "input", "keyevent", str(key)])
            time.sleep(3)

        # channel_updown 액션에 대해 state 검증 및 Slack 보고 추가
        if action_type == "channel_updown":
            verified, info = PostAdAction._verify_channel_switch(device_ip)
            msg01 = (
                f"채널 변경 및 state 확인 완료\n{info}"
                if verified else
                "채널 변경 또는 state 확인 실패"
            )
            send_slack_message(msg01)

        # channel_updown 액션에 대해 state 검증 및 Slack 보고 추가
        if action_type == "home":
            verified, info = PostAdAction._verify_channel_switch(device_ip)
            msg02 = (
                f"앱 진입 및 state 확인 완료\n{info}"
                if verified else
                "앱 진입 또는 state 확인 실패"
            )
            send_slack_message(msg02)


def get_unique_filename(directory, base_filename):
    filename = base_filename
    name, ext = os.path.splitext(base_filename)
    counter = 1
    while os.path.exists(os.path.join(directory, filename)):
        filename = f"{name}_{counter}{ext}"
        counter += 1
    return filename

def date_check():
    return datetime.now().strftime("%Y%m%d")

def sheet_tap_name():
    return datetime.now().strftime("%y%m%d")

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def switch_channel_via_adb(channel_number):
    keyevent_map = {str(i): 7 + i for i in range(10)}
    for digit in str(channel_number):
        if digit in keyevent_map:
            subprocess.run(["adb", "-s", SkbSetting.device_ip, "shell", "input", "keyevent", str(keyevent_map[digit])])
            time.sleep(0.5)

class AdMonitor:
    def __init__(self, device_ip, worksheet, ad_schedule, actions):
        self.device_ip = device_ip
        self.worksheet = worksheet
        self.ad_schedule = ad_schedule
        self.actions = actions
        self.current_action_index = 0

    def start(self):
        while self.current_action_index < len(self.actions):
            try:
                now = datetime.now()
                future_ads = [ad for ad in self.ad_schedule if ad['ad_time'] > now]
                if not future_ads:
                    print("남은 광고 스케줄이 없습니다.")
                    break

                closest_ad = min(future_ads, key=lambda x: x['ad_time'])
                ad_time = closest_ad['ad_time']
                channel = closest_ad['channel']
                channel_name = closest_ad['channel_name']
                switch_time = ad_time - timedelta(minutes=2)

                if now < switch_time:
                    print(f"{current_time_str()} 다음 광고 대기 중... (채널 전환 예정: {switch_time.strftime('%H:%M:%S')})")
                    time.sleep(10)
                    continue

                elif now >= switch_time and now < ad_time:
                    print(f"{current_time_str()} [예정] {channel_name} ({channel}) 채널 이동")
                    switch_channel_via_adb(channel)
                    # wait_duration = (ad_time - datetime.now()).total_seconds()

                    print(f"{current_time_str()} [진행] 광고 감시 시작")
                    success = self.monitor_ad_play()

                    if success:
                        self.current_action_index += 1
                    else:
                        print(f"{current_time_str()} 광고 감지 실패 → 다음 광고 시도")

                else:
                    print(f"{current_time_str()} 광고 시간 지남 (예정: {ad_time.strftime('%H:%M:%S')})")
                    time.sleep(5)

            except Exception as e:
                print(f"모니터링 에러: {e}")
                time.sleep(10)

    def monitor_ad_play(self):
        recording_file = f"{date_check()}_{self.actions[self.current_action_index]}_record.mp4"

        subprocess.run([SkbSetting.adb_path, "-s", self.device_ip, "logcat", "-c"])
        process = subprocess.Popen([
            SkbSetting.adb_path, "-s", self.device_ip, "logcat"
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="ignore")

        start_time = time.time()
        try:
            for line in process.stdout:
                if "AdPlayItem" in line:
                    print(f"{current_time_str()} 광고 감지됨 → 녹화 및 액션 시작")
                    Recorder.start(self.device_ip, recording_file)
                    time.sleep(15)
                    PostAdAction.execute(self.device_ip, self.actions[self.current_action_index])
                    Recorder.stop(self.device_ip, recording_file)
                    SlackReporter.send_file(recording_file, title=f"{self.actions[self.current_action_index]} 광고 녹화")
                    return True

                if time.time() - start_time > 200:
                    print(f"{current_time_str()} 90초 내 광고 감지 실패")
                    return False
        finally:
            process.terminate()

def load_ad_schedule():
    from component.schedule_loader import load_ad_schedule as _load_schedule_rows
    rows = _load_schedule_rows(SkbSetting.SERVICE_ACCOUNT_PATH, section="skb")
    return [
        {
            "ad_time": row["ad_time"],
            "channel": row["channel"],
            "channel_name": row["channel_name"],
        }
        for row in rows
    ]

def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    # 자동 생성되는 로그 파일 이름 (예: 20250508_153025_basic_motion.log)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{timestamp}_basic_motion"
    unique_filename = get_unique_filename(LOG_DIR, base_filename + ".log")
    output_path = os.path.join(LOG_DIR, unique_filename)

    log_saver = ADBLogSaver(output_path=output_path)
    log_thread = threading.Thread(target=log_saver.save_filtered_logs, daemon=True)
    log_thread.start()

    ad_schedule = load_ad_schedule()
    ad_schedule.sort(key=lambda x: x['ad_time'])

    monitor = AdMonitor(SkbSetting.device_ip, None, ad_schedule, action_sequence)
    monitor.start()

    print("모든 광고 감시 완료. 로그 파일을 Slack으로 전송합니다.")
    SlackReporter.send_file(output_path, title="광고 모니터링 로그 파일")

if __name__ == "__main__":
    main()
