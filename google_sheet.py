"""
Google Sheets 자동 기록 모듈
- 기존 "진폭" 시트에 HTS 기준 틱 단위로 기록
- 네트워크 오류 시 3회 자동 재시도
- K열 비고 지원 (경제발표 기록용)
- 중복 방지 로직 포함
"""

import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from datetime import datetime, timedelta
import pytz
import os
import pickle
import time as _time

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
SHEET_NAME     = "진폭"
CLIENT_SECRET  = "client_secret.json"
TOKEN_FILE     = "token.pickle"
TIMEZONE       = "Asia/Seoul"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SYMBOL_ORDER = ["나스닥", "오일", "골드", "천연가스", "구리", "유로", "엔화"]

REMINDER_LOG_SHEET = "알림기록"  # 시트에 값을 기록하지 않는 순수 알림(예고/비교)의 중복 방지용 로그 탭
DAILY_SUMMARY_SHEET = "일일요약"  # 2026-09-01 추가: 미장마감 시 아시아장초반 대비 최종 비교(Slack만 가던 것)를
                                  # 영구 기록. Slack 알림은 3개월 지나면 삭제되므로, 같은 내용을 시트에도
                                  # 구조화된 형태(날짜별 시가/종가/변동)로 보관해서 나중에도 조회/분석 가능하게 함.

TICK_SIZE = {
    "나스닥":   1,
    "오일":     0.01,
    "골드":     0.1,
    "천연가스": 0.001,
    "구리":     0.001,
    "유로":     0.0001,
    "엔화":     1.0,
}


def get_client():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        print("🌐 브라우저에서 구글 로그인 창이 열립니다...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
        creds = flow.run_local_server(port=0)
        print("✅ 로그인 성공!")
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return gspread.authorize(creds)


def calc_ticks(name, high, low):
    if not high or not low:
        return ""
    diff = high - low
    tick = TICK_SIZE.get(name)
    if tick:
        return round(diff / tick)
    return ""


def is_duplicate(ws, date_str, note):
    """같은 날짜+비고가 이미 있는지 확인 (중복 방지)"""
    try:
        all_values = ws.get_all_values()
        for row in all_values[2:]:  # 헤더 건너뜀
            if len(row) >= 11:
                # B열=날짜(index 1), K열=비고(index 10)
                if row[1] == date_str and row[10] == note:
                    return True
    except:
        pass
    return False


def _get_or_create_reminder_log_ws(spreadsheet):
    """'알림기록' 탭이 없으면 새로 만들어서 반환 (순수 알림 중복 방지용 - 진폭 시트는 건드리지 않음)"""
    try:
        return spreadsheet.worksheet(REMINDER_LOG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=REMINDER_LOG_SHEET, rows=2000, cols=3)
        ws.append_row(["날짜", "라벨", "전송시각"], value_input_option="USER_ENTERED")
        return ws


def is_reminder_sent(spreadsheet, date_str, label) -> bool:
    """순수 알림(경제발표 예고/전후비교/일일다이제스트 등, 진폭 시트에 값을 안 남기는 알림)이
    이미 전송됐는지 확인 - '알림기록' 탭에서 조회"""
    try:
        ws = _get_or_create_reminder_log_ws(spreadsheet)
        all_values = ws.get_all_values()
        for row in all_values[1:]:
            if len(row) >= 2 and row[0] == date_str and row[1] == label:
                return True
    except Exception as e:
        print(f"   ⚠️ 알림기록 시트 확인 오류: {e}")
    return False


def mark_reminder_sent(spreadsheet, date_str, label):
    """순수 알림을 보냈다는 사실을 '알림기록' 탭에 남겨서 다음 폴링에서 중복 전송되지 않게 함"""
    try:
        ws = _get_or_create_reminder_log_ws(spreadsheet)
        now_str = datetime.now(pytz.timezone(TIMEZONE)).strftime("%H:%M:%S")
        ws.append_row([date_str, label, now_str], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 알림기록 시트 기록 오류: {e}")


def _get_or_create_daily_summary_ws(spreadsheet):
    """'일일요약' 탭이 없으면 새로 만들어서 반환 (2026-09-01 신설).
    종목별로 [시가(아시아장초반)/종가(미장후)/변동] 3열씩 묶어서 헤더를 구성함."""
    try:
        return spreadsheet.worksheet(DAILY_SUMMARY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=DAILY_SUMMARY_SHEET, rows=2000, cols=1 + len(SYMBOL_ORDER) * 3
        )
        header = ["날짜"]
        for name in SYMBOL_ORDER:
            header += [f"{name}_시가(아시아장초반)", f"{name}_종가(미장후)", f"{name}_변동"]
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws


def append_daily_summary(spreadsheet, date_str, first_row, last_row):
    """미장마감(미장후) 시점에 하루 첫 체크포인트(아시아장초반) 대비 최종 비교를 '일일요약' 탭에 영구
    기록 (2026-09-01부터 적용). Slack의 alert_daily_summary와 완전히 동일한 값을 사용하되, Slack은
    3개월 후 삭제되므로 여기에 종목별 시가/종가/변동을 구조화된 숫자로 남겨서 나중에도 조회·분석 가능하게 함.

    first_row: ws.get_all_values()로 읽은 아시아장초반의 원본 시트 행 (A열 빈칸 → 인덱스 1칸 밀림)
    last_row : build_row()로 만든 미장후의 로컬 행 (밀림 없음) - format_ticks_comparison과 동일한 인덱싱
    """
    try:
        ws = _get_or_create_daily_summary_ws(spreadsheet)
        row = [date_str]
        for idx, name in enumerate(SYMBOL_ORDER):
            first_val = first_row[3 + idx] if len(first_row) > 3 + idx else ""
            last_val = last_row[2 + idx] if len(last_row) > 2 + idx else ""
            try:
                diff = int(last_val) - int(first_val)
            except (ValueError, TypeError):
                diff = ""
            row += [first_val, last_val, diff]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 일일요약 시트 기록 오류: {e}")


def _record_data_inner(data: dict, timing: str, note: str = ""):
    kst = pytz.timezone(TIMEZONE)
    now = datetime.now(kst)

    if timing == "미장후":
        record_date = now - timedelta(days=1)
    else:
        record_date = now

    date_str = f"{record_date.year}. {record_date.month}. {record_date.day}"

    hour = now.hour
    minute = now.strftime("%M")
    second = now.strftime("%S")
    if hour < 12:
        ampm = "오전"
        h = hour if hour != 0 else 12
    else:
        ampm = "오후"
        h = hour - 12 if hour != 12 else 12
    time_str = f"{ampm} {h}:{minute}:{second}"

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    # 중복 방지 (경제발표 기록 시)
    if note and is_duplicate(ws, date_str, note):
        print(f"   ⏭️ 중복 기록 건너뜀: {date_str} {note}")
        return

    # B열부터 시작 (A열은 시트에서 비어있음 → append_row는 A열부터 채우므로 빈칸 제외)
    row = [date_str, time_str]
    for name in SYMBOL_ORDER:
        d = data.get(name, {})
        high = d.get("high")
        low  = d.get("low")
        ticks = calc_ticks(name, high, low)
        row.append(ticks)
        if ticks:
            print(f"   📌 [{name}] 고:{high} 저:{low} → {ticks}틱")

    # K열 비고
    row.append(note)

    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"✅ 구글 시트 [진폭] 기록 완료 [{timing}] {date_str} {time_str} {note}")
    print(f"   👉 https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


def record_data(data: dict, timing: str, retry: int = 3):
    """일반 기록 - 네트워크 오류 시 3회 자동 재시도"""
    for attempt in range(retry):
        try:
            return _record_data_inner(data, timing)
        except Exception as e:
            print(f"   ⚠️ 오류 발생 ({attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                print(f"   🔄 30초 후 재시도...")
                _time.sleep(30)
            else:
                print(f"   ❌ {retry}회 시도 후 실패.")


def record_data_with_note(data: dict, note: str, retry: int = 3):
    """경제발표 기록 - K열 비고 포함, 중복 방지"""
    for attempt in range(retry):
        try:
            return _record_data_inner(data, "경제발표", note=note)
        except Exception as e:
            print(f"   ⚠️ 오류 발생 ({attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                print(f"   🔄 30초 후 재시도...")
                _time.sleep(30)
            else:
                print(f"   ❌ {retry}회 시도 후 실패.")
