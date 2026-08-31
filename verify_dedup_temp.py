"""
임시 검증 스크립트: '경제발표' 탭 전체에서 (날짜,시간,발표명) 기준 중복 행이 있는지 확인.
- 이번에 추가된 75건(8월 49건 + 9월 26건)이 기존 데이터와 진짜 중복 없이
  깨끗하게 들어갔는지 검증하기 위함.
"""
import re
from collections import defaultdict
from google_sheet import get_client, SPREADSHEET_ID

def _normalize_date(date_str):
    s = str(date_str).strip().split(" ")[0]
    s = re.sub(r"[.\-]", "/", s)
    parts = [p for p in s.split("/") if p.strip() != ""]
    if len(parts) == 3:
        try:
            y, m, d = (int(p) for p in parts)
            return f"{y:04d}/{m:02d}/{d:02d}"
        except ValueError:
            pass
    return s

def _normalize_time(s):
    s = str(s).strip()
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return s

def _normalize_text(s):
    return " ".join(str(s).strip().split())

def make_key(date_str, time_str, name_str):
    return f"{_normalize_date(date_str)}_{_normalize_time(time_str)}_{_normalize_text(name_str)}"

client = get_client()
spreadsheet = client.open_by_key(SPREADSHEET_ID)
ws = spreadsheet.worksheet("경제발표")
all_values = ws.get_all_values()
print(f"전체 행 수 (헤더 포함): {len(all_values)}")

data_rows = all_values[2:]  # 헤더 2줄 건너뜀 (기존 코드 관례)
print(f"데이터 행 수: {len(data_rows)}")

key_to_rows = defaultdict(list)
for i, row in enumerate(data_rows):
    if len(row) >= 6:
        key = make_key(row[1], row[3], row[5])
        key_to_rows[key].append((i + 3, row[1], row[3], row[5]))  # 실제 시트 행번호(1-index)

dupes = {k: v for k, v in key_to_rows.items() if len(v) > 1}
print(f"\n=== 중복(같은 날짜+시간+발표명) 키 개수: {len(dupes)} ===")
for k, rows in list(dupes.items())[:30]:
    print(f"  KEY={k}")
    for r in rows:
        print(f"     row{r[0]}: 날짜={r[1]} 시간={r[2]} 발표명={r[3]}")

# 이번에 새로 추가된 8월/9월 데이터 범위 내 중복만 별도 집계
aug_sep_dupes = {k: v for k, v in dupes.items() if k.startswith("2026/08") or k.startswith("2026/09")}
print(f"\n=== 그 중 2026/08~09 범위 중복 키 개수: {len(aug_sep_dupes)} ===")

# 8/3~9/18 범위 데이터 개수 (이번에 추가 대상이었던 날짜 범위)
in_range = [k for k in key_to_rows if k.startswith("2026/08") or k.startswith("2026/09")]
print(f"\n2026/08~09 날짜의 전체 고유 키 개수: {len(in_range)}")
