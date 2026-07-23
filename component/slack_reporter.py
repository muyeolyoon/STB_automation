# slack_reporter.py

import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from setting import Slack

client = WebClient(token=Slack.SLACK_BOT_TOKEN)

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
