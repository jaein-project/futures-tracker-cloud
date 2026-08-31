"""
임시 진단 스크립트: 재인님이 지적하신 "중복이 너무 많다" 문제의 실제 원인을 확인.
가설: 하나HTS 표기(YoY/MoM/QoQ)와 영웅문 표기(전년비/전월비/전분기비)가 서로 달라서
     기존의 문자열 완전일치 방식(_make_key)으로는 같은 발표를 다른 발표로 착각해
     중복으로 잡아내지 못했을 가능성.
방법: 같은 (날짜,시간) 안에서, 표기 방식 차이를 제거한 "핵심 이름"이 같은 행이
     2개 이상 있으면 그걸 잠재적 중복으로 출력.
"""
import re
from collections import defaultdict
from google_sheet import get_client, SPREADSHEET_ID

REPLACEMENTS = [
    ("YoY", ""), ("MoM", ""), ("QoQ", ""),
    ("(전년비)", ""), ("(전월비)", ""), ("(전분기비)", ""),
    ("-계절조정", ""), ("(계절조정)", ""),
    ("(잠정)", ""), ("(확정)", ""), ("(속보)", ""),
    ("(2차 예측)", ""), ("(1차 예측)", ""), ("(1차예측)", ""),
    ("(종합)", ""),
]

def strip_suffix_variants(name: str) -> str:
    s = name
    for a, b in REPLACEMENTS:
        s = s.replace(a, b)
    s = re.sub(r"\s+", "", s)
    return s

client = get_client()
spreadsheet = client.open_by_key(SPREADSHEET_ID)
ws = spreadsheet.worksheet("경제발표")
all_values = ws.get_all_values()
data_rows = all_values[2:]

target = []
for i, row in enumerate(data_rows):
    if len(row) >= 6 and (row[1].startswith("2026/08") or row[1].startswith("2026/09")):
        target.append((i + 3, row))

print(f"2026/08~09 범위 행 수: {len(target)}\n")

groups = defaultdict(list)
for sheet_row, row in target:
    date_ = row[1]
    time_ = row[3]
    name = row[5]
    core = strip_suffix_variants(name)
    groups[(date_, time_, core)].append((sheet_row, name))

dupes = {k: v for k, v in groups.items() if len(v) > 1}
print(f"=== 표기 차이(YoY/전년비 등)까지 감안한 잠재적 중복 그룹 수: {len(dupes)} ===\n")
for (date_, time_, core), rows in sorted(dupes.items()):
    print(f"[{date_} {time_}] core='{core}'")
    for sheet_row, name in rows:
        print(f"   row{sheet_row}: {name}")
