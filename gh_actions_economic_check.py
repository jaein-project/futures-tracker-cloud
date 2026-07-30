"""
GitHub Actions 전용: 5분마다 실행되어, 오늘 중요 경제발표의 전/후 5분 시점 값을 기록

핵심 설계 (체크포인트 스크립트와 동일한 원리):
- GitHub Actions 5분 주기 cron도 실제로는 몇 분~몇십 분 늦게 실행될 수 있음
- 그래서 "지금이 딱 그 순간인지"가 아니라, "목표 시각(전/후)이 이미 지났고 아직 기록 안 됐으면"
  Yahoo Finance 분봉 데이터로 그 목표 시각 당시 값을 역산해서 기록 → 실행이 늦어져도 항상 정확
- 중복 기록 방지는 google_sheet.is_duplicate (날짜+비고 매칭)로 처리

사용법:
  python gh_actions_economic_check.py
"""

import requests
import pytz
from datetime import datetime

from economic_calendar import get_today_event_groups
from yahoo_data import SYMBOLS
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME, SYMBOL_ORDER, calc_ticks, is_duplicate

KST = pytz.timezone("Asia/Seoul")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def get_intraday_high_low(symbol: str, day_start_kst: datetime, target_dt_kst: datetime, multiplier=None):
    """day_start_kst 부터 target_dt_kst 까지의 분봉 누적 고가/저가 (실행 지연과 무관하게 정확)"""
    period1 = int(day_start_kst.astimezone(pytz.UTC).timestamp())
    period2 = int(target_dt_kst.astimezone(pytz.UTC).timestamp()) + 60

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"period1": period1, "period2": period2, "interval": "5m"}
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = res.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        highs, lows = [], []
        for i, ts in enumerate(timestamps):
            if ts is None:
                continue
            bar_time_kst = datetime.fromtimestamp(ts, tz=pytz.UTC).astimezone(KST)
            if day_start_kst <= bar_time_kst <= target_dt_kst:
                h, l = quote["high"][i], quote["low"][i]
                if h is not None:
                    highs.append(h)
                if l is not None:
                    lows.append(l)

        if not highs or not lows:
            return None
        high, low = max(highs), min(lows)
        if multiplier:
            high = round(float(high) * multiplier, 1)
            low  = round(float(low)  * multiplier, 1)
        else:
            high = round(float(high), 5)
            low  = round(float(low),  5)
        return {"high": high, "low": low}
    except Exception as e:
        print(f"   ❌ [{symbol}] 조회 오류: {e}")
        return None


def record_at(ws, target_dt: datetime, date_str: str, note: str):
    if is_duplicate(ws, date_str, note):
        print(f"   ⏭️ 이미 기록됨 - 스킵: {date_str} {note}")
        return

    day_start = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    ampm = "오전" if target_dt.hour < 12 else "오후"
    h12 = target_dt.hour if target_dt.hour in (0, 12) else target_dt.hour % 12
    if h12 == 0:
        h12 = 12
    time_str = f"{ampm} {h12}:{target_dt.strftime('%M')}:00"

    row = [date_str, time_str]
    any_data = False
    for name in SYMBOL_ORDER:
        symbol, multiplier = SYMBOLS.get(name, (None, None))
        hl = get_intraday_high_low(symbol, day_start, target_dt, multiplier) if symbol else None
        if hl:
            ticks = calc_ticks(name, hl["high"], hl["low"])
            row.append(ticks)
            print(f"   ✅ [{name}] 고:{hl['high']} 저:{hl['low']} → {ticks}틱")
            any_data = True
        else:
            row.append("")
            print(f"   ⚠️ [{name}] 데이터 없음")
    row.append(note)

    if not any_data:
        print("   ❌ 전 종목 데이터 없음 - 기록 취소")
        return

    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"✅ 구글 시트 [진폭] 기록 완료 {date_str} {time_str} {note}")


def main():
    now = datetime.now(KST)
    print(f"🔍 경제발표 체크 - {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    groups = get_today_event_groups()
    if not groups:
        print("   ℹ️ 오늘 대상 없음")
        return

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    did_something = False
    for g in groups:
        d = datetime.strptime(g["date"], "%Y/%m/%d")
        date_str = f"{d.year}. {d.month}. {d.day}"

        if now >= g["before_dt"]:
            did_something = True
            print(f"📌 [전] 확인: {g['label_pre']}")
            record_at(ws, g["before_dt"], date_str, g["label_pre"])

        if now >= g["after_dt"]:
            did_something = True
            print(f"📌 [후] 확인: {g['label_post']}")
            record_at(ws, g["after_dt"], date_str, g["label_post"])

    if not did_something:
        print("   ℹ️ 아직 발표 전/후 5분 타이밍이 안 됨")


if __name__ == "__main__":
    main()
