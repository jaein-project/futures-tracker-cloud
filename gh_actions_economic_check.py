"""
GitHub Actions 전용: 5분마다 실행되어, 지금이 오늘 중요 경제발표 5분 전/후 타이밍이면 기록
- 이미 기록된 건 중복 기록 안 함 (google_sheet.is_duplicate 로직 재사용)

사용법:
  python gh_actions_economic_check.py
"""

import pytz
from datetime import datetime

from economic_calendar import get_today_event_groups
from yahoo_data import get_all_symbols
from google_sheet import record_data_with_note

KST = pytz.timezone("Asia/Seoul")

# GitHub Actions cron은 최소 5분 간격이고 실제 실행이 몇 분 늦어질 수 있어
# 목표 시각 기준 앞뒤로 이 정도 여유를 두고 "지금이 그 타이밍이다"로 판단
TOLERANCE_MINUTES = 4


def main():
    now = datetime.now(KST)
    print(f"🔍 경제발표 체크 - {now.strftime('%Y-%m-%d %H:%M:%S')} KST")

    groups = get_today_event_groups()
    if not groups:
        print("   ℹ️ 오늘 대상 없음")
        return

    to_record = []
    for g in groups:
        if abs((now - g["before_dt"]).total_seconds()) <= TOLERANCE_MINUTES * 60:
            to_record.append(("전", g["label_pre"]))
        if abs((now - g["after_dt"]).total_seconds()) <= TOLERANCE_MINUTES * 60:
            to_record.append(("후", g["label_post"]))

    if not to_record:
        print("   ℹ️ 지금은 기록 타이밍 아님")
        return

    for kind, note in to_record:
        print(f"📌 [{kind}] 기록 시도: {note}")
        data = get_all_symbols()
        record_data_with_note(data, note)  # 내부적으로 중복이면 자동 스킵


if __name__ == "__main__":
    main()
