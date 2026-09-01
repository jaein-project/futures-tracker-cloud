"""
경제발표 자동화 모듈
- 경제발표 구글 시트에서 오늘 중요 지표 읽기
- 신규실업수당 / 비농(금요일) / 기준금리·금리결정 / CPI / PCE / PPI / ISM PMI / 소매판매 / FOMC / 파월 연설 해당하면
- 발표 5분 전 / 발표 후 5분 → 진폭 시트에 자동 기록 (#futures-tracker 알림)
- 발표 10분 전 / 5분 전 / 1분 전 → 시트 기록 없이 순수 예고 알림만 (#economic-presentation)
- 발표 20분 후 → 전(-5분) 대비 진폭 비교 알림 (#economic-presentation, 시트 기록 없음)
- 매일 낮 3시 → 오늘 예정된 경제발표 전체(중요도 필터 없음) 다이제스트 (#economic-presentation)
"""

import re
import time
from datetime import datetime, timedelta
import pytz

KST = pytz.timezone("Asia/Seoul")
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
CALENDAR_SHEET = "경제발표"

# 이미 스케줄된 발표 (중복 방지)
scheduled_events = set()


def is_summer_time():
    """gh_actions_poll.py와 동일 - 뉴욕 서머타임(DST) 여부로 CME 정산 리셋 시각(07:00/08:00 KST) 판단"""
    eastern = pytz.timezone("America/New_York")
    return bool(datetime.now(eastern).dst())


def trading_day_start(dt_kst: datetime) -> datetime:
    """2026-08-27 추가: 경제발표의 '오늘' 판단을 체크포인트(gh_actions_poll.py)와 동일한
    거래일 기준(CME 정산 리셋 시각 - 서머타임 07:00 / 아니면 08:00 KST)으로 통일.
    자정이 아니라 이 시각을 넘겨야 '다음 거래일'로 인정됨 - 그래서 새벽 0~7시 사이에
    발표되는 지표(예: 0:45 Fed 연설, 2:00 국채입찰)는 달력상 날짜가 바뀌어도
    '전날 거래일'에 속한 것으로 정확히 귀속됨.
    (gh_actions_poll.py의 trading_day_start()와 완전히 동일한 로직 - 그쪽을 import하면
    순환참조가 생겨서 이 모듈에도 동일하게 정의함)"""
    boundary_hour = 7 if is_summer_time() else 8
    boundary = dt_kst.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    if dt_kst < boundary:
        boundary -= timedelta(days=1)
    return boundary


def _row_trading_day(row_date: str, row_time: str):
    """경제발표 시트의 (달력 날짜, 시각) 한 쌍을 실제 거래일 날짜(date 객체)로 환산.
    파싱 실패 시 None (호출부에서 안전하게 그 행을 건너뜀)."""
    try:
        parts = row_time.strip().split(":")
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
        naive = datetime.strptime(row_date, "%Y/%m/%d").replace(hour=hh, minute=mm)
        return trading_day_start(KST.localize(naive)).date()
    except Exception:
        return None


def is_amplitude_target(name: str, weekday: str) -> bool:
    """진폭 기록 대상 지표 여부 (조건부 서식 기준)
    - 차트가 크게 흔들릴 만한 발표 위주로 선정 (2026-08-25 확장)"""
    if "신규 실업수당" in name or "신규실업수당" in name:
        return True
    if weekday == "금" and "비농" in name:
        return True
    if re.search(r"기준금리|금리결정", name):
        return True
    if "CPI" in name or "소비자물가" in name:
        return True
    if "PCE" in name or "개인소비지출" in name:
        return True
    if "PPI" in name or "생산자물가" in name:
        return True
    if "ISM" in name:
        return True
    if "소매판매" in name or "Retail Sales" in name:
        return True
    if "FOMC" in name:
        return True
    if "파월" in name or "Powell" in name:
        return True
    return False


def fetch_today_events_all():
    """경제발표 시트에서 '오늘 거래일'의 '미국' 지표 전체를 중요도 필터 없이 읽기
    (매일 낮 3시 '오늘의 경제 발표 전체' 다이제스트용 - is_amplitude_target 필터를 타지 않음)
    2026-08-27: 달력 날짜(자정 기준)가 아니라 거래일(07:00/08:00 KST 기준)로 판단하도록 변경 -
    새벽에 발표되는 지표가 엉뚱한 '다음 날짜'로 잡혀서 다이제스트에 잘못 묶이던 문제를
    체크포인트와 동일한 기준으로 통일해서 해결."""
    from google_sheet import get_client

    now = datetime.now(KST)
    current_trading_day = trading_day_start(now).date()

    try:
        client = get_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        ws = spreadsheet.worksheet(CALENDAR_SHEET)
        all_rows = ws.get_all_values()

        events = []
        for row in all_rows[2:]:  # 헤더 2행 건너뜀
            if len(row) < 6:
                continue

            row_date    = row[1].strip()
            row_time    = row[3].strip()
            row_country = row[4].strip()
            row_name    = row[5].strip()

            if _row_trading_day(row_date, row_time) != current_trading_day:
                continue
            if "미국" not in row_country:
                continue
            if not row_name:
                continue

            events.append({"date": row_date, "time": row_time, "name": row_name})

        return events

    except Exception as ex:
        print(f"   ❌ 경제발표 시트(전체) 읽기 오류: {ex}")
        return []


def fetch_today_events():
    """경제발표 시트에서 오늘 날짜 중요 지표 읽기"""
    from google_sheet import get_client

    now = datetime.now(KST)
    current_trading_day = trading_day_start(now).date()
    weekday_ko = WEEKDAYS_KO[now.weekday()]

    try:
        client = get_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        ws = spreadsheet.worksheet(CALENDAR_SHEET)
        all_rows = ws.get_all_values()

        events = []
        for row in all_rows[2:]:  # 헤더 2행 건너뜀
            if len(row) < 6:
                continue

            # B=날짜(1), C=요일(2), D=시간(3), E=국가(4), F=발표명(5)
            row_date    = row[1].strip()
            row_weekday = row[2].strip()
            row_time    = row[3].strip()
            row_country = row[4].strip()
            row_name    = row[5].strip()

            # 오늘 거래일(07:00/08:00 KST 기준) + 미국만
            # 2026-08-27: 달력 날짜 대신 거래일 기준으로 변경 (fetch_today_events_all과 동일 사유)
            if _row_trading_day(row_date, row_time) != current_trading_day:
                continue
            if "미국" not in row_country:
                continue
            if not row_name:
                continue

            # 중요 지표만
            if not is_amplitude_target(row_name, row_weekday):
                continue

            events.append({
                "date":    row_date,
                "weekday": row_weekday,
                "time":    row_time,
                "name":    row_name,
            })

        if events:
            print(f"   📌 오늘 진폭 기록 대상 {len(events)}건:")
            for e in events:
                print(f"      {e['time']} | {e['name']}")
        else:
            print("   ℹ️ 오늘 진폭 기록 대상 없음")

        return events

    except Exception as ex:
        print(f"   ❌ 경제발표 시트 읽기 오류: {ex}")
        return []


def get_today_event_groups():
    """오늘 중요 지표를 시각별로 그룹핑해서 (전/후 라벨, 전/후 datetime)까지 계산해서 반환
    threading.Timer 등 예약 없이 순수 데이터만 반환 - GitHub Actions 같은 one-shot 실행 환경용
    반환: [{"date":..., "time":..., "label_pre":..., "label_post":...,
            "before_dt":..., "after_dt":..., "names":[...]}, ...]
    """
    now = datetime.now(KST)
    # 2026-08-27: 달력 요일이 아니라 거래일 기준 요일로 주말 판단 (예: 토요일 새벽 0~7시는
    # 아직 금요일 거래일에 속하므로 스킵하면 안 됨)
    if trading_day_start(now).weekday() >= 5:
        return []

    events = fetch_today_events()

    groups = {}
    for e in events:
        key = (e["date"], e["time"])
        groups.setdefault(key, []).append(e)

    result = []
    for (date, time_), group in groups.items():
        _time_parts = time_.strip().split(":")
        time_hm = f"{_time_parts[0]}:{_time_parts[1]}" if len(_time_parts) >= 2 else time_.strip()
        try:
            event_dt = KST.localize(datetime.strptime(f"{date} {time_hm}", "%Y/%m/%d %H:%M"))
        except Exception:
            continue

        names = [e["name"] for e in group]
        short_label = f"{names[0][:12]} 등 {len(names)}건" if len(names) > 1 else names[0][:15]

        result.append({
            "date": date,
            "time": time_hm,
            "names": names,
            "label_pre": f"미국_{short_label}_전",
            "label_post": f"미국_{short_label}_후",
            # 2026-09-02 수정: '1분 전' 창(1분 폭)이 5분 주기 폴링(GitHub 기본 스케줄 + 외부
            # cron-job.org)보다 좁아서 구조적으로 거의 발송이 안 되던 문제 - 재인님 확인 후
            # '1분 전' 삭제하고 '15분 전'을 추가해서 15/10/5분 전 3단계로 재편성.
            # 각 창이 정확히 5분 폭이라 5분 주기 폴링과 맞물려 안정적으로 걸림.
            "reminder15_dt": event_dt - timedelta(minutes=15),
            "reminder_dt": event_dt - timedelta(minutes=10),
            "before_dt": event_dt - timedelta(minutes=5),
            "after_dt": event_dt + timedelta(minutes=5),
            "compare_dt": event_dt + timedelta(minutes=20),
        })

    return result


def schedule_amplitude_recording(date: str, time_: str, group: list):
    """같은 날짜+시각에 몰린 발표들은 한 번만 기록 (발표 5분 전 / 발표 후 5분)
    group: 같은 시각에 겹치는 이벤트 리스트 (예: CPI 관련 6개 지표가 21:30에 동시 발표)
    (로컬에서 계속 켜두는 main.py용 - threading.Timer로 예약. 클라우드에서는 미사용)
    """
    import threading

    event_key = f"{date}_{time_}"
    if event_key in scheduled_events:
        return
    scheduled_events.add(event_key)

    now = datetime.now(KST)

    # 시트 시간 값이 '21:30' / '21:30:00' / '9:30:00' 등 다양하게 올 수 있어
    # ':' 기준으로 시/분만 안전하게 추출
    _time_parts = time_.strip().split(":")
    time_hm = f"{_time_parts[0]}:{_time_parts[1]}" if len(_time_parts) >= 2 else time_.strip()

    try:
        event_dt = KST.localize(
            datetime.strptime(f"{date} {time_hm}", "%Y/%m/%d %H:%M")
        )
    except:
        print(f"   ⚠️ 시간 파싱 오류: {time_}")
        return

    names = [e["name"] for e in group]
    if len(names) > 1:
        short_label = f"{names[0][:12]} 등 {len(names)}건"
    else:
        short_label = names[0][:15]

    label_pre  = f"미국_{short_label}_전"
    label_post = f"미국_{short_label}_후"

    if len(names) > 1:
        print(f"   ℹ️ {time_hm} 동시 발표 {len(names)}건 → 1건으로 묶어서 기록: {', '.join(names)}")

    # 발표 5분 전
    before_dt = event_dt - timedelta(minutes=5)
    if before_dt > now:
        delay = (before_dt - now).total_seconds()
        t = threading.Timer(delay, lambda l=label_pre: _record_amplitude(l))
        t.daemon = True
        t.start()
        print(f"   ⏰ [{label_pre}] {before_dt.strftime('%H:%M')} KST 예약")
    else:
        print(f"   ⏭️ [{label_pre}] 이미 지난 시간 스킵")

    # 발표 후 5분
    after_dt = event_dt + timedelta(minutes=5)
    if after_dt > now:
        delay = (after_dt - now).total_seconds()
        t = threading.Timer(delay, lambda l=label_post: _record_amplitude(l))
        t.daemon = True
        t.start()
        print(f"   ⏰ [{label_post}] {after_dt.strftime('%H:%M')} KST 예약")
    else:
        print(f"   ⏭️ [{label_post}] 이미 지난 시간 스킵")



def _record_amplitude(note: str):
    """진폭 수집 후 비고 포함 기록"""
    print(f"\n📌 경제발표 진폭 기록: {note} - {datetime.now().strftime('%H:%M:%S')}")
    try:
        from yahoo_data import get_all_symbols
        from google_sheet import record_data_with_note
        data = get_all_symbols()
        record_data_with_note(data, note)
    except Exception as e:
        print(f"❌ 오류: {e}")


def schedule_today_amplitude():
    """오늘 중요 지표 진폭 스케줄 등록"""
    now = datetime.now(KST)

    # 주말 스킵
    if now.weekday() >= 5:
        print("   ⏭️ 주말 스킵")
        return

    print(f"\n📅 오늘 경제발표 진폭 스케줄 확인 중... ({now.strftime('%Y-%m-%d')})")
    events = fetch_today_events()

    # 같은 날짜+시각에 여러 지표가 몰려있으면 한 그룹으로 묶기 (예: CPI 관련 6개가 동시 발표)
    groups = {}
    for e in events:
        key = (e["date"], e["time"])
        groups.setdefault(key, []).append(e)

    for (date, time_), group in groups.items():
        schedule_amplitude_recording(date, time_, group)


def run_calendar_scheduler():
    """경제발표 스케줄러 시작 (로컬에서 계속 켜두는 main.py용. 클라우드에서는 미사용)"""
    import schedule

    print("\n📅 경제발표 스케줄러 시작...")

    # 시작 시 즉시 오늘 스케줄 등록
    schedule_today_amplitude()

    # 매일 오전 7시에 재등록 (자정 이후 새 날짜 대응)
    schedule.every().day.at("07:00").do(schedule_today_amplitude)

    print("   ✅ 경제발표 스케줄러 완료!")
