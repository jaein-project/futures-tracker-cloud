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
    (2번은 우연히 있을 수 있어서 정상 범위로 보고, 3번 이상부터만 알림 - 2026-08-26 기준 변경)"""
    send_alert(
        f"`{name}` 값이 {streak}번 연속 동일한 값으로 기록되고 있어요. \n"
        f"월물 만기 혹은 데이터 소스에 문제가 있을 수 있어요. 확인 해주세요!\n"
        f"➡️ 동일한 진폭 : {value}",
        title="🚨진폭 값 반복 감지🚨",
    )


# 체크포인트 내부 키(스케줄 계산용) → 실제 알림 문구에 쓰는 표시명
# 아시아/유럽은 원래 이름에 '장'이 없어서 추가, 미장전/미장후는 이미 '미(국)장'이 들어있어 그대로 둠
CHECKPOINT_DISPLAY_NAMES = {
    "아시아마감전": "아시아장마감전",
    "유럽개장전": "유럽장개장전",
    "미장전": "미장전",
    "미장후": "미장후",
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
