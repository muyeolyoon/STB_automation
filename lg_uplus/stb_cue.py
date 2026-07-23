import subprocess
import time
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from datetime import datetime, timedelta
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import threading
from component.setting import Setting, Slack
from component.channel_controller import switch_channel
from component.ad_schedule_loader import AdScheduleLoader

# 파일 경로 세팅
timestamp = datetime.now().strftime("%y%m%d%H%M")
filtered_log_file = f"AddrAD_{timestamp}.log"
recording_file = f"{timestamp}.mp4"
log_file = f"ad_log_{timestamp}.txt"

# 슬랙 클라이언트
client = WebClient(token=Slack.SLACK_BOT_TOKEN)
log_proc = None

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

class Recorder:
    @staticmethod
    def start(device_ip, recording_file):
        print("녹화 시작")
        subprocess.Popen([
            Setting.adb_path, "-s", device_ip, "shell", "screenrecord", f"/sdcard/{recording_file}"
        ])

    @staticmethod
    def stop(device_ip, recording_file):
        print("녹화 종료")
        subprocess.run([Setting.adb_path, "-s", device_ip, "shell", "pkill", "-l", "2", "screenrecord"])
        time.sleep(2)
        subprocess.run([Setting.adb_path, "-s", device_ip, "pull", f"/sdcard/{recording_file}", f"./{recording_file}"])

class LogCapture:
    @staticmethod
    def start(device_ip):
        global log_proc
        print("AddrAD 로그 수집 시작")

        def capture():
            with open(filtered_log_file, "w", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    [Setting.adb_path, "-s", device_ip, "logcat"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore"
                )
                global log_proc
                log_proc = proc
                for line in proc.stdout:
                    if "AddrAD" in line:
                        f.write(line)
                        f.flush()

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()

    @staticmethod
    def stop():
        global log_proc
        print("AddrAD 로그 수집 종료")
        if log_proc:
            log_proc.terminate()
            log_proc.wait()
            time.sleep(2)
            if os.path.exists(filtered_log_file) and os.path.getsize(filtered_log_file) > 10:
                SlackReporter.send_file(filtered_log_file, "AddrAD 필터 로그")
            else:
                print("유효한 AddrAD 로그가 없어 Slack 전송 생복")

class AdMonitor:
    def __init__(self, device_ip, recording_file):
        self.device_ip = device_ip
        self.recording_file = recording_file
        self.ad_blocks = []
        self.current_block = []
        self.ad_count = 0
        self.recording = False

    def log_to_file(self, lines):
        with open(log_file, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def start(self):
        print("로그 감시 시작")
        subprocess.run([Setting.adb_path, "-s", self.device_ip, "logcat", "-c"])
        process = subprocess.Popen([
            Setting.adb_path, "-s", self.device_ip, "logcat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        cue_received = False

        try:
            for line in process.stdout:
                line = line.strip()

                # 광고 감시 시작 조건: receive cue → addPlaylistItem
                if "receive cue" in line:
                    cue_received = True
                    print("[신호] receive cue 감지됨")

                elif cue_received and "addPlaylistItem" in line:
                    print("[신호] addPlaylistItem 감지됨 - 광고 감시 본격 시작")
                    self.current_block.append("[시작] 광고 감시 시작")
                    cue_received = False  # 초기화 후 감시 시작
                    self.recording = True
                    Recorder.start(self.device_ip, self.recording_file)

                elif r"AdPlayItem: AdPlayItem ad" in line:
                    self.ad_count += 1
                    self.current_block.append(f"[{self.ad_count}] 광고 시작 인텐트: {line}")

                elif "ImpressionLog(deviceId=" in line:
                    self.current_block.append(f"[{self.ad_count}] Impression 로그: {line}")

                elif "https://art-stats-api.anypoint.tv/v3/device/impression-logs" in line:
                    if "200" in line:
                        self.current_block.append(f"[{self.ad_count}] POST Impression 로그: {line}")

                elif "success to send impression-logs" in line:
                    self.current_block.append(f"[{self.ad_count}] 전송 성공 로그: {line}")
                    if self.recording:
                        Recorder.stop(self.device_ip, self.recording_file)
                        self.recording = False
                    if self.current_block:
                        self.ad_blocks.append(self.current_block)
                    messages = [f"*[{i+1}] 광고 감지 결과:*\n" + "\n".join(block) for i, block in enumerate(self.ad_blocks)]
                    self.log_to_file(messages)
                    SlackReporter.send_file(log_file, "광고 로그 텍스트")
                    SlackReporter.send_file(self.recording_file, "광고 녹화 영상")
                    break


        except KeyboardInterrupt:
            print("사용자 중단됨.")
        finally:
            process.terminate()


def main():
    ad_schedule = AdScheduleLoader().load_schedule()
    LogCapture.start(Setting.device_ip)

    for ad in ad_schedule:
        now = datetime.now()
        ad_time = ad['ad_time']
        channel = ad['channel']
        channel_name = ad['channel_name']
        switch_time = ad_time - timedelta(minutes=2, seconds=30)
        monitor_time = ad_time - timedelta(seconds=15)

        if now >= ad_time:
            continue

        while datetime.now() < switch_time:
            print(f"{datetime.now()} 대기 중... (채널 전환 예정: {switch_time})")
            time.sleep(5)

        print(f"[전환] {channel_name} ({channel}) 채널로 전환")
        switch_channel(channel, Setting.device_ip)

        time_to_monitor = (monitor_time - datetime.now()).total_seconds()
        if time_to_monitor > 0:
            print(f"{datetime.now()} 광고 감시 대기 중... (감시 시작 예정: {monitor_time})")
            time.sleep(time_to_monitor)

        monitor = AdMonitor(Setting.device_ip, recording_file)
        monitor.start()
        break  # 하나 감시 후 종료

    LogCapture.stop()

if __name__ == "__main__":
    main()
