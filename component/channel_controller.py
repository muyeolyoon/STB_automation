import subprocess
import time

def switch_channel(channel_number, device_ip):
    keyevent_map = {str(i): 7 + i for i in range(10)}
    for digit in str(channel_number):
        if digit in keyevent_map:
            subprocess.run(["adb", "-s", device_ip, "shell", "input", "keyevent", str(keyevent_map[digit])])
            time.sleep(0.5)
