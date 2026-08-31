"""
임시 스크립트: "기존(YoY/MoM/QoQ) 유지, 신규(전년비/전월비/전분기비) 중복 삭제" 방침에 따라
실제로 삭제해야 할 정확한 행 번호 목록을 뽑아냄 (아직 삭제는 안 함 - 목록만 출력).

핵심 로직:
- 같은 (날짜,시간) 안에서 "지표 종류"(YOY/MOM/QOQ/LEVEL)가 같고 "핵심 주제"가 같은 행이
  2개 이상이면 진짜 중복으로 판단.
- 지표 종류가 다르면(YoY vs MoM) 절대 같은 그룹으로 묶지 않음 (오탐 방지).
- 핵심 주제 비교 시 동의어(가격지수=물가지수) 처리.
- 그룹 내에서 "(전년비)/(전월비)/(전분기비)" 표기(영웅문 스타일, 괄호+한글)를 신규/삭제 대상으로,
  "YoY/MoM/QoQ" 표기(기존 하나HTS 스타일)를 유지 대상으로 판단.
"""
import re
from collections import defaultdict
from google_sheet import get_client, SPREADSHEET_ID

def metric_type(name: str):
    if "(전년비)" in name or re.search(r"\bYoY\b", name):
        return "YOY"
    if "(전월비)" in name or re.search(r"\bMoM\b", name):
        return "MOM"
    if "(전분기비)" in name or re.search(r"\bQoQ\b", name):
        return "QOQ"
    return "LEVEL"

def is_new_style(name: str) -> bool:
    return "(전년비)" in name or "(전월비)" in name or "(전분기비)" in name

STRIP_TOKENS = [
    "YoY", "MoM", "QoQ",
    "(전년비)", "(전월비)", "(전분기비)",
    "-계절조정", "(계절조정)",
    "(잠정)", "(확정)", "(속보)",
    "(2차 예측)", "(1차 예측)", "(1차예측)",
    "(종합)",
]

SYNONYMS = [
    ("가격지수", "물가지수"),
]

def core_topic(name: str) -> str:
    s = name
    for t in STRIP_TOKENS:
        s = s.replace(t, "")
    s = re.sub(r"\s+", "", s)
    for a, b in SYNONYMS:
        s = s.replace(a, b)
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

groups = defaultdict(list)
for sheet_row, row in target:
    date_, time_, name = row[1], row[3], row[5]
    mtype = metric_type(name)
    topic = core_topic(name)
    groups[(date_, time_, mtype, topic)].append((sheet_row, name))

to_delete = []
kept_pairs = []
for (date_, time_, mtype, topic), rows in groups.items():
    if len(rows) < 2:
        continue
    news = [r for r in rows if is_new_style(r[1])]
    olds = [r for r in rows if not is_new_style(r[1])]
    if news and olds:
        # 기존(YoY/MoM/QoQ)이 이미 있는데 신규(전년비/전월비/전분기비)가 또 들어온 경우 -> 신규를 삭제 대상으로
        for sheet_row, name in news:
            to_delete.append((sheet_row, date_, time_, name))
        kept_pairs.append((date_, time_, topic, olds, news))
    elif len(news) > 1 or len(olds) > 1:
        # 같은 스타일끼리 중복(예: 공백차이) - 정보용으로만 출력, 자동삭제 대상에서는 제외
        print(f"[참고, 자동삭제 제외] {date_} {time_} {mtype} {topic}: {rows}")

print(f"\n=== 삭제 대상(신규/전년비·전월비·전분기비 스타일) 총 {len(to_delete)}건 ===\n")
for sheet_row, date_, time_, name in sorted(to_delete):
    print(f"row{sheet_row}: {date_} {time_} | {name}")

print(f"\n=== 대응되는 유지 대상(기존 YoY/MoM/QoQ) 그룹 {len(kept_pairs)}개 ===\n")
for date_, time_, topic, olds, news in kept_pairs:
    print(f"[{date_} {time_}] topic={topic}")
    for r, n in olds:
        print(f"   유지 row{r}: {n}")
    for r, n in news:
        print(f"   삭제 row{r}: {n}")
