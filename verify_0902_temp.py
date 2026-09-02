"""
임시 진단 스크립트: 2026-09-02 천연가스(4연속 28)/엔화(3연속 38) 반복감지 알림이
실제 시장 데이터로도 정말 진폭이 멈춰있었는지 Yahoo Finance 원본 5분봉으로 역계산해서 재확인.
확인 후 삭제 예정.
"""
import requests
import pytz
from datetime import datetime

KST = pytz.timezone("Asia/Seoul")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def get_intraday_high_low(symbol, day_start_kst, target_dt_kst, multiplier=None):
    period1 = int(day_start_kst.astimezone(pytz.UTC).timestamp())
    period2 = int(target_dt_kst.astimezone(pytz.UTC).timestamp()) + 60
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"period1": period1, "period2": period2, "interval": "5m"}
    res = requests.get(url, headers=HEADERS, params=params, timeout=15)
    data = res.json()
    result = data.get("chart", {}).get("result")
    if not result:
        return None, None, None, None
    r = result[0]
    ts = r.get("timestamp", []) or []
    quote = r["indicators"]["quote"][0]
    highs = quote.get("high", []) or []
    lows = quote.get("low", []) or []
    valid = [(t, h, l) for t, h, l in zip(ts, highs, lows) if h is not None and l is not None]
    if not valid:
        return None, None, None, None
    max_h = max(v[1] for v in valid)
    min_l = min(v[2] for v in valid)
    high_time = next(datetime.fromtimestamp(v[0], KST) for v in valid if v[1] == max_h)
    low_time = next(datetime.fromtimestamp(v[0], KST) for v in valid if v[2] == min_l)
    last_candle_time = datetime.fromtimestamp(valid[-1][0], KST)
    if multiplier:
        max_h = max_h * multiplier
        min_l = min_l * multiplier
    return max_h, min_l, (high_time, low_time), last_candle_time


day_start = KST.localize(datetime(2026, 9, 2, 7, 0, 0))

print("=== 천연가스 (NGV26.NYM, tick=0.001) 4연속 28틱 역검증 ===")
ng_checkpoints = {
    "10:30": KST.localize(datetime(2026, 9, 2, 10, 30, 0)),
    "12:00": KST.localize(datetime(2026, 9, 2, 12, 0, 0)),
    "15:20": KST.localize(datetime(2026, 9, 2, 15, 20, 0)),
    "15:55": KST.localize(datetime(2026, 9, 2, 15, 55, 0)),
}
for label, target in ng_checkpoints.items():
    high, low, (ht, lt), last_c = get_intraday_high_low("NGV26.NYM", day_start, target)
    if high is None:
        print(f"{label}: 데이터 없음")
        continue
    diff = high - low
    ticks = round(diff / 0.001)
    print(f"{label} -> high={high:.4f}(@{ht.strftime('%H:%M')}) low={low:.4f}(@{lt.strftime('%H:%M')}) ticks={ticks} | 마지막캔들={last_c.strftime('%H:%M')}")

print("\n=== 엔화 (6JU26.CME, x1,000,000, tick=1.0) 3연속 38틱 역검증 ===")
jpy_checkpoints = {
    "15:20": KST.localize(datetime(2026, 9, 2, 15, 20, 0)),
    "15:55": KST.localize(datetime(2026, 9, 2, 15, 55, 0)),
    "22:25": KST.localize(datetime(2026, 9, 2, 22, 25, 0)),
}
for label, target in jpy_checkpoints.items():
    high, low, (ht, lt), last_c = get_intraday_high_low("6JU26.CME", day_start, target, multiplier=1000000)
    if high is None:
        print(f"{label}: 데이터 없음")
        continue
    diff = high - low
    ticks = round(diff / 1.0)
    print(f"{label} -> high={high:.2f}(@{ht.strftime('%H:%M')}) low={low:.2f}(@{lt.strftime('%H:%M')}) ticks={ticks} | 마지막캔들={last_c.strftime('%H:%M')}")
