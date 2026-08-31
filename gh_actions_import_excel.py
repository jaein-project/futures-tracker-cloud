"""
GitHub Actions 전용: incoming/ 폴더에 올라온 경제발표 파일을 자동으로 경제발표 시트에 반영
- 로컬 import_excel.py의 auto_import_all()과 동일한 로직 (정규화된 중복 체크 포함)
- 파일 이동(incoming → imported)은 워크플로 yml에서 git mv로 처리
- 2026-08-31 추가: 하나HTS(.xlsx, 진짜 엑셀 바이너리)와 영웅문(.xls, 실제로는 HTML 문서)
  두 포맷을 모두 자동 판별해서 처리함 (하나HTS 로그인 화면 문제로 영웅문으로 소스 전환)

사용법:
  python gh_actions_import_excel.py <파일경로>
"""

import sys
import re
import time
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

from google_sheet import get_client

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
SHEET_NAME = "경제발표"
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

QUARTER_TO_MONTH = {"1분기": "3월", "2분기": "6월", "3분기": "9월", "4분기": "12월"}


# ── 하나HTS 포맷 (진짜 xlsx, 컬럼: 날짜(YYYY/MM/DD)/시간/국가/표시/발표월(날짜형)) ──

def _get_month_prefix_hana(publish_month_str: str) -> str:
    """발표월이 날짜 문자열(예: '2026-07-01')로 오는 경우 월을 추출"""
    try:
        if publish_month_str and len(publish_month_str) >= 7:
            month = int(publish_month_str[5:7])
            return f"{month}월 "
    except Exception:
        pass
    return ""


def _parse_row_hana(row):
    try:
        date_str = str(row.get("날짜", "")).strip()
        time_str = str(row.get("시간", "")).strip()
        name = str(row.get("표시", "")).strip()
        pub_month = str(row.get("발표월", "")).strip()
        if not date_str or not name:
            return None
        date_obj = datetime.strptime(date_str[:10], "%Y/%m/%d")
        date_formatted = date_obj.strftime("%Y/%m/%d")
        weekday_ko = WEEKDAYS_KO[date_obj.weekday()]
        time_formatted = time_str[:5] if time_str and len(time_str) >= 5 else "00:00"
        month_prefix = _get_month_prefix_hana(pub_month)
        full_name = f"{month_prefix}{name}"
        return {"date": date_formatted, "weekday": weekday_ko, "time": time_formatted,
                "country": "미국", "name": full_name}
    except Exception:
        return None


def _load_events_hana(filepath):
    """하나HTS 포맷: 진짜 엑셀 바이너리. 실패하면 예외를 던져서 영웅문 포맷 시도로 넘어감."""
    df = pd.read_excel(filepath, dtype=str).fillna("")
    print(f"   (하나HTS 형식으로 인식) 총 {len(df)}행 로드")
    df_us = df[df["국가"].str.contains("미국", na=False)]
    print(f"   미국 필터 후: {len(df_us)}행")
    events = []
    for _, row in df_us.iterrows():
        parsed = _parse_row_hana(row)
        if parsed:
            events.append(parsed)
    return events


# ── 영웅문(키움 HTS) 포맷 (HTML 문서를 .xls로 저장, 컬럼: 날짜(MM-DD)/시간/통화/국가아이콘/
#    국가명/발표월(텍스트, 예: '7월'·'2분기')/지표/중요도(별)/방향아이콘/실제치/예상치/이전치) ──

def _normalize_month_prefix_kiwoom(pub_month: str) -> str:
    pub_month = (pub_month or "").strip()
    if not pub_month:
        return ""
    pub_month = QUARTER_TO_MONTH.get(pub_month, pub_month)
    return f"{pub_month} "


def _infer_year_kiwoom(month: int) -> int:
    """영웅문 포맷은 날짜에 연도가 없음(예: '08-03') → 오늘 날짜 기준으로 연도를 추정.
    다운로드 시점(오늘) 대비 6개월 이상 차이나면 연도 경계를 넘은 것으로 보정."""
    today = datetime.now()
    year = today.year
    if month < today.month - 6:
        year += 1
    elif month > today.month + 6:
        year -= 1
    return year


def _parse_row_kiwoom(date_str, time_str, pub_month, name):
    try:
        if not date_str or not name:
            return None
        m = re.match(r"(\d{1,2})-(\d{1,2})", date_str.strip())
        if not m:
            return None
        month, day = int(m.group(1)), int(m.group(2))
        year = _infer_year_kiwoom(month)
        date_obj = datetime(year, month, day)
        date_formatted = date_obj.strftime("%Y/%m/%d")
        weekday_ko = WEEKDAYS_KO[date_obj.weekday()]
        time_str = (time_str or "").strip()
        time_formatted = time_str[:5] if len(time_str) >= 5 else "00:00"
        month_prefix = _normalize_month_prefix_kiwoom(pub_month)
        full_name = f"{month_prefix}{name.strip()}"
        return {"date": date_formatted, "weekday": weekday_ko, "time": time_formatted,
                "country": "미국", "name": full_name}
    except Exception:
        return None


def _load_events_kiwoom(filepath):
    """영웅문 포맷: 실제로는 HTML 문서. <table>을 직접 파싱함.
    컬럼 순서(고정): 0=날짜 1=시간 2=통화 3=국가아이콘 4=국가명 5=발표월 6=지표
                     7=중요도(별) 8=방향아이콘 9=실제치 10=예상치 11=이전치"""
    with open(filepath, encoding="utf-8-sig", errors="ignore") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    table = soup.find("table")
    if table is None or table.find("tbody") is None:
        raise ValueError("표(<table>)를 찾을 수 없음 - 지원하지 않는 파일 형식")

    rows = table.find("tbody").find_all("tr")
    print(f"   (영웅문 형식으로 인식) 총 {len(rows)}행 로드")

    events = []
    us_count = 0
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 12:
            continue
        date_str = tds[0].get_text(strip=True)
        time_str = tds[1].get_text(strip=True)
        country = tds[4].get_text(strip=True)  # 국기 아이콘(tds[3]) 다음 칸에 실제 국가명
        pub_month = tds[5].get_text(strip=True)
        name = tds[6].get_text(strip=True)
        if country != "미국":
            continue
        us_count += 1
        parsed = _parse_row_kiwoom(date_str, time_str, pub_month, name)
        if parsed:
            events.append(parsed)
    print(f"   미국 필터 후: {us_count}행")
    return events


def load_events(filepath):
    """하나HTS(xlsx)로 먼저 시도하고, 실패하면 영웅문(HTML .xls)로 재시도"""
    try:
        return _load_events_hana(filepath)
    except Exception as e:
        print(f"   ℹ️ 하나HTS(xlsx) 형식 아님 ({type(e).__name__}: {e}) → 영웅문 형식으로 재시도")
        return _load_events_kiwoom(filepath)


# ── 공통: 중복 체크 & 시트 기록 ──

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
        print("사용법: python gh_actions_import_excel.py <파일경로>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"📂 파일 읽는 중: {filepath}")
    events = load_events(filepath)
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
