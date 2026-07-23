# ad_schedule_loader.py
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from component.schedule_loader import load_ad_schedule
from component.setting import Setting, Slack


class AdScheduleLoader:
    def __init__(self):
        self.service_account_path = Setting.SERVICE_ACCOUNT_PATH
        self.slack_client = WebClient(token=Slack.SLACK_BOT_TOKEN)

    def _send_slack_message(self, message):
        try:
            self.slack_client.chat_postMessage(channel=Slack.CHANNEL_ID, text=message)
        except SlackApiError as e:
            print(f"Slack 메시지 실패: {e.response['error']}")

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
            self._send_slack_message(f"[오류] 광고 스케줄 로드 실패: {e}")
            exit()
