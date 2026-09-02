"""
Slack 알림 모듈
================

GitHub Actions 워크플로우 자체는 "성공"으로 끝나더라도, 특정 종목만 데이터가
비어있거나(None) 월물 자동계산 결과가 이상해 보이는 경우가 있을 수 있습니다.
그런 "조용한 실패"를 잡아서 Slack으로 알려주는 역할을 합니다.

채널별로 다른 웹훅을 쓸 수 있습니다 (Slack 웹훅은 1개 = 1채널 고정):
  - SLACK_WEBHOOK_URL           → #trading-notify (문제/에러 알림 전용)
  - SLACK_WEBHOOK_URL_ECONOMIC  → #economic-presentation (경제발표 예고 10/5/1분전, 일일 다이제스트,
                                   발표 20분후 전/후 비교 - 전부 시트에는 기록하지 않는 순수 알림)
  - SLACK_WEBHOOK_URL_TRACKER   → #futures-tracker (체크포인트 + 경제발표 전/후 5분, 진폭이 실제로
                                   시트에 기록될 때만 오는 알림)

사용 전 준비:
  1) Slack 앱의 Incoming Webhooks 페이지에서 채널별로 "Add New Webhook to
     Workspace"를 눌러 웹훅 URL을 각각 발급
  2) GitHub 저장소 Settings > Secrets and variables > Actions 에
     위 3개 이름으로 각각 등록 (SLACK_WEBHOOK_URL_ECONOMIC, _TRACKER 는 선택사항 -
     없으면 해당 알림은 SLACK_WEBHOOK_URL 채널로 대신 전송됨)
  3) 워크플로우 yml에서 해당 시크릿들을 환경변수로 넘겨주기:
       env:
         SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
         SLACK_WEBHOOK_URL_ECONOMIC: ${{ secrets.SLACK_WEBHOOK_URL_ECONOMIC }}
         SLACK_WEBHOOK_URL_TRACKER: ${{ secrets.SLACK_WEBHOOK_URL_TRACKER }}

로컬(내 컴퓨터)에서 테스트할 때는 터미널에서:
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
    python alerts.py   # 테스트 메시지 발송
"""

import os
import requests

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"                    # #trading-notify (문제/에러)
WEBHOOK_ECONOMIC_ENV_VAR = "SLACK_WEBHOOK_URL_ECONOMIC"   # #economic-presentation (경제발표 예고)
WEBHOOK_TRACKER_ENV_VAR = "SLACK_WEBHOOK_URL_TRACKER"     # #futures-tracker (진폭 기록)
NTFY_TOPIC_ENV_VAR = "NTFY_TOPIC"


def _first_line(value: str) -> str:
    """환경변수 값에 줄바꿈/보이지 않는 문자가 섞여 들어간 경우 방어용 - 첫 줄만 정리해서 사용"""
    value = (value or "").strip().lstrip("\ufeff\u200b")
    return value.splitlines()[0].strip() if value else ""


def send_ntfy_alert(message: str, title: str = None) -> bool:
    """ntfy.sh 앱으로 실시간 폰 푸시 알림 전송 (Slack과 별도로 당분간 병행 사용).
    NTFY_TOPIC 환경변수가 없으면 조용히 스킵합니다 (에러 아님).
    """
    topic = _first_line(os.environ.get(NTFY_TOPIC_ENV_VAR))
    if not topic:
        return False
    headers = {"Title": title.encode("utf-8")} if title else {}
    try:
        res = requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ ntfy 전송 중 오류: {e}")
        return False


def send_alert(message: str, title: str = None, webhook_env_var: str = WEBHOOK_ENV_VAR) -> bool:
    """Slack + ntfy 둘 다로 전송 (둘 중 하나만 설정돼 있어도 동작)
    webhook_env_var로 어느 채널용 웹훅을 쓸지 지정 가능 (기본: #trading-notify)
    반환값: 둘 중 하나라도 성공하면 True (기존엔 반환값이 없어서 호출부에서 항상 실패로 보였음)"""
    slack_ok = send_slack_alert(message, title, webhook_env_var)
    ntfy_ok = send_ntfy_alert(message, title)
    return slack_ok or ntfy_ok


def send_slack_alert(message: str, title: str = None, webhook_env_var: str = WEBHOOK_ENV_VAR) -> bool:
    """Slack으로 알림 메시지 전송. 지정한 webhook_env_var가 없으면 기본(SLACK_WEBHOOK_URL)으로
    자동 대체하고, 그것도 없으면 콘솔에만 출력.
    반환값: 실제로 Slack 전송에 성공했으면 True

    (2026-08-26: 채널별 발신 이름/아이콘을 payload의 username/icon_emoji로 오버라이드해보려 했으나,
    이 워크스페이스의 webhook 앱이 하나(trading-notify, A0BRZ979JLX)를 3채널이 공유하는 구조라
    오버라이드가 무시되는 것을 확인 - 대신 Slack 앱 자체 이름/아이콘을 "알림봇"으로 통일하기로 함.
    별도 앱 3개로 쪼개는 건 번거로워서 보류.)
    """
    webhook_url = _first_line(os.environ.get(webhook_env_var))
    if not webhook_url and webhook_env_var != WEBHOOK_ENV_VAR:
        webhook_url = _first_line(os.environ.get(WEBHOOK_ENV_VAR))

    text = f"*{title}*\n{message}" if title else message

    if not webhook_url:
        print(f"⚠️ [{webhook_env_var}] 환경변수가 없어서 Slack 대신 콘솔에만 출력합니다:")
        print(text)
        return False

    print(f"🔍 webhook_url({webhook_env_var}) 진단: 길이={len(webhook_url)}자, https로 시작={webhook_url.startswith('https://')}")
    try:
        res = requests.post(webhook_url, json={"text": text}, timeout=10)
        if res.status_code == 200:
            return True
        print(f"❌ Slack 전송 실패 (status={res.status_code}): {res.text}")
        return False
    except Exception as e:
        print(f"❌ Slack 전송 중 오류: {e}")
        return False


def alert_symbol_missing(name: str, symbol: str):
    """특정 종목 데이터가 비어서(None) 왔을 때"""
    send_alert(
        f"`{name}` ({symbol}) 데이터를 못 가져왔어요. 월물 코드가 만기 지났거나, "
        f"Yahoo Finance 쪽 문제일 수 있어요. 확인해주세요.",
        title="⚠️ 진폭 데이터 누락",
    )


def alert_workflow_exception(context: str, error: Exception):
    """예외가 발생해서 워크플로우가 실패했을 때"""
    send_alert(
        f"`{context}` 실행 중 오류가 발생했어요:\n```{error}```",
        title="❌ 워크플로우 오류",
    )


def alert_symbol_rolled(name: str, old_symbol: str, new_symbol: str):
    """월물이 자동으로 롤오버됐을 때 - 확인용 알림 (원치 않으면 호출 안 해도 됨)"""
    send_alert(
        f"`{name}` 월물이 자동으로 바뀌었어요: `{old_symbol}` → `{new_symbol}`\n"
        f"영웅문/하나 HTS와 한 번 비교해서 맞는지 확인해주세요.",
        title="🔄 월물 자동 롤오버",
    )


def alert_duplicate_streak(name, value, streak):
    """같은 값이 3번 이상 연속으로 기록됐을 때 (월물 만기 임박/데이터 정체 의심)
    (2번은 우연히 있을 수 있어서 정상 범위로 보고, 3번 이상부터만 알림 - 2026-08-26 기준 변경)
    2026-09-02 추가: 이 알림이 뜬 체크포인트는 10분 뒤 자동으로 한 번 더 재검증되고,
    값이 다르게 나오면 alert_streak_recheck_correction()이 별도로 보정 안내를 보냄.
    2026-09-02: 멘트는 재인님과 별도로 확정 후 반영 예정 - 우선 원래 문구로 되돌림."""
    send_alert(
        f"`{name}` 값이 {streak}번 연속 동일한 값으로 기록되고 있어요. \n"
        f"월물 만기 혹은 데이터 소스에 문제가 있을 수 있어요. 확인 해주세요!\n"
        f"➡️ 동일한 진폭 : {value}\n"
        f"⏳ 10분 뒤 자동으로 한 번 더 재검증할게요 (다르면 별도로 알려드려요)",
        title="🚨진폭 값 반복 감지🚨",
    )


def alert_streak_recheck_correction(name, date_str, time_str, old_val, new_val):
    """2026-09-02 신규: 반복감지 알림이 뜬 체크포인트를 10분 뒤 재검증했더니 값이 달라져서
    진폭 시트를 보정했을 때 보내는 알림 (재인님 요청 - 엔화 22:25 케이스처럼 Yahoo Finance
    데이터가 늦게 확정되면서 처음엔 낮게 기록됐던 값을 뒤늦게 바로잡는 경우).
    2026-09-02: 멘트는 재인님과 별도로 확정 후 반영 예정 - 우선 원래 문구로 되돌림."""
    send_alert(
        f"`{name}` {date_str} {time_str} 체크포인트, 반복감지 후 10분 뒤 재검증해보니\n"
        f"값이 달라서 스프레드시트를 보정했어요.\n"
        f"➡️ 기존 {old_val}틱 → 보정 {new_val}틱\n"
        f"(Yahoo Finance 데이터가 뒤늦게 확정되면서 처음엔 낮게 잡혔던 것으로 보여요)",
        title="🔧진폭 값 보정 완료🔧",
    )


# 체크포인트 내부 키(스케줄 계산용) → 실제 알림 문구에 쓰는 표시명
# 유럽만 원래 이름에 '장'이 없어서 추가, 아시아마감전/미장전은 원래 이름 그대로 사용
# (2026-08-26: 아시아마감전에 '장'을 넣었다가 사용자 확인 후 원래 이름으로 되돌림)
# (2026-08-26: 아시아장중 - 낮 12시, 하루의 첫 체크포인트 - 추가)
# (2026-08-27: 미장후 -> 미장마감으로 표시 변경 - 실제로 그 시점이 하루 마감이라는 사용자 의견 반영.
#  내부 로직에서 쓰는 키 값("미장후")은 그대로 두고 화면에 보이는 이름만 바꿈)
# (2026-08-31: 아시아장초반(09:00)/아시아장중반(10:30) 추가 - 아시아장중(12:00) 하나로는
#  07:00~12:00 사이 언제 진폭이 커졌는지 알 수 없어서, 최근 일주일 시간대별 비교 결과를
#  바탕으로 나스닥이 몰리는 09시대·오일/골드가 몰리는 10시대 직전을 새로 끼워넣음.
#  이제 "하루의 첫 체크포인트"는 아시아장초반이 됨)
CHECKPOINT_DISPLAY_NAMES = {
    "아시아장초반": "아시아장초반",
    "아시아장중반": "아시아장중반",
    "아시아장중": "아시아장중",
    "아시아마감전": "아시아마감전",
    "유럽개장전": "유럽장개장전",
    "미장전": "미장전",
    "미장후": "미장마감",
}


def _to_ampm_hhmm(time_hhmm: str) -> str:
    """'15:20' -> '오후 3:20' 형태(12시간제)로 변환"""
    try:
        h, m = map(int, time_hhmm.split(":"))
    except Exception:
        return time_hhmm
    period = "오전" if h < 12 else "오후"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{period} {h12}:{m:02d}"


def alert_checkpoint_recorded(date_str: str, time_hhmm: str, timing: str, detail: str = None):
    """정기 체크포인트(아시아마감전 등) 진폭이 시트에 기록됐을 때 #futures-tracker로 실시간 알림.
    time_hhmm: 그 체크포인트의 실제 시각 24시간제 HH:MM (예: '15:20') - 문구에는 12시간제로 변환해서 표시
    detail: 종목별 진폭 값 + 직전 기록 대비 증감을 정리한 문자열 (gh_actions_poll.py에서 생성)"""
    display_name = CHECKPOINT_DISPLAY_NAMES.get(timing, timing)
    ampm_time = _to_ampm_hhmm(time_hhmm)
    message = f"`{display_name}({ampm_time})` 진폭 기록 완료!"
    if detail:
        message += f"\n{detail}"
    send_alert(
        message,
        title=f"✏️ {date_str} 진폭 업데이트 완료",
        webhook_env_var=WEBHOOK_TRACKER_ENV_VAR,
    )


def alert_economic_recorded(date_str: str, note: str, names: list = None, detail: str = None,
                             event_time: str = None):
    """경제발표 전(5분전)/후(5분후) 진폭이 시트에 기록됐을 때 #futures-tracker로 실시간 알림.
    (2026-08-26부터 '10분전' 예고는 이 함수가 아니라 alert_reminder_tier()가 전담함 - 시트 기록과 분리)
    event_time: 그 발표의 실제 예정 시각 HH:MM (예: '21:30')
    detail: 종목별 진폭 값 + 직전 기록 대비 증감을 정리한 문자열 (gh_actions_poll.py에서 생성)"""
    name_str = ", ".join(names) if names else "발표"
    is_post = note.endswith("_후")
    offset_label = "5분 후" if is_post else "5분 전"
    emoji = "🫧" if is_post else "🔥"
    time_part = f"{event_time} " if event_time else ""
    message = f"[{time_part}{name_str} 발표 {offset_label}{emoji}] 진폭 기록 완료!"
    if detail:
        message += f"\n{detail}"
    title = f"✏️ {date_str} 경제 발표 {'후' if is_post else '전'} 진폭 업데이트 완료"
    send_alert(
        message,
        title=title,
        webhook_env_var=WEBHOOK_TRACKER_ENV_VAR,
    )


def alert_reminder_tier(date_str: str, tier_label: str, names: list, event_time: str):
    """중요 경제발표 임박 예고 (10분 전 / 5분 전 / 1분 전) - #economic-presentation 전용.
    시트에는 아무것도 기록하지 않는 순수 알림 (2026-08-26 신규).
    같은 시각에 겹치는 지표는 names에 전부 담겨서 한 번에 나감."""
    lines = "\n".join(f"{event_time}\t{name}" for name in (names or []))
    message = f"\n{lines}"
    send_alert(
        message,
        title=f"🚨{date_str} 경제 발표 `{tier_label}` 예고🚨",
        webhook_env_var=WEBHOOK_ECONOMIC_ENV_VAR,
    )


def alert_daily_digest(date_str: str, events: list):
    """매일 낮 3시, 오늘 예정된 경제발표 전체(중요도 필터 없음) 목록 안내 - #economic-presentation 전용.
    시트에는 기록하지 않는 순수 안내 알림 (2026-08-26 신규).
    events: [{"time": "21:30", "name": "..."}, ...] (시간순 정렬된 상태로 전달받음)"""
    lines = "\n".join(f"{e['time']}\t{e['name']}" for e in events)
    send_alert(
        lines,
        title=f"🔥 {date_str} 오늘의 경제 발표🔥",
        webhook_env_var=WEBHOOK_ECONOMIC_ENV_VAR,
    )


def alert_pre_post_comparison(date_str: str, event_time: str, name_str: str, comparison: str):
    """경제발표 20분 후, 발표 전(-5분) 대비 진폭이 얼마나 움직였는지 한눈에 보는 비교 알림
    - #economic-presentation 전용. 시트에는 기록하지 않는 순수 알림 (2026-08-26 신규).
    comparison: '{종목} {전값} > {후값} (▲증감)' 형태 줄들을 이미 만들어서 전달받음"""
    message = f"[{event_time} {name_str} 발표 기준]\n{comparison}"
    send_alert(
        message,
        title=f"📊 {date_str} 경제 발표 전/후 진폭 비교",
        webhook_env_var=WEBHOOK_ECONOMIC_ENV_VAR,
    )


# ─────────────────────────────────────────────────────────────
# 2026-08-26 추가: 아시아장중 체크포인트 / 하루 마감 요약 / 완전휴장일 안내 / 연말 캘린더 리마인더
# ─────────────────────────────────────────────────────────────

def alert_daily_summary(date_str: str, comparison: str):
    """하루 전체(하루 첫 체크포인트 ~ 미장후) 진폭 요약 - 미장후 기록 직후 #futures-tracker로 발송.
    2026-08-31: 아시아장초반(09:00) 추가로 하루 첫 체크포인트가 아시아장중(12:00)에서
    아시아장초반(09:00)으로 당겨져서, 문구도 특정 체크포인트 이름을 박지 않고 일반화함.
    comparison: format_ticks_comparison()으로 만든 '{종목} {시작값} > {마감값} (증감)' 줄들"""
    message = f"⏰ 전일({date_str}) 진폭 결과\n하루 첫 체크(아시아장초반) 대비 미장마감, 최종 진폭이에요!\n{comparison}"
    send_alert(message, webhook_env_var=WEBHOOK_TRACKER_ENV_VAR)


# 완전휴장일 안내에 곁들이는 휴일별 인사말 (굿프라이데이는 축하 성격이 아니라 인사말 없음)
HOLIDAY_GREETINGS = {
    "신정": "Happy New Year 🎉🥳",
    "추수감사절": "Happy Thanksgiving 🦃🍂",
    "크리스마스": "Merry Christmas 🎅🏻🎄",
}


def alert_full_holiday_today(date_str: str, holiday_name: str):
    """완전휴장일 당일, 아시아장중 시점에 1회 - #futures-tracker + #trading-notify 동시발송."""
    greeting = HOLIDAY_GREETINGS.get(holiday_name)
    greeting_line = f"\n{greeting}" if greeting else ""
    send_alert(
        f"🎌 [휴장안내] 오늘({date_str})은 '{holiday_name}'로 미국 휴장일이라 진폭 기록을 쉬어갑니다!{greeting_line}",
        webhook_env_var=WEBHOOK_TRACKER_ENV_VAR,
    )
    send_alert(
        f"🎌 [휴장안내] 오늘({date_str})은 '{holiday_name}'로 미국 휴장일이니 참고 해주세요!",
        webhook_env_var=WEBHOOK_ENV_VAR,
    )


def alert_full_holiday_tomorrow(date_str: str, holiday_name: str):
    """완전휴장일 전날, 미장후(+하루 마감 요약) 알림 이후 - #trading-notify로 예고."""
    greeting = HOLIDAY_GREETINGS.get(holiday_name)
    greeting_line = f"\n{greeting}" if greeting else ""
    send_alert(
        f"🎌 [휴장안내] 내일({date_str})은 '{holiday_name}'로 미국 휴장일이라 진폭 기록이 없어요!{greeting_line}",
        webhook_env_var=WEBHOOK_ENV_VAR,
    )


def alert_unexpected_no_trading(date_str: str, timing: str):
    """하드코딩된 완전휴장일 목록에는 없는데 7개 종목 전부 무변동(고=저)으로 감지된 경우 -
    예상 못한 휴장/데이터 문제일 수 있어 확인 요망 알림 (#trading-notify, 데이터 기반 백업 감지)."""
    send_alert(
        f"`{timing}` 시점에 7개 종목 전부 무변동(고=저)으로 감지돼서 기록을 스킵했어요.\n"
        f"휴장일 목록에 없는 날인데 이렇게 나온 거라, 예상 못한 휴장이거나 데이터 문제일 수 있어요 - 확인해주세요!",
        title=f"🚨 {date_str} 전종목 무변동 감지",
        webhook_env_var=WEBHOOK_ENV_VAR,
    )


def alert_early_close_today(date_str: str, name: str, detail: str):
    """조기종료일 당일, 하루 첫 체크포인트 시점에 1회 - #trading-notify 로 참고용 안내만 발송.
    완전휴장일과 달리 진폭 기록/체크포인트는 평소처럼 정상 진행됨 (기록 로직 변경 없음) - 2026-09-01 신규.
    2026-09-01 재인님 요청: '조기종료 안내'(당일)는 볼드 유지 - title 파라미터 그대로 사용
    (send_slack_alert가 title을 자동으로 *볼드* 처리함). 이모지는 ⏰(다른 알림에서 이미 사용 중)와
    구분되도록 ‼️로 통일 (재인님 재요청, 예고/안내 둘 다 동일 이모지)."""
    send_alert(
        f"오늘({date_str})은 '{name}'로 일부 상품 조기종료가 있는 날이에요!\n{detail}\n"
        f"※ 진폭 기록은 평소처럼 정상 진행돼요 - 참고만 해주세요 🙏",
        title="‼️ [조기종료 안내]‼️",
        webhook_env_var=WEBHOOK_ENV_VAR,
    )


def alert_early_close_tomorrow(date_str: str, name: str, detail: str):
    """조기종료일 전날, 미장후(+하루 마감 요약) 알림 이후 - #trading-notify 로 예고.
    2026-09-01 신규. 2026-09-01 재인님 요청: '조기종료 예고'(전날)는 볼드 없이 -
    title 파라미터를 쓰면 send_slack_alert가 자동으로 *볼드* 처리하므로,
    title을 안 쓰고 첫 줄에 직접 넣어서 일반 텍스트로 보냄. 이모지는 ⏰(다른 알림에서 이미
    사용 중)와 구분되도록 ‼️로 통일 (재인님 재요청, 예고/안내 둘 다 동일 이모지)."""
    send_alert(
        f"‼️ [조기종료 예고]‼️\n"
        f"내일({date_str})은 '{name}'로 일부 상품 조기종료가 있는 날이에요!\n{detail}\n"
        f"※ 진폭 기록은 평소처럼 정상 진행돼요 - 참고만 해주세요 🙏",
        webhook_env_var=WEBHOOK_ENV_VAR,
    )


def alert_holiday_calendar_reminder(tier: str):
    """연말 CME 다음해 휴장일 캘린더 갱신 리마인더 - #trading-notify, 12월 초/중순/말 3회.
    tier: 'early' | 'mid' | 'final'"""
    from datetime import datetime as _dt
    next_year = _dt.now().year + 1
    if tier == "early":
        title = f"🔧 [운영 알림] {next_year}년 CME 휴장일 캘린더 업데이트 필요"
        message = (
            "재인님, 아래 링크에서 확인 후 코드에 반영해주세요 🙏\n"
            "https://www.cmegroup.com/tools-information/holiday-calendar.html"
        )
    elif tier == "mid":
        title = f"🔧 [운영 알림-리마인드] {next_year}년 휴장일 반영 확인"
        message = f"재인님, {next_year}년 휴장일 코드 반영 했는지 체크해주세요 🙏"
    else:  # final
        title = f"🔧 [운영 알림-🚨최종리마인드🚨] {next_year}년 휴장일 반영 마감 체크"
        message = f"재인님, {next_year}년 휴장일 코드 반영 완료됐는지, 날짜 오류는 없는지 마지막으로 꼭 확인해주세요 🙏"
    send_alert(message, title=title, webhook_env_var=WEBHOOK_ENV_VAR)
