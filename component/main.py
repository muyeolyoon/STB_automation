# main.py

import os
import sys
import time
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
if stbrpa_dir not in sys.path:
    sys.path.append(stbrpa_dir)

from channel_controller import ChannelController
from ad_monitor import AdMonitor
from component.schedule_loader import load_schedule_data
from setting import DEVICE_IP, SERVICE_ACCOUNT_PATH


def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def monitor_and_switch_channels(data):
    while True:
        try:
            now = datetime.now()
            channel_to_switch = None

            for row in data:
                channel_name = row["채널명"]
                channel = row["채널번호"]
                ad_time_str = row["광고편성 시간"]

                try:
                    ad_time = datetime.strptime(ad_time_str, "%H:%M:%S")
                    ad_time_today = now.replace(
                        hour=ad_time.hour,
                        minute=ad_time.minute,
                        second=ad_time.second,
                        microsecond=0,
                    )
                    switch_time = ad_time_today - timedelta(seconds=60)

                    if switch_time <= now < ad_time_today:
                        channel_to_switch = (channel_name, channel)
                        break

                except Exception as e:
                    print(f"{current_time_str()} 오류 ({channel_name}): {e}")

            if channel_to_switch:
                channel_name, channel = channel_to_switch
                print(f"{current_time_str()} 광고 예정 채널 {channel_name} ({channel})")
                ChannelController.switch_channel(channel)
                print(f"{current_time_str()} 광고 대기 중...")
                ad_monitor = AdMonitor(channel_name, channel)
                ad_monitor.start_monitoring()
                time.sleep(4 * 60)
            else:
                print(f"{current_time_str()} 대기 중 - 전환할 채널 없음")

            time.sleep(30)

        except Exception as loop_err:
            print(f"{current_time_str()} 반복 에러: {loop_err}")
            time.sleep(30)


def main():
    data = load_schedule_data(SERVICE_ACCOUNT_PATH, section="uplus")
    print(f"[schedule] U+ 편성 {len(data)}건 감시 시작")
    monitor_and_switch_channels(data)


if __name__ == "__main__":
    main()
