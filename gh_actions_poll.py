"""
GitHub Actions 전용 통합 스크립트 - 5분마다 실행
- 하루 4회 체크포인트(아시아마감전/유럽개장전/미장전/미장후) + 경제발표 전/후 5분을
  전부 이 한 스크립트가 5분마다 확인해서, "목표 시각이 지났는데 아직 기록 안 된 것"이 있으면
  그때그때 Yahoo Finance 분봉 데이터로 정확한 값을 역산해서 채움

기존에 특정 cron 시각(예: 22:25)에만 의존하던 방식은 GitHub 무료 스케줄의 지연/누락에 취약해서,
5분마다 계속 폴링하며 "놓친 게 있으면 바로 잡는" 방식으로 통합함.
"""

import re
import requests
import pytz
from datetime import datetime, timedelta

from yahoo_data import SYMBOLS
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME, SYMBOL_ORDER, calc_ticks, is_duplicate
from economic_calendar import get_today_event_groups

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

CHECK_WINDOW_MINUTES = 30  # 정기 체크포인트 중복 확인용 시간 창

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def is_summer_time():
    eastern = pytz.timezone("America/New_York")
    return bool(datetime.now(eastern).dst())


def get_intraday_high_low(symbol: str, day_start_kst: datetime, target_dt_kst: datetime, multiplier=None):
    """day_start_kst 부터 target_dt_kst 까지의 분봉 누적 고가/저가"""
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


def build_row(day_start: datetime, target_dt: datetime, date_str: str, time_str: str, note: str):
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
    return row, any_data


def time_str_from(target_dt: datetime) -> str:
    ampm = "오전" if target_dt.hour < 12 else "오후"
    h12 = target_dt.hour if target_dt.hour in (0, 12) else target_dt.hour % 12
    if h12 == 0:
        h12 = 12
    return f"{ampm} {h12}:{target_dt.strftime('%M')}:00"


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


def checkpoint_already_recorded(ws, date_str: str, target_dt: datetime) -> bool:
    """오늘 날짜 + 이 시간대 창에 이미 기록(비고 없는 정규 체크포인트)이 있는지 확인"""
    target_minutes = target_dt.hour * 60 + target_dt.minute
    try:
        all_values = ws.get_all_values()
        for row in all_values[2:]:
            if len(row) < 11:
                continue
            if row[1].strip() != date_str:
                continue
            if row[10].strip():
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


def process_checkpoints(ws, now: datetime):
    sched = SUMMER_SCHEDULE if is_summer_time() else WINTER_SCHEDULE

    for timing, hhmm in sched.items():
        th, tm = map(int, hhmm.split(":"))
        target_dt = now.replace(hour=th, minute=tm, second=0, microsecond=0)

        # 자정을 넘겨 지연 처리되는 경우 보정
        if target_dt > now:
            target_dt -= timedelta(days=1)

        if timing == "미장후":
            if target_dt.weekday() == 6:
                continue
        else:
            if target_dt.weekday() >= 5:
                continue

        if now < target_dt:
            continue  # 아직 목표 시각 안 됨

        record_date = target_dt - timedelta(days=1) if timing == "미장후" else target_dt
        date_str = f"{record_date.year}. {record_date.month}. {record_date.day}"
        day_start = record_date.replace(hour=0, minute=0, second=0, microsecond=0)

        if checkpoint_already_recorded(ws, date_str, target_dt):
            continue

        print(f"🚀 [{timing}] {day_start.strftime('%Y-%m-%d %H:%M')} ~ {target_dt.strftime('%Y-%m-%d %H:%M')} KST 누적 데이터 역산 중...")
        time_str = time_str_from(target_dt)
        row, any_data = build_row(day_start, target_dt, date_str, time_str, "")
        if any_data:
            ws.append_row(row, value_input_option="USER_ENTERED")
            print(f"✅ [{timing}] 기록 완료: {date_str} {time_str}")
        else:
            print(f"   ❌ [{timing}] 전 종목 데이터 없음 - 기록 취소")


def process_economic(ws, now: datetime):
    groups = get_today_event_groups()
    if not groups:
        return

    for g in groups:
        d = datetime.strptime(g["date"], "%Y/%m/%d")
        date_str = f"{d.year}. {d.month}. {d.day}"

        for target_dt, note in [(g["before_dt"], g["label_pre"]), (g["after_dt"], g["label_post"])]:
            if now < target_dt:
                continue
            if is_duplicate(ws, date_str, note):
                continue
            print(f"📌 경제발표 기록 시도: {date_str} {note}")
            day_start = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            time_str = time_str_from(target_dt)
            row, any_data = build_row(day_start, target_dt, date_str, time_str, note)
            if any_data:
                ws.append_row(row, value_input_option="USER_ENTERED")
                print(f"✅ 경제발표 기록 완료: {date_str} {note}")
            else:
                print(f"   ❌ 경제발표 전 종목 데이터 없음 - 기록 취소")


def main():
    now = datetime.now(KST)
    print(f"🔍 폴링 체크 - {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    process_checkpoints(ws, now)
    process_economic(ws, now)


if __name__ == "__main__":
    main()
