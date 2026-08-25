"""
Slack 알림 모듈
================

GitHub Actions 워크플로우 자체는 "성공"으로 끝나더라도, 특정 종목만 데이터가
비어있거나(None) 월물 자동계산 결과가 이상해 보이는 경우가 있을 수 있습니다.
그런 "조용한 실패"를 잡아서 Slack으로 알려주는 역할을 합니다.

사용 전 준비:
  1) Slack에서 Incoming Webhook URL 발급 (설정 방법은 별도 안내 참고)
  2) GitHub 저장소 Settings > Secrets and variables > Actions 에
     이름 SLACK_WEBHOOK_URL 로 등록
  3) 워크플로우 yml에서 해당 시크릿을 환경변수로 넘겨주기:
       env:
         SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}

로컬(내 컴퓨터)에서 테스트할 때는 터미널에서:
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
    python alerts.py   # 테스트 메시지 발송
"""

import os
import requests

WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"
NTFY_TOPIC_ENV_VAR = "NTFY_TOPIC"


def send_ntfy_alert(message: str, title: str = None) -> bool:
    """ntfy.sh 앱으로 실시간 폰 푸시 알림 전송 (Slack과 별도로 당분간 병행 사용).
    NTFY_TOPIC 환경변수가 없으면 조용히 스킵합니다 (에러 아님).
    """
    topic = os.environ.get(NTFY_TOPIC_ENV_VAR)
    if not topic:
        return False
    headers = {"Title": title.encode("utf-8")} if title else {}
    try:
        res = requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ ntfy 전송 중 오류: {e}")
        return False


def send_alert(message: str, title: str = None):
    """Slack + ntfy 둘 다로 전송 (둘 중 하나만 설정돼 있어도 동작)"""
    send_slack_alert(message, title)
    send_ntfy_alert(message, title)


def send_slack_alert(message: str, title: str = None) -> bool:
    """Slack으로 알림 메시지 전송. 웹훅 URL이 설정 안 돼있으면 콘솔에만 출력.
    반환값: 실제로 Slack 전송에 성공했으면 True
    """
    webhook_url = os.environ.get(WEBHOOK_ENV_VAR)

    text = f"*{title}*\n{message}" if title else message

    if not webhook_url:
        print(f"⚠️ [{WEBHOOK_ENV_VAR}] 환경변수가 없어서 Slack 대신 콘솔에만 출력합니다:")
        print(text)
        return False

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
    """같은 값이 연속으로 여러 번 기록됐을 때 (월물 만기 임박/데이터 정체 의심)"""
    send_alert(
        f"`{name}` 값이 {streak}번 연속 똑같이({value}) 기록되고 있어요. "
        f"월물 만기가 다가와서 거래가 뜸해졌거나, 데이터 소스에 문제가 있을 수 있어요. 확인해주세요.",
        title="🔁 진폭 값 반복 감지",
    )


def alert_economic_recorded(date_str: str, note: str, names: list = None):
    """경제발표(전/후) 진폭이 시트에 기록됐을 때 실시간 알림"""
    name_str = ", ".join(names) if names else ""
    send_alert(
        f"`{date_str}` `{note}` 진폭이 기록됐어요." + (f"\n대상 지표: {name_str}" if name_str else ""),
        title="📢 경제발표 진폭 기록",
    )
