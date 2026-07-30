"""
Yahoo Finance 과거 일봉(고가/저가)으로 놓친 날짜의 진폭을 백필하는 스크립트
- 네트워크 오류 등으로 특정 체크포인트 기록을 놓쳤을 때 사용
- 오늘 시세가 아니라 '지정한 날짜'의 실제 고가/저가를 Yahoo Finance 과거 데이터에서 가져옴

사용법:
  python backfill_amplitude.py 2026-07-16 미장후
  python backfill_amplitude.py 2026-07-16 미장후 --dry-run   (시트에 안 쓰고 결과만 미리보기)

날짜는 '실제 미국 거래일 기준'으로 입력하세요.
  예: 한국시간 07/17 05:05에 놓친 미장후 기록 → 미국 기준으로는 07/16 이므로 "2026-07-16" 입력
"""

import sys
import requests
from datetime import datetime, timedelta
import pytz

from yahoo_data import SYMBOLS
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME, SYMBOL_ORDER, calc_ticks

KST = pytz.timezone("Asia/Seoul")
US_EASTERN = pytz.timezone("America/New_York")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def get_intraday_high_low(symbol: str, day_start_kst: datetime, target_dt_kst: datetime, multiplier=None):
    """day_start_kst 부터 target_dt_kst 까지의 분봉 누적 고가/저가 계산
    (그 시각에 실시간으로 조회했으면 나왔을 값과 동일한 방식)
    """
    # 'range' 문자열 대신 정확한 시작~끝 시각(Unix timestamp)을 직접 지정
    period1 = int(day_start_kst.astimezone(pytz.UTC).timestamp())
    period2 = int(target_dt_kst.astimezone(pytz.UTC).timestamp()) + 60  # 끝 시각 포함되도록 살짝 여유

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


def get_historical_high_low(symbol: str, target_date: str, multiplier=None):
    """Yahoo Finance 과거 일봉에서 target_date(YYYY-MM-DD, 미국 거래일 기준) 고가/저가 조회"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "10d", "interval": "1d"}
    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = res.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        target = datetime.strptime(target_date, "%Y-%m-%d").date()

        for i, ts in enumerate(timestamps):
            # 봉의 타임스탬프를 미국 동부시간 날짜로 변환해서 비교
            bar_date = datetime.fromtimestamp(ts, tz=pytz.UTC).astimezone(US_EASTERN).date()
            if bar_date == target:
                high = quote["high"][i]
                low  = quote["low"][i]
                if high is None or low is None:
                    return None
                if multiplier:
                    high = round(float(high) * multiplier, 1)
                    low  = round(float(low)  * multiplier, 1)
                else:
                    high = round(float(high), 5)
                    low  = round(float(low),  5)
                return {"high": high, "low": low}
        return None
    except Exception as e:
        print(f"   ❌ [{symbol}] 조회 오류: {e}")
        return None


def backfill_intraday(date_str: str, time_str: str, timing: str, dry_run: bool = False):
    """특정 날짜의 특정 시각(KST)까지 누적 고가/저가로 백필 (예: 15:20, 15:55, 22:25 체크포인트용)
    date_str/time_str 은 '실제 조회해야 할 KST 날짜/시각'을 넣어주세요.
    미장후는 자동으로 시트 라벨 날짜를 -1일 처리합니다 (전날 하루 전체 세션 반영).
    """
    target_dt = KST.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))

    # 시트 라벨 날짜 (미장후는 -1일) - 이 날짜의 자정부터 누적해야 하루 전체 세션이 반영됨
    label_date = target_dt - timedelta(days=1) if timing == "미장후" else target_dt
    day_start = label_date.replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"\n📌 백필 대상: {day_start.strftime('%Y-%m-%d %H:%M')} ~ {target_dt.strftime('%Y-%m-%d %H:%M')} KST 누적 ({timing})")
    print("=" * 50)

    results = {}
    for name, (symbol, multiplier) in SYMBOLS.items():
        hl = get_intraday_high_low(symbol, day_start, target_dt, multiplier)
        if hl:
            amplitude = round((hl["high"] - hl["low"]) / hl["low"] * 100, 3) if hl["low"] else None
            ticks = calc_ticks(name, hl["high"], hl["low"])
            print(f"   ✅ [{name}] 고:{hl['high']} 저:{hl['low']} 진폭:{amplitude}% → {ticks}틱")
            results[name] = {"high": hl["high"], "low": hl["low"], "ticks": ticks}
        else:
            print(f"   ⚠️ [{name}] 해당 시간대 데이터 없음")
            results[name] = None

    if dry_run:
        print("\n(dry-run) 시트에 기록하지 않았습니다.")
        return

    date_out = f"{label_date.year}. {label_date.month}. {label_date.day}"
    note = f"수동백필_{timing}"

    row = [date_out, "백필(수동)"]
    for name in SYMBOL_ORDER:
        r = results.get(name)
        row.append(r["ticks"] if r else "")
    row.append(note)

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)
    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"\n✅ 구글 시트에 백필 완료: {date_out} [{timing}] (비고: {note})")
    print(f"   👉 https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


def backfill(target_date: str, timing: str, dry_run: bool = False):
    print(f"\n📌 백필 대상: {target_date} ({timing})")
    print("=" * 50)

    results = {}
    for name, (symbol, multiplier) in SYMBOLS.items():
        hl = get_historical_high_low(symbol, target_date, multiplier)
        if hl:
            amplitude = round((hl["high"] - hl["low"]) / hl["low"] * 100, 3) if hl["low"] else None
            ticks = calc_ticks(name, hl["high"], hl["low"])
            print(f"   ✅ [{name}] 고:{hl['high']} 저:{hl['low']} 진폭:{amplitude}% → {ticks}틱")
            results[name] = {"high": hl["high"], "low": hl["low"], "ticks": ticks}
        else:
            print(f"   ⚠️ [{name}] 해당 날짜 데이터 없음")
            results[name] = None

    if dry_run:
        print("\n(dry-run) 시트에 기록하지 않았습니다.")
        return

    # 날짜 포맷을 진폭 시트 형식(YYYY. M. D)으로 변환
    d = datetime.strptime(target_date, "%Y-%m-%d")
    date_str = f"{d.year}. {d.month}. {d.day}"
    note = f"수동백필_{timing}"

    row = [date_str, "백필(수동)"]
    for name in SYMBOL_ORDER:
        r = results.get(name)
        row.append(r["ticks"] if r else "")
    row.append(note)

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)
    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"\n✅ 구글 시트에 백필 완료: {date_str} [{timing}] (비고: {note})")
    print(f"   👉 https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법 1) 하루 전체 마감 기준 (미장후 등):")
        print("   python backfill_amplitude.py YYYY-MM-DD 타이밍 [--dry-run]")
        print("   예: python backfill_amplitude.py 2026-07-16 미장후")
        print()
        print("사용법 2) 특정 시각까지 누적 (아시아마감전/유럽개장전/미장전/미장후):")
        print("   python backfill_amplitude.py YYYY-MM-DD HH:MM 타이밍 [--dry-run]")
        print("   예: python backfill_amplitude.py 2026-07-20 15:20 아시아마감전")
        print("   ※ 미장후는 '실제 조회할 날짜/시각'을 넣으면 시트 라벨은 자동으로 -1일 처리됩니다")
        print("     예: 7/27 라벨의 미장후 → python backfill_amplitude.py 2026-07-28 05:05 미장후")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]

    if len(args) == 3:
        # 특정 시각 누적 모드: 날짜 시각 타이밍
        date_arg, time_arg, timing_arg = args
        backfill_intraday(date_arg, time_arg, timing_arg, dry_run)
    elif len(args) == 2:
        # 하루 전체 마감 모드: 날짜 타이밍
        date_arg, timing_arg = args
        backfill(date_arg, timing_arg, dry_run)
    else:
        print("❌ 인자 개수가 맞지 않아요. 위 사용법을 참고해주세요.")
        sys.exit(1)
