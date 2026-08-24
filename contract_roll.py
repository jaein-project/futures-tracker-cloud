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
     넘어간다"는 보수적인 근사 규칙입니다. 처음 롤오버가 일어나는 날, 영웅문/하나
     HTS에서 실제로 어느 월물이 활발히 거래되는지 한 번만 비교해주시고,
     하루라도 어긋나면 알려주세요 - roll_days_before 숫자만 조정하면 바로 고칠
     수 있게 만들어뒀습니다.
  3) 계산된 심볼로 Yahoo Finance에서 데이터를 못 가져오거나 비정상적으로 비어
     있으면 알림이 갑니다 (alerts.py 참고) - 이게 실질적인 안전장치입니다.
"""

from datetime import date, timedelta

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


def get_symbol(name: str, today: date = None) -> str:
    """종목명(예: '나스닥')에 대한 오늘 기준 Yahoo Finance 심볼을 자동 계산해서 반환
    예: get_symbol("나스닥") -> "MNQU26.CME"
    """
    rule = CONTRACT_RULES[name]
    base = BASE_TICKER[name]
    y, m = current_contract_month(rule["cycle"], rule, today)
    code = MONTH_CODE[m] + str(y)[-2:]
    return f"{base}{code}.{rule['exchange']}"


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
    today = date.today()
    print(f"오늘({today}) 기준 자동 계산된 월물:\n")
    for name, (symbol, mult) in build_symbols(today).items():
        print(f"  {name:6s} -> {symbol}")
