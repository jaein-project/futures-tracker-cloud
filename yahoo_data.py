import requests

from contract_roll import build_symbols
from alerts import alert_symbol_missing

# ─────────────────────────────────────────────────────────────
# 기존에는 여기에 SYMBOLS = {"나스닥": ("MNQU26.CME", None), ...} 형태로
# 월물 코드를 직접 손으로 적어뒀지만, 이제 contract_roll.py가 오늘 날짜
# 기준으로 자동 계산합니다. 월물 롤오버 규칙과 주의사항은 contract_roll.py
# 상단 주석을 꼭 한 번 읽어주세요.
# ─────────────────────────────────────────────────────────────
SYMBOLS = build_symbols()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_quote(symbol, multiplier=None):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        meta = data["chart"]["result"][0]["meta"]
        high = meta.get("regularMarketDayHigh")
        low = meta.get("regularMarketDayLow")
        open_ = meta.get("regularMarketOpen")
        close = meta.get("regularMarketPrice")
        if multiplier:
            high = round(float(high) * multiplier, 1) if high else None
            low = round(float(low) * multiplier, 1) if low else None
            open_ = round(float(open_) * multiplier, 1) if open_ else None
            close = round(float(close) * multiplier, 1) if close else None
        else:
            high = round(float(high), 5) if high else None
            low = round(float(low), 5) if low else None
            open_ = round(float(open_), 5) if open_ else None
            close = round(float(close), 5) if close else None
        return {"open": open_, "high": high, "low": low, "close": close}
    except Exception as e:
        print(f" ❌ API 오류: {e}")
        return None

def get_all_symbols():
    results = {}
    for name, (symbol, multiplier) in SYMBOLS.items():
        result = get_quote(symbol, multiplier)
        if result and result["high"] and result["low"]:
            high = result["high"]
            low = result["low"]
            amplitude = round((high - low) / low * 100, 3) if low else None
            print(f" ✅ [{name}] 고:{high} / 저:{low} / 진폭:{amplitude}%")
            results[name] = {
                "symbol": symbol, "open": result["open"],
                "high": high, "low": low,
                "close": result["close"], "amplitude": amplitude,
            }
        else:
            print(f" ⚠️ [{name}] 데이터 없음 (symbol={symbol})")
            # 조용히 넘어가지 않고 Slack으로 알려줍니다 - 월물 코드가
            # 잘못됐거나 만기 지났을 가능성이 큰 신호입니다.
            alert_symbol_missing(name, symbol)
            results[name] = {"symbol": symbol, "high": None, "low": None, "amplitude": None}
    return results
