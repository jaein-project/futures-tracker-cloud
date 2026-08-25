"""
경제발표 자동화 모듈
- 경제발표 구글 시트에서 오늘 중요 지표 읽기
- 신규실업수당 / 비농(금요일) / 기준금리·금리결정 / CPI / PCE / PPI / ISM PMI / 소매판매 / FOMC / 파월 연설 해당하면
- 발표 10분 전 / 발표 5분 전 / 발표 후 5분 → 진폭 시트에 자동 기록
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


def fetch_today_events():
    """경제발표 시트에서 오늘 날짜 중요 지표 읽기"""
    from google_sheet import get_client

    now = datetime.now(KST)
    today_str = now.strftime("%Y/%m/%d")
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

            # 오늘 날짜 + 미국만
            if row_date != today_str:
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
    if now.weekday() >= 5:
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
            "label_reminder": f"미국_{short_label}_10분전",
            "label_pre": f"미국_{short_label}_전",
            "label_post": f"미국_{short_label}_후",
            "reminder_dt": event_dt - timedelta(minutes=10),
            "before_dt": event_dt - timedelta(minutes=5),
            "after_dt": event_dt + timedelta(minutes=5),
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
