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
