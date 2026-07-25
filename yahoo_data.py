import requests

SYMBOLS = {
    "나스닥":   ("MNQU26.CME", None),
    "오일":     ("CLU26.NYM",  None),
    "골드":     ("GCQ26.CMX",  None),
    "천연가스": ("NGU26.NYM",  None),
    "구리":     ("HGU26.CMX",  None),
    "유로":     ("6EU26.CME",  None),   # ⚠️ 확인 필요: 알려주신 코드가 천연가스와 동일(NGU26)해서 일단 기존 코드 유지
    "엔화":     ("6JU26.CME",  1000000),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def get_quote(symbol, multiplier=None):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json()
        meta = data["chart"]["result"][0]["meta"]
        high  = meta.get("regularMarketDayHigh")
        low   = meta.get("regularMarketDayLow")
        open_ = meta.get("regularMarketOpen")
        close = meta.get("regularMarketPrice")
        if multiplier:
            high  = round(float(high)  * multiplier, 1) if high  else None
            low   = round(float(low)   * multiplier, 1) if low   else None
            open_ = round(float(open_) * multiplier, 1) if open_ else None
            close = round(float(close) * multiplier, 1) if close else None
        else:
            high  = round(float(high),  5) if high  else None
            low   = round(float(low),   5) if low   else None
            open_ = round(float(open_), 5) if open_ else None
            close = round(float(close), 5) if close else None
        return {"open": open_, "high": high, "low": low, "close": close}
    except Exception as e:
        print(f"   ❌ API 오류: {e}")
        return None

def get_all_symbols():
    results = {}
    for name, (symbol, multiplier) in SYMBOLS.items():
        result = get_quote(symbol, multiplier)
        if result and result["high"] and result["low"]:
            high = result["high"]
            low  = result["low"]
            amplitude = round((high - low) / low * 100, 3) if low else None
            print(f"   ✅ [{name}] 고:{high} / 저:{low} / 진폭:{amplitude}%")
            results[name] = {
                "symbol": symbol, "open": result["open"],
                "high": high, "low": low,
                "close": result["close"], "amplitude": amplitude,
            }
        else:
            print(f"   ⚠️ [{name}] 데이터 없음")
            results[name] = {"symbol": symbol, "high": None, "low": None, "amplitude": None}
    return results
