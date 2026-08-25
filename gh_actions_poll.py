"""
GitHub Actions 전용 통합 스크립트 - 5분마다 실행
- 하루 4회 체크포인트(아시아마감전/유럽개장전/미장전/미장후) + 경제발표 전/후 5분을
  전부 이 한 스크립트가 5분마다 확인해서, "목표 시각이 지났는데 아직 기록 안 된 것"이 있으면
  그때그때 Yahoo Finance 분봉 데이터로 정확한 값을 역산해서 채움
기존에 특정 cron 시각(예: 22:25)에만 의존하던 방식은 GitHub 무료 스케줄의 지연/누락에 취약해서,
5분마다 계속 폴링하며 "놓친 게 있으면 바로 잡는" 방식으로 통합함.

2026-08-26 추가:
- 중요 경제발표 임박 예고 (10분전/5분전/1분전) - 시트에 기록하지 않는 순수 알림 (#economic-presentation)
- 발표 20분 후 전/후 진폭 비교 알림 - 역시 순수 알림 (#economic-presentation)
- 매일 낮 3시 오늘의 경제발표 전체(중요도 필터 없음) 다이제스트 - 역시 순수 알림 (#economic-presentation)
  이 3가지는 진폭 시트가 아니라 별도 '알림기록' 탭(google_sheet.py)에 중복 방지 기록을 남김
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
        timestamps = result.get("timestamp")
        if not timestamps:
            return None  # 휴장 등으로 데이터가 전혀 없는 구간
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
            from alerts import alert_symbol_missing
            alert_symbol_missing(name, symbol)
    row.append(note)
    return row, any_data

def format_ticks_detail(row, prev_row=None):
    """종목별 진폭 값 + 직전 기록 대비 증감(화살표)을 정리해서 Slack 알림용 문자열로 반환.
    row: build_row()가 만든 로컬 행 [date_str, time_str, tick*7, note] (인덱스 밀림 없음)
    prev_row: ws.get_all_values()로 읽은 원본 시트 행 (A열이 빈칸이라 인덱스가 1칸 밀려있음)
    prev_row가 None이면(거래일 경계 등 비교 대상 없음) 값만 표시함.
    """
    lines = []
    for idx, name in enumerate(SYMBOL_ORDER):
        val = row[2 + idx]
        if val == "" or val is None:
            lines.append(f"{name} 데이터없음")
            continue
        line = f"{name} {val}"
        if prev_row is not None and len(prev_row) > 3 + idx:
            prev_val = prev_row[3 + idx]
            try:
                diff = int(val) - int(prev_val)
                if diff > 0:
                    line += f" (▲{diff})"
                elif diff < 0:
                    line += f" (▼{abs(diff)})"
                else:
                    line += " ( - )"
            except (ValueError, TypeError):
                pass
        lines.append(line)
    return "\n".join(lines)

def format_ticks_comparison(before_row, after_row):
    """경제발표 20분 후 비교용: '{종목} {전값} > {후값} (증감)' 형태로 정리.
    before_row: ws.get_all_values()에서 읽은 '전(-5분)' 원본 행 (A열 빈칸 → 인덱스 1칸 밀림)
    after_row: build_row()로 방금 새로 계산한 로컬 행 (인덱스 밀림 없음)
    """
    lines = []
    for idx, name in enumerate(SYMBOL_ORDER):
        before_val = before_row[3 + idx] if len(before_row) > 3 + idx else ""
        after_val = after_row[2 + idx]
        if before_val in ("", None) or after_val in ("", None):
            lines.append(f"{name} 데이터없음")
            continue
        try:
            b, a = int(before_val), int(after_val)
            diff = a - b
            if diff > 0:
                arrow = f"(▲{diff})"
            elif diff < 0:
                arrow = f"(▼{abs(diff)})"
            else:
                arrow = "( - )"
            lines.append(f"{name} {b} > {a} {arrow}")
        except (ValueError, TypeError):
            lines.append(f"{name} {before_val} > {after_val}")
    return "\n".join(lines)

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

LOOKBACK_DAYS = 3  # 오늘 포함 최근 며칠치를 매번 재확인할지 (하루 전체가 통째로 안 돌았을 경우 대비)

def checkpoint_already_recorded_cached(all_values, date_str: str, target_dt: datetime) -> bool:
    """미리 읽어둔 all_values를 재사용 (매번 시트 새로 안 읽음)"""
    target_minutes = target_dt.hour * 60 + target_dt.minute
    for row in all_values[2:]:
        if len(row) < 11:
            continue
        if row[1].strip() != date_str:
            continue
        note = row[10].strip()
        if note and note.startswith("미국_"):
            continue  # 경제발표 전용 행만 제외
        m = _parse_korean_time_to_minutes(row[2].strip())
        if m is None:
            continue
        diff = min(abs(m - target_minutes), 1440 - abs(m - target_minutes))
        if diff <= CHECK_WINDOW_MINUTES:
            return True
    return False

def check_duplicate_streak(all_values, new_row, threshold=3):
    """같은 값이 threshold번 이상 연속으로 기록되면 Slack 알림
    (경제발표/백필처럼 비고가 있는 행은 스트릭 계산에서 제외)
    2번 정도는 우연히 있을 수 있어서 정상 범위로 보고, 3번 이상부터만 알림 (2026-08-26 기준 변경)
    """
    from alerts import alert_duplicate_streak
    for idx, name in enumerate(SYMBOL_ORDER):
        col = 3 + idx
        new_val = new_row[2 + idx]
        if new_val == "" or new_val is None:
            continue
        streak = 1
        for old_row in reversed(all_values[2:]):
            if len(old_row) <= 10 or old_row[10].strip():
                continue
            if len(old_row) <= col:
                continue
            if str(old_row[col]) == str(new_val):
                streak += 1
            else:
                break
        if streak >= threshold:
            alert_duplicate_streak(name, new_val, streak)

def process_checkpoints(ws, now: datetime):
    sched = SUMMER_SCHEDULE if is_summer_time() else WINTER_SCHEDULE
    try:
        all_values = ws.get_all_values()
    except Exception as e:
        print(f"   ⚠️ 시트 읽기 오류: {e}")
        return
    for day_offset in range(LOOKBACK_DAYS + 1):  # 0=오늘, 1=어제, 2=그제, 3=그끄제
        check_date = (now - timedelta(days=day_offset)).date()
        for timing, hhmm in sched.items():
            th, tm = map(int, hhmm.split(":"))
            target_dt = KST.localize(datetime(check_date.year, check_date.month, check_date.day, th, tm))
            if timing == "미장후":
                record_date_check = target_dt - timedelta(days=1)
                if record_date_check.weekday() >= 5:  # 토/일 세션은 존재하지 않음 (둘 다 스킵)
                    continue
            else:
                if target_dt.weekday() >= 5:
                    continue
            if now < target_dt:
                continue  # 아직 미래 시각
            record_date = target_dt - timedelta(days=1) if timing == "미장후" else target_dt
            date_str = f"{record_date.year}. {record_date.month}. {record_date.day}"
            day_start = record_date.replace(hour=0, minute=0, second=0, microsecond=0)
            if checkpoint_already_recorded_cached(all_values, date_str, target_dt):
                continue
            print(f"🚀 [{timing}] {date_str} 누락분 발견 - {day_start.strftime('%Y-%m-%d %H:%M')} ~ {target_dt.strftime('%Y-%m-%d %H:%M')} KST 역산 중...")
            time_str = time_str_from(target_dt)
            row, any_data = build_row(day_start, target_dt, date_str, time_str, "")
            if any_data:
                check_duplicate_streak(all_values, row)
                ws.append_row(row, value_input_option="USER_ENTERED")
                print(f"✅ [{timing}] 기록 완료: {date_str} {time_str}")
                from alerts import alert_checkpoint_recorded
                prev_row = all_values[-1] if len(all_values) > 2 else None
                if prev_row is not None and len(prev_row) > 1 and prev_row[1].strip() != date_str:
                    prev_row = None  # 거래일이 바뀌는 경계(예: 오늘 아시아마감전 vs 전일 미장후)는 비교 대상에서 제외
                detail = format_ticks_detail(row, prev_row)
                time_hhmm = target_dt.strftime("%H:%M")
                alert_checkpoint_recorded(date_str, time_hhmm, timing, detail)
                # 방금 추가한 행도 반영해서 이후 루프에서 다시 중복 감지되도록 갱신
                all_values.append(row)
            else:
                print(f"   ❌ [{timing}] 전 종목 데이터 없음 - 기록 취소")

def process_economic(ws, now: datetime):
    groups = get_today_event_groups()
    if not groups:
        return
    for g in groups:
        d = datetime.strptime(g["date"], "%Y/%m/%d")
        date_str = f"{d.year}. {d.month}. {d.day}"

        # 실제 진폭 기록 (발표 전 5분 / 발표 후 5분) - 시트에 기록 + #futures-tracker 알림
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
                from alerts import alert_economic_recorded
                try:
                    all_values = ws.get_all_values()
                except Exception as e:
                    print(f"   ⚠️ 비교용 시트 읽기 오류: {e}")
                    all_values = []
                prev_row = None
                if note == g["label_post"]:
                    # "후"는 같은 발표의 "전" 기록과 비교 (발표로 인한 변동폭)
                    for r in reversed(all_values[2:]):
                        if len(r) >= 11 and r[1].strip() == date_str and r[10].strip() == g["label_pre"]:
                            prev_row = r
                            break
                elif len(all_values) > 3:
                    # "전"은 방금 추가되기 전 마지막 기록과 비교 (단, 날짜가 다르면 비교 안 함)
                    candidate = all_values[-2]
                    if len(candidate) > 1 and candidate[1].strip() == date_str:
                        prev_row = candidate
                detail = format_ticks_detail(row, prev_row)
                alert_economic_recorded(date_str, note, g.get("names"), detail, event_time=g["time"])
            else:
                print(f"   ❌ 경제발표 전 종목 데이터 없음 - 기록 취소")

        # 중요 발표 임박 예고 (10분전/5분전/1분전) - 시트 기록 없이 #economic-presentation 순수 알림
        process_reminder_tiers(ws, now, g, date_str)

        # 발표 20분 후 전/후 진폭 비교 - 시트 기록 없이 #economic-presentation 순수 알림
        process_post_comparison(ws, now, g, date_str)


def process_reminder_tiers(ws, now: datetime, g: dict, date_str: str):
    """중요 경제발표 임박 예고 - 10분 전 / 5분 전 / 1분 전, 시트에는 기록하지 않는 순수 알림.
    (기존에는 '10분전'만 시트에 기록하며 예고했는데, 2026-08-26부터 순수 알림 3단계로 교체)"""
    from alerts import alert_reminder_tier
    from google_sheet import is_reminder_sent, mark_reminder_sent
    event_dt = KST.localize(datetime.strptime(f"{g['date']} {g['time']}", "%Y/%m/%d %H:%M"))
    spreadsheet = ws.spreadsheet
    for tier_label, tier_dt in [("10분 전", g["reminder_dt"]), ("5분 전", g["before_dt"]), ("1분 전", g["one_min_dt"])]:
        if now < tier_dt or now >= event_dt:
            continue  # 아직 그 시점이 안 됐거나, 발표가 이미 지나버려서 예고가 의미 없어짐
        label = f"{g['label_pre']}_{tier_label}예고"
        if is_reminder_sent(spreadsheet, date_str, label):
            continue
        alert_reminder_tier(date_str, tier_label, g["names"], g["time"])
        mark_reminder_sent(spreadsheet, date_str, label)


def process_post_comparison(ws, now: datetime, g: dict, date_str: str):
    """발표 20분 후, 발표 전(-5분) 대비 진폭이 얼마나 움직였는지 비교 알림.
    시트에는 기록하지 않는 순수 알림 (2026-08-26 신규) - #economic-presentation"""
    from alerts import alert_pre_post_comparison
    from google_sheet import is_reminder_sent, mark_reminder_sent
    compare_dt = g["compare_dt"]
    if now < compare_dt:
        return
    label = f"{g['label_pre']}_20분후비교"
    spreadsheet = ws.spreadsheet
    if is_reminder_sent(spreadsheet, date_str, label):
        return
    try:
        all_values = ws.get_all_values()
    except Exception as e:
        print(f"   ⚠️ 비교용 시트 읽기 오류: {e}")
        return
    before_row = None
    for r in reversed(all_values[2:]):
        if len(r) >= 11 and r[1].strip() == date_str and r[10].strip() == g["label_pre"]:
            before_row = r
            break
    if before_row is None:
        print(f"   ⏭️ [{g['label_pre']}] '전' 기록이 아직 없어서 20분 후 비교 스킵")
        return
    day_start = compare_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    after_row, any_data = build_row(day_start, compare_dt, date_str, time_str_from(compare_dt), "")
    if not any_data:
        print(f"   ❌ 20분 후 비교용 데이터 없음")
        return
    comparison = format_ticks_comparison(before_row, after_row)
    name_str = ", ".join(g.get("names") or []) or "발표"
    alert_pre_post_comparison(date_str, g["time"], name_str, comparison)
    mark_reminder_sent(spreadsheet, date_str, label)


def process_daily_digest(ws, now: datetime):
    """매일 낮 3시, 오늘 예정된 경제발표 전체(중요도 필터 없음)를 한 번 정리해서 안내.
    시트에는 기록하지 않는 순수 알림 (2026-08-26 신규) - #economic-presentation"""
    from alerts import alert_daily_digest
    from google_sheet import is_reminder_sent, mark_reminder_sent
    from economic_calendar import fetch_today_events_all
    if now.weekday() >= 5:
        return
    if now.hour < 15:
        return
    today = now.date()
    date_str = f"{today.year}. {today.month}. {today.day}"
    label = "일일경제발표다이제스트"
    spreadsheet = ws.spreadsheet
    if is_reminder_sent(spreadsheet, date_str, label):
        return
    events = fetch_today_events_all()
    if not events:
        mark_reminder_sent(spreadsheet, date_str, label)  # 오늘 발표가 없어도 매 폴링마다 다시 확인하지 않도록 마킹
        return

    def _time_key(e):
        parts = e["time"].strip().split(":")
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except Exception:
            return (99, 99)

    events_sorted = sorted(events, key=_time_key)
    for e in events_sorted:
        parts = e["time"].strip().split(":")
        e["time"] = f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else e["time"]
    alert_daily_digest(date_str, events_sorted)
    mark_reminder_sent(spreadsheet, date_str, label)

def check_rollover_alerts(ws, now: datetime):
    """오늘 아직 기록된 체크포인트가 없으면(오늘 첫 폴링으로 간주), 어제 대비
    월물이 자동으로 바뀐 종목이 있는지 확인해서 알림"""
    from contract_roll import CONTRACT_RULES, get_symbol
    from alerts import alert_symbol_rolled
    today = now.date()
    today_str = f"{today.year}. {today.month}. {today.day}"
    try:
        all_values = ws.get_all_values()
    except Exception as e:
        print(f"   ⚠️ 롤오버 확인용 시트 읽기 오류: {e}")
        return
    for row in all_values[2:]:
        if len(row) >= 2 and row[1].strip() == today_str:
            return  # 오늘 이미 기록된 행이 있음 - 오늘 첫 폴링이 아니므로 스킵
    yesterday = today - timedelta(days=1)
    for name in CONTRACT_RULES:
        try:
            old_symbol = get_symbol(name, yesterday)
            new_symbol = get_symbol(name, today)
        except Exception as e:
            print(f"   ⚠️ [{name}] 롤오버 확인 오류: {e}")
            continue
        if old_symbol != new_symbol:
            print(f"🔄 [{name}] 월물 자동 롤오버 감지: {old_symbol} → {new_symbol}")
            alert_symbol_rolled(name, old_symbol, new_symbol)


def main():
    now = datetime.now(KST)
    print(f"🔍 폴링 체크 - {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)
    check_rollover_alerts(ws, now)
    process_checkpoints(ws, now)
    process_economic(ws, now)
    process_daily_digest(ws, now)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from alerts import alert_workflow_exception
        alert_workflow_exception("gh_actions_poll.py", e)
        raise
