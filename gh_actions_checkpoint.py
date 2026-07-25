"""
GitHub Actions 전용: 하루 4회 체크포인트 중 하나를 실행하고 즉시 종료
(노트북에서 계속 켜두던 main.py와 달리, 실행-종료 방식)

사용법:
  python gh_actions_checkpoint.py 아시아마감전
  python gh_actions_checkpoint.py 유럽개장전
  python gh_actions_checkpoint.py 미장전
  python gh_actions_checkpoint.py 미장후

서머타임/겨울시간 이중 스케줄 대응:
  미장전/미장후는 cron이 서머타임용, 겨울시간용 시각 둘 다 등록돼있고,
  이 스크립트가 실행 시점에 실제 서머타임 여부를 확인해서
  해당 안 되는 시각에 잘못 실행됐으면 아무것도 안 하고 조용히 종료함
  (예: 겨울시간 스케줄로 등록된 23:25 트리거가 서머타임 기간에 발동돼도 무시)
"""

import sys
import pytz
from datetime import datetime

from yahoo_data import get_all_symbols
from google_sheet import record_data

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


def is_summer_time():
    eastern = pytz.timezone("America/New_York")
    return bool(datetime.now(eastern).dst())


def is_correct_schedule_now(timing: str, tolerance_minutes: int = 20) -> bool:
    """지금이 이 체크포인트를 실제로 기록해야 할 타이밍이 맞는지 확인
    (서머/겨울 이중 cron 등록 시, 잘못된 쪽 트리거를 걸러내기 위함)
    """
    sched = SUMMER_SCHEDULE if is_summer_time() else WINTER_SCHEDULE
    target_str = sched.get(timing)
    if not target_str:
        return False

    kst = pytz.timezone("Asia/Seoul")
    now = datetime.now(kst)
    th, tm = map(int, target_str.split(":"))
    target_minutes = th * 60 + tm
    now_minutes = now.hour * 60 + now.minute
    diff = min(abs(now_minutes - target_minutes), 1440 - abs(now_minutes - target_minutes))
    return diff <= tolerance_minutes


def main():
    if len(sys.argv) < 2:
        print("사용법: python gh_actions_checkpoint.py [아시아마감전|유럽개장전|미장전|미장후]")
        sys.exit(1)

    timing = sys.argv[1]

    kst = pytz.timezone("Asia/Seoul")
    now = datetime.now(kst)

    # 주말 스킵 (미장후는 토요일 새벽=금요일 밤 마감이라 예외)
    if timing == "미장후":
        if now.weekday() == 6:  # 일요일만 스킵
            print(f"⏭️ [{timing}] 일요일이라 스킵")
            return
    else:
        if now.weekday() >= 5:
            print(f"⏭️ [{timing}] 주말이라 스킵")
            return

    # 서머/겨울 이중 스케줄 중 지금이 맞는 타이밍인지 확인
    if not is_correct_schedule_now(timing):
        print(f"⏭️ [{timing}] 지금은 이 체크포인트 실행 시각이 아님 (서머/겨울 스케줄 불일치) - 스킵")
        return

    print(f"🚀 [{timing}] 데이터 수집 시작 - {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
    data = get_all_symbols()
    record_data(data, timing)


if __name__ == "__main__":
    main()
