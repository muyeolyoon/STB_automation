# ad_monitor.py

import subprocess
import time
from datetime import datetime
from setting import DEVICE_IP
class AdMonitor:
    def __init__(self, channel_name, channel_number):
        self.channel_name = channel_name
        self.channel_number = channel_number
        self.recording_file = f"{datetime.now().strftime('%y%m%d%H%M')}_{channel_number}.mp4"

    def start_monitoring(self):
        print(f"광고 감시 시작 - 채널 {self.channel_name} ({self.channel_number})")
        subprocess.run(["adb", "-s", DEVICE_IP, "logcat", "-c"])

        process = subprocess.Popen(
            ["adb", "-s", DEVICE_IP, "logcat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        try:
            for line in process.stdout:
                if "AdPlayItem" in line:
                    print("광고 감지됨, 녹화 시작")
                    self.start_recording()
                    time.sleep(20)  # 20초간 녹화
                    self.stop_recording()
                    break
        except KeyboardInterrupt:
            print("사용자 중단")
        finally:
            process.terminate()

    def start_recording(self):
        subprocess.Popen(["adb", "-s", DEVICE_IP, "shell", "screenrecord", f"/sdcard/{self.recording_file}"])

    def stop_recording(self):
        subprocess.run(["adb", "-s", DEVICE_IP, "shell", "pkill", "-l", "2", "screenrecord"])
        time.sleep(2)
        subprocess.run(["adb", "-s", DEVICE_IP, "pull", f"/sdcard/{self.recording_file}", f"./{self.recording_file}"])
