"""
Google Sheets 자동 기록 모듈
- 기존 "진폭" 시트에 HTS 기준 틱 단위로 기록
- 네트워크 오류 시 3회 자동 재시도
- K열 비고 지원 (경제발표 기록용)
- 중복 방지 로직 포함
"""

import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from datetime import datetime, timedelta
import pytz
import os
import pickle
import time as _time

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
SHEET_NAME     = "진폭"
CLIENT_SECRET  = "client_secret.json"
TOKEN_FILE     = "token.pickle"
TIMEZONE       = "Asia/Seoul"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SYMBOL_ORDER = ["나스닥", "오일", "골드", "천연가스", "구리", "유로", "엔화"]

REMINDER_LOG_SHEET = "알림기록"  # 시트에 값을 기록하지 않는 순수 알림(예고/비교)의 중복 방지용 로그 탭
DAILY_SUMMARY_SHEET = "진폭_일일요약"  # 2026-09-01 추가: 미장마감 시 아시아장초반 대비 최종 비교(Slack만 가던 것)를
                                  # 영구 기록. Slack 알림은 3개월 지나면 삭제되므로, 같은 내용을 시트에도
                                  # 구조화된 형태(날짜별 시가/종가/변동)로 보관해서 나중에도 조회/분석 가능하게 함.
                                  # (탭 이름은 재인님이 시트가 늘어나도 헷갈리지 않도록 "진폭_일일요약"으로 지정)
STREAK_RECHECK_SHEET = "반복감지_재검증"  # 2026-09-02 추가: 진폭 반복감지(3회 연속 동일값) 알림이 뜨면,
                                     # 재인님 요청대로 그 자리에서 바로 재조회하지 않고 약 10분 뒤(Yahoo
                                     # Finance가 방금 지나간 5분봉을 확정할 시간을 준 뒤)에 같은 구간을
                                     # 한 번 더 계산해서 값이 다르면 진폭 시트를 보정하고 별도 알림을 보냄.
                                     # 이 탭은 그 "재검증 대기열"을 GitHub Actions 실행 간에도 유지하기 위한
                                     # 저장소 (매 실행이 별도 프로세스라 메모리에는 못 들고 있음).
                                     # 2026-09-04 추가: 메시지ts/채널ID 2개 컬럼 추가 - 재검증 결과를
                                     # 원본 알림 메시지에 이모지+스레드로 달아주기 위함 (봇 더블체크 기능).
STREAK_RECHECK_DELAY_MINUTES = 10

ROLLOVER_CHECK_SHEET = "롤오버_체크"  # 2026-09-04 추가: 월물 롤오버 알림이 뜨면
                                  # ROLLOVER_CHECK_DELAY_MINUTES(10분) 뒤 새 월물 데이터가 정상적으로
                                  # 들어오는지 확인해서 원본 알림에 스레드로 답변해주기 위한 대기열
                                  # (재인님이 HTS로 직접 확인하는 동안, 봇이 먼저 데이터 유입 여부만 체크).
ROLLOVER_CHECK_DELAY_MINUTES = 10

WORKFLOW_ERROR_SHEET = "워크플로우_오류_추적"  # 2026-09-04 추가: 워크플로우 오류 알림이 뜨면 등록해뒀다가,
                                          # 다음 폴링(gh_actions_poll.py)이 예외 없이 끝까지 정상
                                          # 완료되면 '복구됐다'고 보고 원본 알림에 이모지+스레드 답변.

TICK_SIZE = {
    "나스닥":   1,
    "오일":     0.01,
    "골드":     0.1,
    "천연가스": 0.001,
    "구리":     0.001,
    "유로":     0.0001,
    "엔화":     1.0,
}


def get_client():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        print("🌐 브라우저에서 구글 로그인 창이 열립니다...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
        creds = flow.run_local_server(port=0)
        print("✅ 로그인 성공!")
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return gspread.authorize(creds)


def calc_ticks(name, high, low):
    if not high or not low:
        return ""
    diff = high - low
    tick = TICK_SIZE.get(name)
    if tick:
        return round(diff / tick)
    return ""


def is_duplicate(ws, date_str, note):
    """같은 날짜+비고가 이미 있는지 확인 (중복 방지)"""
    try:
        all_values = ws.get_all_values()
        for row in all_values[2:]:  # 헤더 건너뜀
            if len(row) >= 11:
                # B열=날짜(index 1), K열=비고(index 10)
                if row[1] == date_str and row[10] == note:
                    return True
    except:
        pass
    return False


def _get_or_create_reminder_log_ws(spreadsheet):
    """'알림기록' 탭이 없으면 새로 만들어서 반환 (순수 알림 중복 방지용 - 진폭 시트는 건드리지 않음)"""
    try:
        return spreadsheet.worksheet(REMINDER_LOG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=REMINDER_LOG_SHEET, rows=2000, cols=3)
        ws.append_row(["날짜", "라벨", "전송시각"], value_input_option="USER_ENTERED")
        return ws


def is_reminder_sent(spreadsheet, date_str, label) -> bool:
    """순수 알림(경제발표 예고/전후비교/일일다이제스트 등, 진폭 시트에 값을 안 남기는 알림)이
    이미 전송됐는지 확인 - '알림기록' 탭에서 조회"""
    try:
        ws = _get_or_create_reminder_log_ws(spreadsheet)
        all_values = ws.get_all_values()
        for row in all_values[1:]:
            if len(row) >= 2 and row[0] == date_str and row[1] == label:
                return True
    except Exception as e:
        print(f"   ⚠️ 알림기록 시트 확인 오류: {e}")
    return False


def mark_reminder_sent(spreadsheet, date_str, label):
    """순수 알림을 보냈다는 사실을 '알림기록' 탭에 남겨서 다음 폴링에서 중복 전송되지 않게 함"""
    try:
        ws = _get_or_create_reminder_log_ws(spreadsheet)
        now_str = datetime.now(pytz.timezone(TIMEZONE)).strftime("%H:%M:%S")
        ws.append_row([date_str, label, now_str], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 알림기록 시트 기록 오류: {e}")


def _get_or_create_daily_summary_ws(spreadsheet):
    """'진폭_일일요약' 탭이 없으면 새로 만들어서 반환 (2026-09-01 신설).
    종목별로 [시가(아시아장초반)/종가(미장후)/변동] 3열씩 묶어서 헤더를 구성함."""
    try:
        return spreadsheet.worksheet(DAILY_SUMMARY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=DAILY_SUMMARY_SHEET, rows=2000, cols=1 + len(SYMBOL_ORDER) * 3
        )
        header = ["날짜"]
        for name in SYMBOL_ORDER:
            header += [f"{name}_시가(아시아장초반)", f"{name}_종가(미장후)", f"{name}_변동"]
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws


def append_daily_summary(spreadsheet, date_str, first_row, last_row):
    """미장마감(미장후) 시점에 하루 첫 체크포인트(아시아장초반) 대비 최종 비교를 '진폭_일일요약' 탭에 영구
    기록 (2026-09-01부터 적용). Slack의 alert_daily_summary와 완전히 동일한 값을 사용하되, Slack은
    3개월 후 삭제되므로 여기에 종목별 시가/종가/변동을 구조화된 숫자로 남겨서 나중에도 조회·분석 가능하게 함.

    first_row: ws.get_all_values()로 읽은 아시아장초반의 원본 시트 행 (A열 빈칸 → 인덱스 1칸 밀림)
    last_row : build_row()로 만든 미장후의 로컬 행 (밀림 없음) - format_ticks_comparison과 동일한 인덱싱
    """
    try:
        ws = _get_or_create_daily_summary_ws(spreadsheet)
        row = [date_str]
        for idx, name in enumerate(SYMBOL_ORDER):
            first_val = first_row[3 + idx] if len(first_row) > 3 + idx else ""
            last_val = last_row[2 + idx] if len(last_row) > 2 + idx else ""
            try:
                diff = int(last_val) - int(first_val)
            except (ValueError, TypeError):
                diff = ""
            row += [first_val, last_val, diff]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 진폭_일일요약 시트 기록 오류: {e}")


def _get_or_create_streak_recheck_ws(spreadsheet):
    """'반복감지_재검증' 탭이 없으면 새로 만들어서 반환 (2026-09-02 신설).
    2026-09-04: 이미 있는 탭이면 헤더에 메시지ts/채널ID 컬럼이 없을 경우 자동으로 추가함
    (기존에 만들어진 실제 시트는 9개 컬럼이라, 코드만 바꿔서는 새 컬럼이 안 생기기 때문)."""
    try:
        ws = spreadsheet.worksheet(STREAK_RECHECK_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=STREAK_RECHECK_SHEET, rows=2000, cols=11)
        header = ["등록시각", "재검증예정시각", "날짜", "체크시간", "종목", "원래값",
                  "day_start", "target_dt", "상태", "메시지ts", "채널ID"]
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws
    header = ws.row_values(1)
    if len(header) < 11:
        ws.update_cell(1, 10, "메시지ts")
        ws.update_cell(1, 11, "채널ID")
    return ws


def register_streak_recheck(spreadsheet, date_str, time_str, name, old_val, day_start, target_dt,
                             message_ts=None, channel=None):
    """진폭 반복감지(3회 연속 동일값) 알림이 뜬 체크포인트를 STREAK_RECHECK_DELAY_MINUTES
    (기본 10분) 뒤 재검증 대상으로 등록. 그 자리에서 바로 재조회하면 Yahoo Finance가
    방금 지나간 5분봉을 아직 확정 못 했을 가능성이 커서(2026-09-02 엔화 22:25 사례로 확인됨)
    일부러 시간차를 둠 - 재인님 요청.
    message_ts/channel: 2026-09-04 추가 - 원본 알림 메시지 위치(봇 더블체크용). 없어도(웹훅으로
    대체 발송된 경우 등) 정상 동작하며, 그 경우 재검증 결과에 이모지/스레드만 안 달림."""
    try:
        ws = _get_or_create_streak_recheck_ws(spreadsheet)
        now = datetime.now(pytz.timezone(TIMEZONE))
        recheck_at = now + timedelta(minutes=STREAK_RECHECK_DELAY_MINUTES)
        ws.append_row([
            now.isoformat(), recheck_at.isoformat(), date_str, time_str, name, str(old_val),
            day_start.isoformat(), target_dt.isoformat(), "대기",
            message_ts or "", channel or "",
        ], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 반복감지_재검증 등록 오류: {e}")


def get_due_streak_rechecks(spreadsheet):
    """'상태'가 '대기'이고 재검증예정시각이 이미 지난 항목들을 반환.
    day_start/target_dt 파싱에 실패하면(수동 편집 등) 안전하게 건너뜀.
    message_ts/channel은 2026-09-04 이전에 등록된 옛날 행에는 없을 수 있어서 없으면 None 반환
    (호출부에서 없으면 이모지/스레드 없이 조용히 건너뜀)."""
    result = []
    try:
        ws = _get_or_create_streak_recheck_ws(spreadsheet)
        all_values = ws.get_all_values()
        now = datetime.now(pytz.timezone(TIMEZONE))
        for i, row in enumerate(all_values[1:], start=2):  # 헤더 1줄, 실제 시트 행번호는 2부터
            if len(row) < 9 or row[8].strip() != "대기":
                continue
            try:
                recheck_at = datetime.fromisoformat(row[1].strip())
            except Exception:
                continue
            if recheck_at > now:
                continue
            try:
                day_start = datetime.fromisoformat(row[6].strip())
                target_dt = datetime.fromisoformat(row[7].strip())
            except Exception:
                continue
            result.append({
                "row_idx": i,
                "date_str": row[2],
                "time_str": row[3],
                "name": row[4],
                "old_val": row[5],
                "day_start": day_start,
                "target_dt": target_dt,
                "message_ts": row[9].strip() if len(row) > 9 and row[9].strip() else None,
                "channel": row[10].strip() if len(row) > 10 and row[10].strip() else None,
            })
    except Exception as e:
        print(f"   ⚠️ 반복감지_재검증 조회 오류: {e}")
    return result


def mark_streak_recheck_done(spreadsheet, row_idx, status):
    """재검증 처리 완료 표시 ('완료-일치' / '완료-보정' / '완료-조회실패')"""
    try:
        ws = _get_or_create_streak_recheck_ws(spreadsheet)
        ws.update_cell(row_idx, 9, status)
    except Exception as e:
        print(f"   ⚠️ 반복감지_재검증 상태 갱신 오류: {e}")


def _get_or_create_rollover_check_ws(spreadsheet):
    """'롤오버_체크' 탭이 없으면 새로 만들어서 반환 (2026-09-04 신설)."""
    try:
        return spreadsheet.worksheet(ROLLOVER_CHECK_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=ROLLOVER_CHECK_SHEET, rows=2000, cols=9)
        header = ["등록시각", "확인예정시각", "날짜", "종목", "이전월물", "신규월물",
                  "메시지ts", "채널ID", "상태"]
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws


def register_rollover_check(spreadsheet, date_str, name, old_symbol, new_symbol, message_ts, channel):
    """월물 롤오버 알림이 뜬 뒤 ROLLOVER_CHECK_DELAY_MINUTES(10분) 뒤 새 월물 데이터가
    정상적으로 들어오는지 확인하기 위한 대기열 등록 (2026-09-04 신규 - 재인님 요청)."""
    try:
        ws = _get_or_create_rollover_check_ws(spreadsheet)
        now = datetime.now(pytz.timezone(TIMEZONE))
        check_at = now + timedelta(minutes=ROLLOVER_CHECK_DELAY_MINUTES)
        ws.append_row([
            now.isoformat(), check_at.isoformat(), date_str, name, old_symbol, new_symbol,
            message_ts or "", channel or "", "대기",
        ], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 롤오버_체크 등록 오류: {e}")


def get_due_rollover_checks(spreadsheet):
    """'상태'가 '대기'이고 확인예정시각이 이미 지난 롤오버 체크 항목들을 반환."""
    result = []
    try:
        ws = _get_or_create_rollover_check_ws(spreadsheet)
        all_values = ws.get_all_values()
        now = datetime.now(pytz.timezone(TIMEZONE))
        for i, row in enumerate(all_values[1:], start=2):
            if len(row) < 9 or row[8].strip() != "대기":
                continue
            try:
                check_at = datetime.fromisoformat(row[1].strip())
            except Exception:
                continue
            if check_at > now:
                continue
            result.append({
                "row_idx": i,
                "name": row[3],
                "old_symbol": row[4],
                "new_symbol": row[5],
                "message_ts": row[6].strip() if row[6].strip() else None,
                "channel": row[7].strip() if row[7].strip() else None,
            })
    except Exception as e:
        print(f"   ⚠️ 롤오버_체크 조회 오류: {e}")
    return result


def mark_rollover_check_done(spreadsheet, row_idx, status):
    """롤오버 체크 처리 완료 표시 ('완료-정상' / '완료-이상' / '완료-조회실패')"""
    try:
        ws = _get_or_create_rollover_check_ws(spreadsheet)
        ws.update_cell(row_idx, 9, status)
    except Exception as e:
        print(f"   ⚠️ 롤오버_체크 상태 갱신 오류: {e}")


def _get_or_create_workflow_error_ws(spreadsheet):
    """'워크플로우_오류_추적' 탭이 없으면 새로 만들어서 반환 (2026-09-04 신설)."""
    try:
        return spreadsheet.worksheet(WORKFLOW_ERROR_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=WORKFLOW_ERROR_SHEET, rows=2000, cols=5)
        header = ["등록시각", "컨텍스트", "메시지ts", "채널ID", "상태"]
        ws.append_row(header, value_input_option="USER_ENTERED")
        return ws


def register_workflow_error(spreadsheet, context, message_ts, channel):
    """워크플로우 오류 알림이 뜨면 등록 - 다음 폴링(gh_actions_poll.py)이 예외 없이 끝까지
    정상 완료되면 자동으로 '복구됐다'고 보고 원본 알림에 이모지+스레드 답변을 달아줌
    (2026-09-04 신규 - 재인님 요청)."""
    try:
        ws = _get_or_create_workflow_error_ws(spreadsheet)
        now = datetime.now(pytz.timezone(TIMEZONE))
        ws.append_row([now.isoformat(), context, message_ts or "", channel or "", "대기"],
                       value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"   ⚠️ 워크플로우_오류_추적 등록 오류: {e}")


def get_pending_workflow_errors(spreadsheet):
    """'상태'가 '대기'인 워크플로우 오류 항목들을 전부 반환 (시간 조건 없음 - 다음 폴링이
    한 번이라도 예외 없이 끝까지 돌면 그 시점에 전부 '복구됨'으로 처리)."""
    result = []
    try:
        ws = _get_or_create_workflow_error_ws(spreadsheet)
        all_values = ws.get_all_values()
        for i, row in enumerate(all_values[1:], start=2):
            if len(row) < 5 or row[4].strip() != "대기":
                continue
            result.append({
                "row_idx": i,
                "message_ts": row[2].strip() if row[2].strip() else None,
                "channel": row[3].strip() if row[3].strip() else None,
            })
    except Exception as e:
        print(f"   ⚠️ 워크플로우_오류_추적 조회 오류: {e}")
    return result


def mark_workflow_error_resolved(spreadsheet, row_idx, status="완료-복구"):
    """워크플로우 오류 복구 확인 완료 표시"""
    try:
        ws = _get_or_create_workflow_error_ws(spreadsheet)
        ws.update_cell(row_idx, 5, status)
    except Exception as e:
        print(f"   ⚠️ 워크플로우_오류_추적 상태 갱신 오류: {e}")


def update_amplitude_cell(ws, date_str, time_str, name, new_val):
    """진폭 시트에서 date_str+time_str에 해당하는 행을 찾아, name 종목의 틱 값을
    new_val로 덮어씀 (반복감지 재검증 결과 보정용). 성공하면 True, 못 찾으면 False."""
    try:
        col_idx = 4 + SYMBOL_ORDER.index(name)  # A열 빈칸, B=날짜, C=체크시간, D부터 종목 시작
        all_values = ws.get_all_values()
        for i, row in enumerate(all_values[2:], start=3):  # 헤더 2줄, 실제 시트 행번호는 3부터
            if len(row) > 2 and row[1].strip() == date_str and row[2].strip() == time_str:
                ws.update_cell(i, col_idx, new_val)
                return True
    except Exception as e:
        print(f"   ⚠️ 진폭 시트 보정 오류: {e}")
    return False


def _record_data_inner(data: dict, timing: str, note: str = ""):
    kst = pytz.timezone(TIMEZONE)
    now = datetime.now(kst)

    if timing == "미장후":
        record_date = now - timedelta(days=1)
    else:
        record_date = now

    date_str = f"{record_date.year}. {record_date.month}. {record_date.day}"

    hour = now.hour
    minute = now.strftime("%M")
    second = now.strftime("%S")
    if hour < 12:
        ampm = "오전"
        h = hour if hour != 0 else 12
    else:
        ampm = "오후"
        h = hour - 12 if hour != 12 else 12
    time_str = f"{ampm} {h}:{minute}:{second}"

    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)

    # 중복 방지 (경제발표 기록 시)
    if note and is_duplicate(ws, date_str, note):
        print(f"   ⏭️ 중복 기록 건너뜀: {date_str} {note}")
        return

    # B열부터 시작 (A열은 시트에서 비어있음 → append_row는 A열부터 채우므로 빈칸 제외)
    row = [date_str, time_str]
    for name in SYMBOL_ORDER:
        d = data.get(name, {})
        high = d.get("high")
        low  = d.get("low")
        ticks = calc_ticks(name, high, low)
        row.append(ticks)
        if ticks:
            print(f"   📌 [{name}] 고:{high} 저:{low} → {ticks}틱")

    # K열 비고
    row.append(note)

    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"✅ 구글 시트 [진폭] 기록 완료 [{timing}] {date_str} {time_str} {note}")
    print(f"   👉 https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")


def record_data(data: dict, timing: str, retry: int = 3):
    """일반 기록 - 네트워크 오류 시 3회 자동 재시도"""
    for attempt in range(retry):
        try:
            return _record_data_inner(data, timing)
        except Exception as e:
            print(f"   ⚠️ 오류 발생 ({attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                print(f"   🔄 30초 후 재시도...")
                _time.sleep(30)
            else:
                print(f"   ❌ {retry}회 시도 후 실패.")


def record_data_with_note(data: dict, note: str, retry: int = 3):
    """경제발표 기록 - K열 비고 포함, 중복 방지"""
    for attempt in range(retry):
        try:
            return _record_data_inner(data, "경제발표", note=note)
        except Exception as e:
            print(f"   ⚠️ 오류 발생 ({attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                print(f"   🔄 30초 후 재시도...")
                _time.sleep(30)
            else:
                print(f"   ❌ {retry}회 시도 후 실패.")
