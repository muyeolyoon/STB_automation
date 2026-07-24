import subprocess
import time
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
from datetime import datetime, timedelta
import threading
from component.setting import AirtelSetting
from component.channel_controller import switch_channel
from component.ad_schedule_loader import SkbAdScheduleLoader
from component.obs_recorder import OBSRecorder

# 파일 경로 세팅
timestamp = datetime.now().strftime("%y%m%d%H%M")
filtered_log_file = f"AddrAD_{timestamp}.log"
log_file = f"ad_log_{timestamp}.txt"

# 슬랙 클라이언트
log_proc = None

class LocalNotifier:
    @staticmethod
    def send_file(file_path, title=None):
        print(f"[notify skipped] file={file_path} title={title}")



class LogCapture:
    @staticmethod
    def start(device_ip):
        global log_proc
        print("AddrAD 로그 수집 시작")

        def capture():
            with open(filtered_log_file, "w", encoding="utf-8") as f:
                proc = subprocess.Popen(
                    [AirtelSetting.adb_path, "-s", device_ip, "logcat"],
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
                LocalNotifier.send_file(filtered_log_file, "AddrAD 필터 로그")
            else:
                print("유효한 AddrAD 로그가 없어 알림 생복")

class AdMonitor:
    def __init__(self, device_ip):
        self.device_ip = device_ip
        self.ad_blocks = []
        self.current_block = []
        self.ad_count = 0

    def log_to_file(self, lines):
        with open(log_file, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def start(self, timeout_seconds=60):
        print("로그 감시 시작")
        subprocess.run([AirtelSetting.adb_path, "-s", self.device_ip, "logcat", "-c"])
        process = subprocess.Popen([
            AirtelSetting.adb_path, "-s", self.device_ip, "logcat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        cue_received = False
        cue_time = datetime.now()

        try:
            for line in process.stdout:
                line = line.strip()

                # 60초 타임아웃 검사
                if not cue_received and (datetime.now() - cue_time).total_seconds() > timeout_seconds:
                    print("⏱️ 60초 내에 'receive cue' 감지 실패 - 다음 스케줄로 이동")
                    break  # 감시 종료

                if "receive cue" in line:
                    cue_received = True
                    print("[신호] receive cue 감지됨")

                elif cue_received and "addPlaylistItem" in line:
                    print("[신호] addPlaylistItem 감지됨 - 광고 감시 본격 시작")
                    self.current_block.append("[시작] 광고 감시 시작")

                elif r"AdPlayItem: AdPlayItem ad" in line:
                    self.ad_count += 1
                    self.current_block.append(f"[{self.ad_count}] 광고 시작 인텐트: {line}")

                elif "ImpressionLog(deviceId=" in line:
                    self.current_block.append(f"[{self.ad_count}] Impression 로그: {line}")

                elif "https://art-stats-api.anypoint.tv/v3/device/impression-logs" in line and "200" in line:
                    self.current_block.append(f"[{self.ad_count}] POST Impression 로그: {line}")

                elif "success to send impression-logs" in line:
                    self.current_block.append(f"[{self.ad_count}] 전송 성공 로그: {line}")
                    if self.current_block:
                        self.ad_blocks.append(self.current_block)
                    messages = [f"*[{i+1}] 광고 감지 결과:*\n" + "\n".join(block) for i, block in enumerate(self.ad_blocks)]
                    self.log_to_file(messages)
                    LocalNotifier.send_file(log_file, "광고 로그 텍스트")
                    break

        except KeyboardInterrupt:
            print("사용자 중단됨.")
        finally:
            process.terminate()



def main():
    ad_schedule = SkbAdScheduleLoader().load_schedule()
    LogCapture.start(AirtelSetting.device_ip)
    
    date_str = datetime.now().strftime("%y%m%d_%H%M%S")
    recorder = OBSRecorder(password="123456")
    recorder.connect()
    print("obs 연결")

    recorder.start_recording()

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
        switch_channel(channel, AirtelSetting.device_ip)

        time_to_monitor = (monitor_time - datetime.now()).total_seconds()
        if time_to_monitor > 0:
            print(f"{datetime.now()} 광고 감시 대기 중... (감시 시작 예정: {monitor_time})")
            time.sleep(time_to_monitor)

        monitor = AdMonitor(AirtelSetting.device_ip)
        monitor.start(timeout_seconds=60)  # 60초 안에 receive cue가 없으면 중단 후 다음 광고로

        # 성공적으로 광고 감시가 진행되었으면 break
        if monitor.ad_blocks:
            break  # 광고 감시 성공 시 종료

    LogCapture.stop()
    recorder.stop_recording()  # 녹화 종료

if __name__ == "__main__":
    main()
