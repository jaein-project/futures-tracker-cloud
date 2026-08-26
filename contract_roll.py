"""
선물 계약월(월물) 자동 계산 모듈
================================

지금까지 yahoo_data.py의 SYMBOLS 딕셔너리에 "MNQU26.CME" 같은 계약월 코드를
사람이 손으로 직접 갱신해왔습니다. 이 모듈은 오늘 날짜를 기준으로
"지금 사용해야 할 계약월 코드"를 자동으로 계산해서 그 수작업을 없애줍니다.

월물 코드 (CME/NYMEX/COMEX 공통):
    F=1월 G=2월 H=3월 J=4월 K=5월 M=6월 N=7월 Q=8월 U=9월 V=10월 X=11월 Z=12월

⚠️ 중요 - 반드시 읽어주세요
----------------------------
아래 CONTRACT_RULES의 "만기 대략 기준일" / roll_days_before 값은 거래소 공식
캘린더를 실시간 조회한 게 아니라, 상품별로 널리 알려진 일반적인 규칙을
근사치로 코드화한 것입니다. 특히 원유/천연가스/구리/골드처럼 실물 상품 선물은
거래소·연도별 공휴일에 따라 만기일이 며칠씩 미세하게 달라질 수 있습니다.

그래서:
  1) 나스닥/유로/엔화(금융 선물, 분기월물)는 "계약월의 세 번째 금요일" 이라는
     잘 알려진 규칙을 그대로 코드화했습니다 - 이 부분은 신뢰도가 높습니다.
  2) 오일/천연가스/구리/골드(실물 상품, 매월물)는 "대략 이 시점에 다음 월물로
     넘어간다"는 보수적인 근사 규칙입니다. roll_days_before 날짜 규칙만으로는
     실제 거래소 상황과 며칠씩 어긋날 수 있어서(2026-08-26, 천연가스 NGU26→NGV26
     롤오버가 날짜 규칙보다 며칠 더 일찍 실제로 일어난 걸 하나HTS로 확인함),
     아래 3번 거래량 자동 비교로 실시간 보정합니다.
  3) 계산된 심볼로 Yahoo Finance에서 데이터를 못 가져오거나 비정상적으로 비어
     있으면 알림이 갑니다 (alerts.py 참고).
  4) **거래량 기반 자동 보정 (2026-08-26 추가)**: 위 1)/2)의 날짜 규칙으로 일단
     "이번 달 계약월"을 추정한 뒤, 실제로 Yahoo Finance에서 그 계약월과 바로
     다음 계약월의 당일 거래량(regularMarketVolume)을 비교합니다. 다음 월물의
     거래량이 이미 더 많다면 - 즉 실제 시장에서 이미 다음 월물로 활발히
     거래가 옮겨갔다면 - 날짜 규칙과 무관하게 다음 월물로 넘어간 것으로 보고
     자동으로 그 심볼을 씁니다 (`_volume_corrected_month`). 이러면 사람이
     HTS를 직접 보고 "월물이 언제 바뀌었는지" 알려줄 필요 없이 매 폴링마다
     스스로 확인/보정합니다. Yahoo 거래량 조회가 실패하면 안전하게 원래 날짜
     규칙 값을 그대로 씁니다.
"""

from datetime import date, timedelta
import requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# 상품별 실제 거래되는(유동성 있는) 계약월 사이클과 롤오버 기준
#   cycle             : 그 상품이 거래되는 달(1~12) 리스트
#   roll_days_before   : "만기 기준일"보다 며칠 전에 다음 월물로 넘어갈지 (여유있게 보수적으로)
#   expiry_rule        : "third_friday_same_month" (금융선물, 계약월 자체의 3주차 금요일)
#                         또는 "n_days_before_month_start" (실물상품 근사치 - 계약월
#                         시작 며칠 전부터 이미 그 월물이 활발히 거래된다고 가정)
CONTRACT_RULES = {
    "나스닥":   {"cycle": [3, 6, 9, 12],        "roll_days_before": 8,  "expiry_rule": "third_friday_same_month", "exchange": "CME"},
    "유로":     {"cycle": [3, 6, 9, 12],        "roll_days_before": 8,  "expiry_rule": "third_friday_same_month", "exchange": "CME"},
    "엔화":     {"cycle": [3, 6, 9, 12],        "roll_days_before": 8,  "expiry_rule": "third_friday_same_month", "exchange": "CME"},
    "오일":     {"cycle": list(range(1, 13)),   "roll_days_before": 25, "expiry_rule": "n_days_before_month_start", "exchange": "NYM"},
    "천연가스": {"cycle": list(range(1, 13)),   "roll_days_before": 4,  "expiry_rule": "n_days_before_month_start", "exchange": "NYM"},
    "구리":     {"cycle": [3, 5, 7, 9, 12],     "roll_days_before": 5,  "expiry_rule": "n_days_before_month_start", "exchange": "CMX"},
    "골드":     {"cycle": [2, 4, 6, 8, 12], "roll_days_before": 5,  "expiry_rule": "n_days_before_month_start", "exchange": "CMX"},
}

BASE_TICKER = {
    "나스닥": "MNQ",
    "유로": "6E",
    "엔화": "6J",
    "오일": "CL",
    "천연가스": "NG",
    "구리": "HG",
    "골드": "GC",
}

# 기존 yahoo_data.py의 multiplier 값 (엔화만 특수)
MULTIPLIER = {
    "엔화": 1000000,
}


def _third_friday(year: int, month: int) -> date:
    """해당 연/월의 세 번째 금요일 날짜"""
    d = date(year, month, 1)
    friday_count = 0
    while True:
        if d.weekday() == 4:  # Friday
            friday_count += 1
            if friday_count == 3:
                return d
        d += timedelta(days=1)


def _roll_trigger_date(year: int, month: int, rule: dict) -> date:
    """이 계약월 코드를 '그만 쓰고 다음 월물로 넘어가야 하는' 기준일"""
    if rule["expiry_rule"] == "third_friday_same_month":
        expiry = _third_friday(year, month)
    else:  # n_days_before_month_start: 계약월 시작일 자체를 만기 근사치로 사용
        expiry = date(year, month, 1)
    return expiry - timedelta(days=rule["roll_days_before"])


def current_contract_month(cycle: list, rule: dict, today: date = None):
    """오늘 날짜 기준으로 사용해야 할 계약월 (year, month) 반환"""
    if today is None:
        today = date.today()

    seen = set()
    candidates = []
    y = today.year - 1
    for _ in range(4):  # 작년 ~ 3년 뒤까지 넉넉히 후보 생성
        for m in cycle:
            key = (y, m)
            if key not in seen:
                seen.add(key)
                candidates.append(key)
        y += 1
    candidates.sort()

    for y, m in candidates:
        if today < _roll_trigger_date(y, m, rule):
            return y, m

    return candidates[-1]  # fallback


def _next_in_cycle(cycle: list, year: int, month: int):
    """cycle 안에서 (year, month) 바로 다음 계약월 반환 (사이클 끝이면 다음 해 첫 달)"""
    cycle_sorted = sorted(cycle)
    if month in cycle_sorted:
        idx = cycle_sorted.index(month)
        if idx + 1 < len(cycle_sorted):
            return year, cycle_sorted[idx + 1]
        return year + 1, cycle_sorted[0]
    # month가 cycle에 없는 예외 상황 대비 - 그냥 다음 달로
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _symbol_str(base: str, exchange: str, year: int, month: int) -> str:
    return f"{base}{MONTH_CODE[month]}{str(year)[-2:]}.{exchange}"


def _get_volume(symbol: str):
    """해당 심볼의 최근(당일) 거래량 조회. 실패하면 None (호출부에서 안전하게 무시)"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        res = requests.get(url, headers=_HEADERS, timeout=10)
        meta = res.json()["chart"]["result"][0]["meta"]
        return meta.get("regularMarketVolume")
    except Exception:
        return None


def _volume_corrected_month(base: str, rule: dict, year: int, month: int, max_steps: int = 2):
    """날짜 규칙으로 추정한 계약월이 실제 시장과 맞는지 거래량으로 한 번 더 확인.
    바로 다음 계약월의 거래량이 이미 현재(추정) 계약월보다 많으면, 실제로는
    이미 그쪽으로 롤오버된 것으로 보고 다음 월물로 교체한다 (최대 max_steps단계
    까지 연쇄 확인 - 두 달 이상 밀려있는 극단적인 경우 대비).
    Yahoo 거래량 조회가 하나라도 실패하면 안전하게 원래 날짜 규칙 값을 그대로 둔다.
    """
    for _ in range(max_steps):
        cur_symbol = _symbol_str(base, rule["exchange"], year, month)
        next_year, next_month = _next_in_cycle(rule["cycle"], year, month)
        next_symbol = _symbol_str(base, rule["exchange"], next_year, next_month)
        cur_vol = _get_volume(cur_symbol)
        next_vol = _get_volume(next_symbol)
        if cur_vol is None or next_vol is None:
            break  # 조회 실패 - 날짜 규칙 값 그대로 사용
        if next_vol > cur_vol:
            year, month = next_year, next_month
            continue
        break
    return year, month


def get_symbol(name: str, today: date = None) -> str:
    """종목명(예: '나스닥')에 대한 오늘 기준 Yahoo Finance 심볼을 자동 계산해서 반환
    예: get_symbol("나스닥") -> "MNQU26.CME"

    1) 날짜 규칙(third_friday_same_month / n_days_before_month_start)으로 일단 추정
    2) 바로 다음 계약월과 거래량을 비교해서, 이미 시장이 다음 월물로 넘어갔으면 보정
       (2026-08-26: 천연가스가 날짜 규칙보다 며칠 먼저 실제로 롤오버된 걸 발견하고 추가)
    """
    rule = CONTRACT_RULES[name]
    base = BASE_TICKER[name]
    y, m = current_contract_month(rule["cycle"], rule, today)
    y, m = _volume_corrected_month(base, rule, y, m)
    return _symbol_str(base, rule["exchange"], y, m)


def build_symbols(today: date = None) -> dict:
    """yahoo_data.py의 기존 SYMBOLS 딕셔너리와 동일한 형식으로 자동 생성
    {"나스닥": ("MNQU26.CME", None), ...}
    """
    return {
        name: (get_symbol(name, today), MULTIPLIER.get(name))
        for name in CONTRACT_RULES
    }


if __name__ == "__main__":
    # 직접 실행하면 오늘 기준 계산 결과를 눈으로 확인할 수 있습니다.
    # 사용법: python contract_roll.py
    # 날짜 규칙만으로 계산한 값과, 거래량 보정까지 거친 최종값을 같이 보여줘서
    # 혹시 보정이 실제로 일어났는지 한눈에 확인할 수 있게 합니다.
    today = date.today()
    print(f"오늘({today}) 기준 자동 계산된 월물:\n")
    for name in CONTRACT_RULES:
        rule = CONTRACT_RULES[name]
        base = BASE_TICKER[name]
        raw_y, raw_m = current_contract_month(rule["cycle"], rule, today)
        raw_symbol = _symbol_str(base, rule["exchange"], raw_y, raw_m)
        final_symbol = get_symbol(name, today)
        mark = " (거래량 보정으로 변경됨!)" if final_symbol != raw_symbol else ""
        print(f"  {name:6s} -> 날짜규칙: {raw_symbol:12s} 최종: {final_symbol}{mark}")
