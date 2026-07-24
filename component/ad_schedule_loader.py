# ad_schedule_loader.py
from datetime import datetime


from component.schedule_loader import load_ad_schedule
from component.setting import Setting


class AdScheduleLoader:
    def __init__(self):
        self.service_account_path = Setting.SERVICE_ACCOUNT_PATH
    def _notify(self, message):
        print(f"[notify] {message}")



    def load_schedule(self):
        try:
            rows = load_ad_schedule(
                self.service_account_path,
                section="uplus",
            )
            ad_schedule = []
            for row in rows:
                ad_time = row["ad_time"]
                ad_schedule.append(
                    {
                        "channel_name": row["channel_name"],
                        "channel": row["channel"],
                        "ad_time": ad_time,
                    }
                )
            return ad_schedule
        except Exception as e:
            self._notify(f"[오류] 광고 스케줄 로드 실패: {e}")
            exit()
