"""
임시 진단 스크립트: 재인님이 "중복이 너무 많다"고 하신 부분을 직접 눈으로 확인하기 위해
2026/08~09 범위의 모든 행을 날짜/시간 순으로 그대로 출력함 (가공/필터링 없음).
"""
from google_sheet import get_client, SPREADSHEET_ID

client = get_client()
spreadsheet = client.open_by_key(SPREADSHEET_ID)
ws = spreadsheet.worksheet("경제발표")
all_values = ws.get_all_values()
print(f"전체 행 수 (헤더 포함): {len(all_values)}")

data_rows = all_values[2:]  # 헤더 2줄 건너뜀

target = []
for i, row in enumerate(data_rows):
    if len(row) >= 6 and (row[1].startswith("2026/08") or row[1].startswith("2026/09")):
        target.append((i + 3, row))  # 실제 시트 행번호(1-index)

print(f"\n2026/08~09 범위 행 수: {len(target)}\n")

for sheet_row, row in target:
    date_ = row[1] if len(row) > 1 else ""
    weekday = row[2] if len(row) > 2 else ""
    time_ = row[3] if len(row) > 3 else ""
    country = row[4] if len(row) > 4 else ""
    name = row[5] if len(row) > 5 else ""
    note = row[6] if len(row) > 6 else ""
    print(f"row{sheet_row}: {date_} {weekday} {time_} | {country} | {name} | 비고={note}")
