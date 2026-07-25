"""
GitHub Actions 전용: incoming/ 폴더에 올라온 xlsx를 자동으로 경제발표 시트에 반영
- 로컬 import_excel.py의 auto_import_all()과 동일한 로직 (정규화된 중복 체크 포함)
- 파일 이동(incoming → imported)은 워크플로 yml에서 git mv로 처리

사용법:
  python gh_actions_import_excel.py <xlsx경로>
"""

import sys
import re
import time
import pandas as pd
from datetime import datetime

from google_sheet import get_client

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
SHEET_NAME = "경제발표"
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


def get_month_prefix(publish_month_str: str) -> str:
    try:
        if publish_month_str and len(publish_month_str) >= 7:
            month = int(publish_month_str[5:7])
            return f"{month}월 "
    except Exception:
        pass
    return ""


def parse_row(row):
    try:
        date_str  = str(row.get("날짜", "")).strip()
        time_str  = str(row.get("시간", "")).strip()
        name      = str(row.get("표시", "")).strip()
        pub_month = str(row.get("발표월", "")).strip()
        if not date_str or not name:
            return None
        date_obj = datetime.strptime(date_str[:10], "%Y/%m/%d")
        date_formatted = date_obj.strftime("%Y/%m/%d")
        weekday_ko = WEEKDAYS_KO[date_obj.weekday()]
        time_formatted = time_str[:5] if time_str and len(time_str) >= 5 else "00:00"
        month_prefix = get_month_prefix(pub_month)
        full_name = f"{month_prefix}{name}"
        return {"date": date_formatted, "weekday": weekday_ko, "time": time_formatted,
                "country": "미국", "name": full_name}
    except Exception:
        return None


def _normalize_date(date_str: str) -> str:
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


def _normalize_time(s: str) -> str:
    s = str(s).strip()
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return s


def _normalize_text(s: str) -> str:
    return " ".join(str(s).strip().split())


def _make_key(date_str, time_str, name_str) -> str:
    return f"{_normalize_date(date_str)}_{_normalize_time(time_str)}_{_normalize_text(name_str)}"


def main():
    if len(sys.argv) < 2:
        print("사용법: python gh_actions_import_excel.py <xlsx경로>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"📂 파일 읽는 중: {filepath}")
    df = pd.read_excel(filepath, dtype=str).fillna("")
    print(f"   총 {len(df)}행 로드 완료")

    df_us = df[df["국가"].str.contains("미국", na=False)]
    print(f"   미국 필터 후: {len(df_us)}행")

    events = []
    for _, row in df_us.iterrows():
        parsed = parse_row(row)
        if parsed:
            events.append(parsed)
    print(f"   파싱 완료: {len(events)}건")

    if not events:
        print("   ⚠️ 유효한 데이터 없음")
        return

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    existing = ws.get_all_values()
    existing_keys = set()
    for row in existing[2:]:
        if len(row) >= 6:
            existing_keys.add(_make_key(row[1], row[3], row[5]))

    new_rows = []
    for e in events:
        key = _make_key(e["date"], e["time"], e["name"])
        if key not in existing_keys:
            new_rows.append([e["date"], e["weekday"], e["time"], e["country"], e["name"], ""])
            existing_keys.add(key)

    if not new_rows:
        print("   ℹ️ 추가할 신규 데이터 없음 (모두 중복)")
        return

    batch_size = 20
    added = 0
    for i in range(0, len(new_rows), batch_size):
        batch = new_rows[i:i + batch_size]
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        added += len(batch)
        if i + batch_size < len(new_rows):
            time.sleep(2)

    print(f"   ✅ {added}건 추가됨 (기존 데이터 보존)")


if __name__ == "__main__":
    main()
