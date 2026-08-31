import sys
sys.path.insert(0, ".")
from google_sheet import get_client, SPREADSHEET_ID, DAILY_SUMMARY_SHEET

client = get_client()
ss = client.open_by_key(SPREADSHEET_ID)
ws = ss.worksheet(DAILY_SUMMARY_SHEET)
values = ws.get_all_values()

for i, row in enumerate(values):
    if row and row[0] == "__TEST_DELETE_ME__":
        ws.delete_rows(i + 1)  # gspread는 1-based 행 번호
        print(f"테스트 행(시트 {i+1}행) 삭제 완료")
        break
else:
    print("테스트 행을 찾지 못함 (이미 삭제됐을 수 있음)")

print("최종 상태:")
for row in ws.get_all_values():
    print(row)
