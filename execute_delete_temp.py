"""
임시 실행 스크립트: 확인된 중복 행(영웅문 스타일 신규 중복)을 실제로 삭제.
재인님 결정: "기존(YoY/MoM/QoQ) 유지, 신규(전년비/전월비/전분기비 및 공백차이 중복) 삭제".

삭제 전, 각 행의 현재 텍스트가 예상과 일치하는지 재확인한 뒤에만 삭제 진행 (안전장치).
행 번호가 큰 것부터 삭제해서 삭제할 때마다 아래쪽 행이 밀리는 문제를 방지.
"""
from google_sheet import get_client, SPREADSHEET_ID

# (행번호, 예상 발표명) - 값이 일치할 때만 삭제
TARGETS = [
    (740, "7월 CPI상승률(전년비)"),
    (741, "7월 CPI상승률(전월비)-계절조정"),
    (742, "7월 근원CPI상승률(전월비)"),
    (759, "7월 소매판매(전월비)"),
    (774, "7월 산업생산(전월비)"),
    (776, "7월 잠정주택판매(전월비)"),
    (819, "7월 PCE가격지수(전년비)"),
    (820, "7월 PCE가격지수(전월비)"),
    (823, "7월 근원PCE가격지수(전월비)"),
    (892, "8월 CPI상승률(전년비)"),
    (893, "8월 CPI상승률(전월비)-계절조정"),
    (894, "8월 근원CPI상승률(전월비)"),
    (910, "8월 소매판매(전월비)"),
    (923, "8월 잠정주택판매(전월비)"),
    (925, "8월 산업생산(전월비)"),
    (729, "7월 기존주택판매"),
    (802, "7월 신규주택판매"),
    (881, "8월 기존주택판매"),
]

client = get_client()
spreadsheet = client.open_by_key(SPREADSHEET_ID)
ws = spreadsheet.worksheet("경제발표")

# 1) 삭제 전 검증: 현재 시트 행 내용이 기대값과 일치하는지 확인
all_values = ws.get_all_values()
verified = []
mismatches = []
for row_num, expected_name in TARGETS:
    idx = row_num - 1  # 0-indexed
    if idx < len(all_values):
        actual_row = all_values[idx]
        actual_name = actual_row[5] if len(actual_row) > 5 else ""
        if actual_name == expected_name:
            verified.append((row_num, expected_name))
        else:
            mismatches.append((row_num, expected_name, actual_name))
    else:
        mismatches.append((row_num, expected_name, "(행 없음)"))

print(f"검증 통과: {len(verified)}건 / 불일치: {len(mismatches)}건")
for row_num, expected_name, actual_name in mismatches:
    print(f"  ⚠️ row{row_num}: 예상='{expected_name}' 실제='{actual_name}' -> 삭제 건너뜀")

if not verified:
    print("삭제할 행이 없습니다. 종료.")
else:
    # 2) 큰 행 번호부터 삭제 (아래에서 위로) - 삭제할 때마다 아래쪽 행 번호가 당겨지는 걸 방지
    verified_sorted = sorted(verified, key=lambda x: -x[0])
    deleted = []
    for row_num, expected_name in verified_sorted:
        ws.delete_rows(row_num)
        deleted.append((row_num, expected_name))
        print(f"  🗑️ row{row_num} 삭제됨: {expected_name}")

    print(f"\n=== 총 {len(deleted)}건 삭제 완료 ===")
