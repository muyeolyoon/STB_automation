"""Drive Excel 편성표 탭 목록·샘플 row 확인 (공유 설정 검증용)."""

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
stbrpa_dir = os.path.abspath(os.path.join(current_dir, ".."))
if stbrpa_dir not in sys.path:
    sys.path.append(stbrpa_dir)

from component.schedule_loader import (  # noqa: E402
    DEFAULT_DRIVE_FILE_ID,
    load_schedule_data,
    list_drive_excel_tabs,
    resolve_worksheet_tab,
)
from component.gspread_reader import sheet_tab_name  # noqa: E402

SERVICE_ACCOUNT_PATH = r"D:\python_test\anypointmedia-QA\stb-rpa\service_account.json"
DRIVE_FILE_ID = os.environ.get("DRIVE_SCHEDULE_FILE_ID", DEFAULT_DRIVE_FILE_ID)


def main():
    print(f"오늘 날짜 탭 후보: {sheet_tab_name()} / {sheet_tab_name()} 모니터링")
    print(f"Drive file ID: {DRIVE_FILE_ID}")
    print(f"Service account: {SERVICE_ACCOUNT_PATH}\n")

    tabs = list_drive_excel_tabs(SERVICE_ACCOUNT_PATH, DRIVE_FILE_ID)
    print(f"탭 {len(tabs)}개:")
    for name in tabs:
        print(f"  - {name}")

    import openpyxl
    from component.schedule_loader import download_drive_excel

    cache = os.path.join(
        r"D:\python_test\anypointmedia-QA\test_log",
        "_schedule_cache",
        f"probe_{sheet_tab_name()}.xlsx",
    )
    path = download_drive_excel(SERVICE_ACCOUNT_PATH, DRIVE_FILE_ID, cache_path=cache)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tab = resolve_worksheet_tab(wb)
    print(f"\n선택된 탭: {tab}")
    wb.close()

    data_uplus = load_schedule_data(
        SERVICE_ACCOUNT_PATH,
        drive_file_id=DRIVE_FILE_ID,
        cache_dir=os.path.dirname(cache),
        section="uplus",
    )
    data_skb = load_schedule_data(
        SERVICE_ACCOUNT_PATH,
        drive_file_id=DRIVE_FILE_ID,
        cache_dir=os.path.dirname(cache),
        section="skb",
    )
    print(f"\nU+ row 수: {len(data_uplus)} / SKB row 수: {len(data_skb)}")
    print("U+ 샘플:", data_uplus[0])
    print("SKB 샘플:", data_skb[0])


if __name__ == "__main__":
    main()
