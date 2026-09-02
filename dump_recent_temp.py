"""
임시 진단 스크립트: 2026-09-02 천연가스/엔화 반복감지 알림 역확인을 위해
진폭 시트 최근 행을 정확한 값 그대로 출력. 확인 후 삭제 예정.
"""
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME

client = get_client()
spreadsheet = client.open_by_key(SPREADSHEET_ID)
ws = spreadsheet.worksheet(SHEET_NAME)
all_values = ws.get_all_values()
print(f"전체 행 수: {len(all_values)}")

for i, row in enumerate(all_values[-20:]):
    real_row = len(all_values) - 20 + i + 1
    print(f"row{real_row}: {row}")
