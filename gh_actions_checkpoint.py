"""
GitHub Actions 전용: 하루 4회 체크포인트 중 하나를 실행하고 즉시 종료

핵심 설계 변경 (v2):
- GitHub Actions 무료 스케줄은 실행이 몇 시간씩 늦어질 수 있음 (알려진 한계)
- 그래서 "지금 시세"가 아니라 "목표 시각(예: 15:55) 당시까지의 누적 고가/저가"를
  Yahoo Finance 분봉 데이터로 역산해서 기록함 → 실행이 늦어져도 항상 정확한 값
- 같은 날 같은 체크포인트가 중복 기록되지 않도록, 쓰기 전에 시트에서
  오늘 날짜 + 해당 시간대 창(window)에 이미 기록이 있는지 확인 (서머/겨울 이중 cron 대비)

사용법:
  python gh_actions_checkpoint.py 아시아마감전
  python gh_actions_checkpoint.py 유럽개장전
  python gh_actions_checkpoint.py 미장전
  python gh_actions_checkpoint.py 미장후
  python gh_actions_checkpoint.py 미장전 --force   (시간/중복 체크 무시하고 강제 기록, 테스트용)
"""

import re
import sys
import requests
import pytz
from datetime import datetime, timedelta

from yahoo_data import SYMBOLS
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME, SYMBOL_ORDER, calc_ticks

KST = pytz.timezone("Asia/Seoul")

SUMMER_SCHEDULE = {
    "아시아마감전": "15:20",
    "유럽개장전":   "15:55",
    "미장전":       "22:25",
    "미장후":       "05:05",
}
WINTER_SCHEDULE = {
    "아시아마감전": "15:20",
    "유럽개장전":   "15:55",
    "미장전":       "23:25",
    "미장후":       "06:05",
}

# 중복 체크용 시간대 창 (앞뒤 30분) - 서머/겨울 이중 cron이 같은 날 둘 다 기록 시도하는 것 방지
CHECK_WINDOW_MINUTES = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def is_summer_time():
    eastern = pytz.timezone("America/New_York")
    return bool(datetime.now(eastern).dst())


def get_target_dt(timing: str, now_kst: datetime):
    sched = SUMMER_SCHEDULE if is_summer_time() else WINTER_SCHEDULE
    target_str = sched[timing]
    th, tm = map(int, target_str.split(":"))
    return now_kst.replace(hour=th, minute=tm, second=0, microsecond=0)


def get_intraday_high_low(symbol: str, target_dt_kst: datetime, multiplier=None):
    """당일 00:00 KST 부터 target_dt_kst 까지의 분봉 누적 고가/저가 (실행 시각과 무관하게 정확한 값)"""
    day_start_kst = target_dt_kst.replace(hour=0, minute=0, second=0, microsecond=0)
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


def _parse_korean_time_to_minutes(s: str):
    m = re.match(r"(오전|오후)\s*(\d+):(\d+)", s)
    if not m:
        return None
    ampm, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and h != 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    return h * 60 + mi


def already_recorded(ws, date_str: str, target_dt: datetime) -> bool:
    """오늘 날짜 + 이 시간대 창에 이미 기록(비고 없는 정규 체크포인트)이 있는지 확인"""
    target_minutes = target_dt.hour * 60 + target_dt.minute
    try:
        all_values = ws.get_all_values()
        for row in all_values[2:]:
            if len(row) < 11:
                continue
            if row[1].strip() != date_str:
                continue
            if row[10].strip():  # 비고 있으면 경제발표용이라 제외
                continue
            m = _parse_korean_time_to_minutes(row[2].strip())
            if m is None:
                continue
            diff = min(abs(m - target_minutes), 1440 - abs(m - target_minutes))
            if diff <= CHECK_WINDOW_MINUTES:
                return True
    except Exception as e:
        print(f"   ⚠️ 중복 확인 오류(무시하고 계속): {e}")
    return False


def main():
    if len(sys.argv) < 2:
        print("사용법: python gh_actions_checkpoint.py [아시아마감전|유럽개장전|미장전|미장후] [--force]")
        sys.exit(1)

    timing = sys.argv[1]
    force = "--force" in sys.argv
    now = datetime.now(KST)

    if not force:
        if timing == "미장후":
            if now.weekday() == 6:
                print(f"⏭️ [{timing}] 일요일이라 스킵")
                return
        else:
            if now.weekday() >= 5:
                print(f"⏭️ [{timing}] 주말이라 스킵")
                return

    target_dt = get_target_dt(timing, now)

    if not force and now < target_dt:
        print(f"⏭️ [{timing}] 아직 목표 시각({target_dt.strftime('%H:%M')})이 안 됨 - 스킵")
        return

    # 시트 기록용 날짜 (미장후는 -1일)
    record_date = target_dt - timedelta(days=1) if timing == "미장후" else target_dt
    date_str = f"{record_date.year}. {record_date.month}. {record_date.day}"

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    if not force and already_recorded(ws, date_str, target_dt):
        print(f"⏭️ [{timing}] {date_str} 근처 이미 기록 있음 - 중복 스킵 (서머/겨울 이중 트리거 대응)")
        return

    print(f"🚀 [{timing}] 목표 시각 {target_dt.strftime('%Y-%m-%d %H:%M')} KST 기준 데이터 역산 중...")

    ampm = "오전" if target_dt.hour < 12 else "오후"
    h12 = target_dt.hour if target_dt.hour in (0, 12) else target_dt.hour % 12
    if h12 == 0:
        h12 = 12
    time_str = f"{ampm} {h12}:{target_dt.strftime('%M')}:00"

    row = [date_str, time_str]
    any_data = False
    for name in SYMBOL_ORDER:
        symbol, multiplier = SYMBOLS.get(name, (None, None))
        hl = get_intraday_high_low(symbol, target_dt, multiplier) if symbol else None
        if hl:
            ticks = calc_ticks(name, hl["high"], hl["low"])
            row.append(ticks)
            print(f"   ✅ [{name}] 고:{hl['high']} 저:{hl['low']} → {ticks}틱")
            any_data = True
        else:
            row.append("")
            print(f"   ⚠️ [{name}] 데이터 없음")
    row.append("")  # 비고

    if not any_data:
        print("   ❌ 전 종목 데이터 없음 - 기록 취소")
        return

    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"✅ 구글 시트 [진폭] 기록 완료 [{timing}] {date_str} {time_str}")


if __name__ == "__main__":
    main()
