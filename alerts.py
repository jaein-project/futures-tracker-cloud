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

2026-09-04 추가: 반복감지 / 롤오버 / 워크플로우 오류 - 봇 더블체크(이모지 반응 + 스레드 답변) 기능
──────────────────────────────────────────────────────────────────────────────
재인님이 수동으로 하던 "이모지 체크 + 스레드 답글"을 봇이 자동으로 대신 해주는 기능. 이 3개
알림만 웹훅이 아니라 Slack Bot Token(Web API, chat.postMessage)으로 보내서 메시지 ts(고유
식별자)를 확보해야 나중에 그 메시지에 이모지/스레드 답변을 달 수 있음 (웹훅은 ts를 안 줘서
불가능). 그 외 알림들(체크포인트 기록, 경제발표 등)은 기존 웹훅 방식 그대로 유지.

준비:
  1) Slack 앱 OAuth & Permissions에서 Bot Token Scopes에 chat:write, reactions:write 추가 후
     워크스페이스에 설치 → Bot User OAuth Token(xoxb-...) 발급
  2) GitHub 저장소 Settings > Secrets and variables > Actions 에 SLACK_BOT_TOKEN 이름으로 등록
  3) 봇을 #trading-notify 채널에 초대 (/invite @봇이름)
  4) 워크플로우 yml env에 SLACK_BOT_TOKEN 추가

봇 토큰이 없거나 API 호출이 실패하면 자동으로 기존 웹훅 방식(send_alert)으로 대체 발송되므로,
이 3개 알림 자체가 안 오는 일은 없음 - 다만 그 경우엔 이모지/스레드 더블체크만 그 알림에 한해
동작하지 않음 (ts를 못 받아서).
"""

import os
import requests

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"                    # #trading-notify (문제/에러)
WEBHOOK_ECONOMIC_ENV_VAR = "SLACK_WEBHOOK_URL_ECONOMIC"   # #economic-presentation (경제발표 예고)
WEBHOOK_TRACKER_ENV_VAR = "SLACK_WEBHOOK_URL_TRACKER"     # #futures-tracker (진폭 기록)
NTFY_TOPIC_ENV_VAR = "NTFY_TOPIC"

BOT_TOKEN_ENV_VAR = "SLACK_BOT_TOKEN"
TRADING_NOTIFY_CHANNEL_ID = "C0BS0HENLJ1"  # #trading-notify 채널 ID - 반복감지/롤오버/워크플로우 오류 더블체크 전용
FUTURES_TRACKER_CHANNEL_ID = "C0BTBE5EJM6"  # #futures-tracker 채널 ID - 2026-09-04 추가:
                                             # 반복감지 재검증 보정 결과를 체크포인트 메시지 스레드에 달기 위해 필요
JAEIN_SLACK_USER_ID = "U0BRP2C3PMM"  # 재인님 개인 Slack 계정 - 롤오버 데이터 이상 시 태그용


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


def _slack_api_post(method: str, payload: dict) -> dict:
    """Slack Web API(Bot Token) 호출 공통 함수 - 실패해도 예외를 던지지 않고
    {"ok": False, ...} 형태를 반환함 (호출부에서 항상 안전하게 .get()으로 처리 가능)."""
    token = _first_line(os.environ.get(BOT_TOKEN_ENV_VAR))
    if not token:
        return {"ok": False, "error": "no_bot_token"}
    try:
        res = requests.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        data = res.json()
        if not data.get("ok"):
            print(f"❌ Slack API({method}) 실패: {data.get('error')}")
        return data
    except Exception as e:
        print(f"❌ Slack API({method}) 호출 중 오류: {e}")
        return {"ok": False, "error": str(e)}


def post_bot_alert(message: str, title: str = None, channel: str = TRADING_NOTIFY_CHANNEL_ID,
                    fallback_webhook_env_var: str = WEBHOOK_ENV_VAR) -> dict:
    """봇 토큰(Web API)으로 메시지를 보내서 메시지 ts를 확보함 (나중에 이모지/스레드 답변을
    달 때 필요 - 반복감지/롤오버/워크플로우 오류/체크포인트 기록 알림 전용, 2026-09-04 신규).
    반환값: {"ts": ..., "channel": ...} - 봇 토큰이 없거나 실패하면 기존 웹훅 방식(send_alert)으로
    자동 대체 발송하고 {"ts": None, "channel": None}을 반환함 (알림 자체는 정상 발송됨,
    다만 이번 알림에 한해 이후 이모지/스레드 더블체크는 동작 안 함).
    fallback_webhook_env_var: 봇 전송 실패 시 대체할 웹훅 채널 - 채널마다 다르므로(기본은
    #trading-notify) alert_checkpoint_recorded 등 다른 채널용 호출부에서 지정 (2026-09-04 추가)."""
    text = f"*{title}*\n{message}" if title else message
    result = _slack_api_post("chat.postMessage", {"channel": channel, "text": text})
    if result.get("ok"):
        send_ntfy_alert(message, title)
        return {"ts": result.get("ts"), "channel": result.get("channel", channel)}
    print("   ℹ️ 봇 토큰 전송 실패/미설정 - 웹훅 방식으로 대체 발송")
    send_alert(message, title=title, webhook_env_var=fallback_webhook_env_var)
    return {"ts": None, "channel": None}


def add_reaction(channel: str, ts: str, emoji: str) -> bool:
    """메시지에 이모지 반응 추가 (channel/ts가 없으면 - 웹훅 대체 발송된 경우 등 - 조용히 스킵)"""
    if not channel or not ts:
        return False
    result = _slack_api_post("reactions.add", {"channel": channel, "timestamp": ts, "name": emoji})
    return bool(result.get("ok"))


def reply_in_thread(channel: str, ts: str, message: str) -> bool:
    """메시지에 스레드 답글 추가 (channel/ts가 없으면 조용히 스킵)"""
    if not channel or not ts:
        return False
    result = _slack_api_post("chat.postMessage", {"channel": channel, "thread_ts": ts, "text": message})
    return bool(result.get("ok"))


def alert_symbol_missing(name: str, symbol: str):
    """특정 종목 데이터가 비어서(None) 왔을 때"""
    send_alert(
        f"`{name}` ({symbol}) 데이터를 못 가져왔어요. 월물 코드가 만기 지났거나, "
        f"Yahoo Finance 쪽 문제일 수 있어요. 확인해주세요.",
        title="⚠️ 진폭 데이터 누락",
    )


def alert_workflow_exception(context: str, error: Exception):
    """예외가 발생해서 워크플로우가 실패했을 때
    2026-09-04 수정: 다음 폴링이 정상 완료되면 자동으로 '복구됐다'고 이모지+스레드 답변을
    달아주는 기능을 위해 봇 토큰(Web API)으로 전송하고 ts/channel을 반환하도록 변경
    (반환값은 process_workflow_recovery 관련 등록에 사용됨, google_sheet.register_workflow_error 참고)."""
    return post_bot_alert(
        f"`{context}` 실행 중 오류가 발생했어요:\n```{error}```",
        title="❌ 워크플로우 오류",
    )


def alert_symbol_rolled(name: str, old_symbol: str, new_symbol: str):
    """월물이 자동으로 롤오버됐을 때 - 확인용 알림 (원치 않으면 호출 안 해도 됨)
    2026-09-04 수정: 10분 뒤 새 월물 데이터가 정상적으로 들어오는지 봇이 확인해서 스레드로
    답변해주는 기능을 위해 봇 토큰(Web API)으로 전송하고 ts/channel을 반환하도록 변경."""
    return post_bot_alert(
        f"`{name}` 월물이 자동으로 바뀌었어요: `{old_symbol}` → `{new_symbol}`\n"
        f"영웅문/하나 HTS와 한 번 비교해서 맞는지 확인해주세요.",
        title="🔄 월물 자동 롤오버",
    )


def alert_duplicate_streak(name, value, streak):
    """같은 값이 3번 이상 연속으로 기록됐을 때 (월물 만기 임박/데이터 정체 의심)
    (2번은 우연히 있을 수 있어서 정상 범위로 보고, 3번 이상부터만 알림 - 2026-08-26 기준 변경)
    2026-09-02 추가: 이 알림이 뜬 체크포인트는 10분 뒤 자동으로 한 번 더 재검증됨.
    2026-09-02 멘트 확정 (재인님 승인): "⏳ 10분 후 재검증 예정 (오류 확인 시 별도 알림)" 문구로 최종 확정.
    (문구상 "별도 알림"이라 되어 있지만 2026-09-04부터는 값이 다르면 별도 알림 대신
    #futures-tracker 체크포인트 메시지 스레드에 보정 내용이 달림 - 문구 자체는 재인님이
    이미 승인한 표현이라 그대로 유지, 실제 동작만 변경됨.)
    2026-09-04 수정: 재검증 결과(일치/보정)를 이 메시지에 이모지+스레드로 달아주는 기능을 위해
    봇 토큰(Web API)으로 전송하고 ts/channel을 반환하도록 변경 (register_streak_recheck에 전달됨)."""
    return post_bot_alert(
        f"`{name}` 값이 {streak}번 연속 동일한 값으로 기록되고 있어요.\n"
        f"월물 만기 혹은 데이터 소스에 문제가 있을 수 있으니 확인해주세요!\n"
        f"⏳ 10분 후 재검증 예정 (오류 확인 시 별도 알림)\n"
        f"➡️ 동일한 진폭 : {value}",
        title="🚨진폭 값 반복 감지🚨",
    )


# ─────────────────────────────────────────────────────────────
# 2026-09-04 추가: 반복감지/롤오버/워크플로우 오류 - 봇 더블체크(이모지+스레드 답변) 문구
# 재인님이 수동으로 하던 "이모지 체크 + 스레드 답글"을 봇이 대신 해주는 기능 (재인님 확정 문구).
# 이모지는 전부 🤖(robot_face)로 통일 - 재인님 확정 사유: "어차피 github에서 해주는 거니까
# AI가 더 맞는 거 같네요" (2026-09-04, 기존 👀에서 변경). "재인님이 직접 다 확인했다"는 뜻의
# ✅ 체크는 재인님이 직접 추가하는 것과 구분하기 위해 봇은 항상 🤖만 사용함
# (재인님 요청: "체크는 내가 할게!" - 롤오버는 예외로 봇이 이모지를 안 달고 재인님이 직접 확인).
# ─────────────────────────────────────────────────────────────

RECHECK_EMOJI = "robot_face"  # 🤖 (2026-09-04: 👀에서 변경 - 재인님 확정)

# 반복감지 재검증 - 값이 그대로였을 때(문제 없음) 스레드 답변. 매번 같은 문구면 기계적으로
# 보여서 두 문구를 번갈아 가면서 사용함 (재인님 요청 - 등록 순번의 홀/짝으로 alternate)
STREAK_MATCH_REPLIES = [
    "✅ 재검증 완료 - 값 그대로예요, 이상 없어요!",
    "10분 후 재검증 결과 동일 - 문제 없는 걸로 확인했어요!",
]

ROLLOVER_NORMAL_REPLY = "10분 후 확인 결과, 새 월물 데이터 정상적으로 들어오고 있어요!"

# 워크플로우 오류 복구 확인 - 역시 두 문구를 번갈아 가면서 사용
WORKFLOW_RECOVERED_REPLIES = [
    "다음 폴링에서 정상 처리 확인 - 복구됐어요!",
    "다시 확인해보니 정상 작동 중이에요 - 복구 완료!",
]


def _rollover_abnormal_reply() -> str:
    """롤오버 후 10분이 지나도 새 월물 데이터가 확인 안 될 때 - 재인님 태그 + HTS 확인 요청"""
    return f"<@{JAEIN_SLACK_USER_ID}> 새 월물 데이터 확인 불가하여 HTS에서 확인해주세요!"


def reply_streak_match(channel: str, ts: str, variant_idx: int):
    """반복감지 재검증 결과 값이 동일(문제 없음)했을 때 - 🤖 반응 + 스레드 답변(번갈아가며)"""
    add_reaction(channel, ts, RECHECK_EMOJI)
    reply_in_thread(channel, ts, STREAK_MATCH_REPLIES[variant_idx % len(STREAK_MATCH_REPLIES)])


def reply_streak_correction_ack(channel: str, ts: str):
    """반복감지 재검증 결과 값이 달라서 보정됐을 때 - #trading-notify 원본 반복감지 메시지에는
    🤖 반응만 추가함 (스레드 답글 없음 - 보정 상세 내용은 #futures-tracker의 해당 체크포인트
    메시지 쪽에 reply_checkpoint_correction()으로 달림). 2026-09-04 수정 (재인님 요청) -
    기존에는 여기에도 스레드 답변이 붙고 별도로 "🔧진폭 값 보정 완료🔧" 알림이 새 글로도
    올라갔는데, 재인님이 "정신 사납다"고 판단해서 이 메시지는 이모지만 남기고
    상세 내용은 #futures-tracker 쪽으로 완전히 옮김."""
    add_reaction(channel, ts, RECHECK_EMOJI)


def reply_checkpoint_correction(channel: str, ts: str, name: str, date_str: str, time_str: str,
                                 old_val, new_val):
    """반복감지 재검증 결과 값이 달라서 보정됐을 때 - #futures-tracker의 해당 체크포인트
    ("✏️ {날짜} 진폭 업데이트 완료") 메시지에 🤖 반응 + 스레드로 보정 내용 전체를 답변.
    2026-09-04 신규 (재인님 요청) - 기존에 별도 알림으로 새로 올라가던 "🔧진폭 값 보정 완료🔧"를
    완전히 대체함 (더 이상 새 알림은 안 뜨고 이 스레드 답변으로만 안내됨). 메시지 문구는
    기존 alert_streak_recheck_correction()에서 쓰던 것과 동일(재인님이 이미 승인한 문구라
    자리만 옮기고 내용은 그대로 유지)."""
    add_reaction(channel, ts, RECHECK_EMOJI)
    text = (
        f"*🔧진폭 값 보정 완료🔧*\n"
        f"`{name}` {date_str} {time_str} 재검증 완료!\n"
        f"재검증 결과 값이 달라서 스프레드시트를 수정했어요.\n"
        f"➡️ 기존 {old_val} → 재검증 {new_val}"
    )
    reply_in_thread(channel, ts, text)


def reply_rollover_check(channel: str, ts: str, data_ok: bool):
    """롤오버 10분 후 새 월물 데이터 정상 유입 확인 - 스레드 답변만(이모지 없음, 재인님이
    HTS 확인 후 직접 이모지 추가 예정 - 재인님 요청: "체크는 내가 할게!")"""
    reply_in_thread(channel, ts, ROLLOVER_NORMAL_REPLY if data_ok else _rollover_abnormal_reply())


def reply_workflow_recovered(channel: str, ts: str, variant_idx: int):
    """다음 폴링에서 워크플로우가 예외 없이 정상 완료되어 복구 확인됐을 때 -
    🤖 반응 + 스레드 답변(번갈아가며)"""
    add_reaction(channel, ts, RECHECK_EMOJI)
    reply_in_thread(channel, ts, WORKFLOW_RECOVERED_REPLIES[variant_idx % len(WORKFLOW_RECOVERED_REPLIES)])


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
    detail: 종목별 진폭 값 + 직전 기록 대비 증감을 정리한 문자열 (gh_actions_poll.py에서 생성)
    2026-09-04 수정: 이 체크포인트에서 반복감지 재검증 보정이 발생하면 이 메시지에 🤖 반응 +
    스레드로 보정 내용을 달아주는 기능을 위해 봇 토큰(Web API)으로 전송하고 ts/channel을
    반환하도록 변경 (반환값은 register_streak_recheck의 checkpoint_ts/checkpoint_channel로 전달됨).
    봇 토큰이 없거나 실패하면 기존과 동일하게 #futures-tracker 웹훅으로 대체 발송됨."""
    display_name = CHECKPOINT_DISPLAY_NAMES.get(timing, timing)
    ampm_time = _to_ampm_hhmm(time_hhmm)
    message = f"`{display_name}({ampm_time})` 진폭 기록 완료!"
    if detail:
        message += f"\n{detail}"
    return post_bot_alert(
        message,
        title=f"✏️ {date_str} 진폭 업데이트 완료",
        channel=FUTURES_TRACKER_CHANNEL_ID,
        fallback_webhook_env_var=WEBHOOK_TRACKER_ENV_VAR,
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
