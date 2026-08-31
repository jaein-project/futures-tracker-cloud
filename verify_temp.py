import sys
sys.path.insert(0, ".")
from google_sheet import get_client, SPREADSHEET_ID, append_daily_summary, DAILY_SUMMARY_SHEET

client = get_client()
ss = client.open_by_key(SPREADSHEET_ID)

TEST_DATE = "__TEST_DELETE_ME__"
first_row = ["", TEST_DATE, "오전 9:00:00", "206", "86", "314", "48", "30", "9", "4"]
last_row = [TEST_DATE, "오전 5:05:00", "375", "153", "1930", "96", "118", "88", "36", ""]

append_daily_summary(ss, TEST_DATE, first_row, last_row)

ws = ss.worksheet(DAILY_SUMMARY_SHEET)
values = ws.get_all_values()
print(f"'일일요약' 탭 총 행 수: {len(values)}")
print("헤더:", values[0])
print("마지막 행(테스트 데이터):", values[-1])
