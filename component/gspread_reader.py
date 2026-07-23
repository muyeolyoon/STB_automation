import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime


def sheet_tab_name():
    return datetime.now().strftime("%y%m%d")


def list_all_sheet_names(service_account_path, spreadsheet_key):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(service_account_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(spreadsheet_key)
    return [ws.title for ws in sheet.worksheets()]


def load_sheet_data(service_account_path, spreadsheet_key, sheet_name=None, section=None):
    """편성표 로드. 기본은 Drive Excel(YYMMDD 모니터링 탭, SKB/U+ 블록 분리)."""
    if section is None and sheet_name and "skb" in sheet_name.lower():
        section = "skb"
        sheet_name = None

    source = os.environ.get("SCHEDULE_SOURCE", "drive_xlsx").strip().lower()
    if source != "gspread":
        from component.schedule_loader import load_schedule_data

        return load_schedule_data(
            service_account_path,
            spreadsheet_key=spreadsheet_key,
            sheet_name=sheet_name,
            section=section,
            source="drive_xlsx",
        )

    from component.schedule_loader import load_gspread_monitoring_data

    if sheet_name is None:
        sheet_name = f"{sheet_tab_name()} 모니터링"

    print(f"[schedule] gspread 탭: '{sheet_name}'")
    return load_gspread_monitoring_data(
        service_account_path,
        spreadsheet_key,
        sheet_name=sheet_name,
        section=section,
    )
