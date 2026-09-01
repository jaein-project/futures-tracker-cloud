"""
임시 진단 스크립트: 2026-09-01 구리(HGZ26.CMX) 진폭이 12:00/15:20/15:55 세 체크포인트에서
정말로 53틱으로 동일했는지, Yahoo Finance 원본 5분봉 데이터로 역계산해서 재확인.
재인님이 물어본 "구리 3연속 반복이 진짜 진폭 정체인지" 확인용. 확인 후 삭제 예정.
"""
import requests
import pytz
from datetime import datetime

KST = pytz.timezone("Asia/Seoul")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
SYMBOL = "HGZ26.CMX"


def get_intraday_high_low(symbol, day_start_kst, target_dt_kst):
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
    # 최고가/최저가가 실제로 찍힌 시각도 같이 확인 (몇 시에 마지막으로 갱신됐는지)
    high_time = next(datetime.fromtimestamp(v[0], KST) for v in valid if v[1] == max_h)
    low_time = next(datetime.fromtimestamp(v[0], KST) for v in valid if v[2] == min_l)
    last_candle_time = datetime.fromtimestamp(valid[-1][0], KST)
    return max_h, min_l, (high_time, low_time), last_candle_time


day_start = KST.localize(datetime(2026, 9, 1, 7, 0, 0))

checkpoints = {
    "아시아장중 12:00": KST.localize(datetime(2026, 9, 1, 12, 0, 0)),
    "아시아마감전 15:20": KST.localize(datetime(2026, 9, 1, 15, 20, 0)),
    "유럽개장전 15:55": KST.localize(datetime(2026, 9, 1, 15, 55, 0)),
}

print(f"day_start = {day_start}")
print(f"심볼 = {SYMBOL} (구리, tick=0.001)")
print()

for label, target in checkpoints.items():
    high, low, (high_t, low_t), last_candle = get_intraday_high_low(SYMBOL, day_start, target)
    if high is None:
        print(f"{label}: 데이터 없음")
        continue
    diff = high - low
    ticks = round(diff / 0.001)
    print(
        f"{label} (기준시각 {target.strftime('%H:%M')}) -> "
        f"high={high:.4f}(@{high_t.strftime('%H:%M')}) low={low:.4f}(@{low_t.strftime('%H:%M')}) "
        f"diff={diff:.4f} ticks={ticks} | 마지막 캔들={last_candle.strftime('%H:%M')}"
    )
