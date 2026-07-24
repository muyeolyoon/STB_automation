# ad_schedule_loader.py

from component.schedule_loader import load_ad_schedule
from component.setting import Setting


class SkbAdScheduleLoader:
    def __init__(self):
        self.service_account_path = Setting.SERVICE_ACCOUNT_PATH
    def _notify(self, message):
        print(f"[notify] {message}")



    def load_schedule(self):
        try:
            return load_ad_schedule(
                self.service_account_path,
                section="skb",
            )
        except Exception as e:
            self._notify(f"[오류] 광고 스케줄 로드 실패: {e}")
            exit()
