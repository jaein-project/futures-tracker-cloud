import sys
sys.path.insert(0, ".")
from datetime import datetime
import pytz
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME
from gh_actions_poll import format_ticks_detail, format_ticks_comparison, time_str_from, SUMMER_SCHEDULE, WINTER_SCHEDULE

KST = pytz.timezone("Asia/Seoul")
client = get_client()
ss = client.open_by_key(SPREADSHEET_ID)
ws = ss.worksheet(SHEET_NAME)
all_values = ws.get_all_values()

TARGET_DATES = ["2026. 8. 31", "2026. 8. 28"]
sched = SUMMER_SCHEDULE  # 8/28, 8/31 모두 서머타임 기간

for target_date_str in TARGET_DATES:
    print(f"\n\n===== {target_date_str} (새 로직 시뮬레이션) =====")
    y, m, d = [int(x.strip()) for x in target_date_str.replace(".", " ").split()]
    sched_keys = list(sched.keys())

    idxs = [i for i, r in enumerate(all_values) if len(r) > 1 and r[1].strip() == target_date_str]
    checkpoint_idxs = [i for i in idxs if not (len(all_values[i]) > 10 and all_values[i][10].strip())]

    for i in checkpoint_idxs:
        r = all_values[i]
        cur_time_str = r[2].strip()
        # 이 행이 스케줄의 몇 번째 타이밍인지 역으로 찾기
        cur_timing = None
        for tname, hhmm in sched.items():
            th, tm = map(int, hhmm.split(":"))
            if time_str_from(KST.localize(datetime(y, m, d, th, tm))) == cur_time_str:
                cur_timing = tname
                break
        if cur_timing is None:
            print(f"row[{i}] time={cur_time_str} -> 스케줄에 매칭 안됨(구 스케줄 시절 데이터), 스킵")
            continue
        cur_idx = sched_keys.index(cur_timing)
        prev_row = None
        if cur_idx > 0:
            prev_timing = sched_keys[cur_idx - 1]
            ph, pm = map(int, sched[prev_timing].split(":"))
            prev_time_str = time_str_from(KST.localize(datetime(y, m, d, ph, pm)))
            for rr in all_values[2:]:
                if len(rr) > 10 and rr[10].strip():
                    continue
                if len(rr) > 1 and rr[1].strip() == target_date_str and len(rr) > 2 and rr[2].strip() == prev_time_str:
                    prev_row = rr
                    break
        local_row = [r[1], r[2]] + r[3:10] + [r[10] if len(r) > 10 else ""]
        detail = format_ticks_detail(local_row, prev_row)
        print(f"\n--- [{cur_timing}] row[{i}] time={cur_time_str} (prev_row {'있음:' + prev_row[2] if prev_row else '없음(하루의 첫 체크포인트)'}) ---")
        print(detail)

    # 미장후 daily summary용 first_row 시뮬레이션
    day_rows = [r for r in all_values if len(r) > 1 and r[1].strip() == target_date_str]
    if len(day_rows) >= 2:
        first_timing, first_hhmm = next(iter(sched.items()))
        fh, fm = map(int, first_hhmm.split(":"))
        first_time_str = time_str_from(KST.localize(datetime(y, m, d, fh, fm)))
        first_row = next((r for r in day_rows if len(r) > 2 and r[2].strip() == first_time_str), day_rows[0])
        print(f"\n[새 로직] 미장마감 비교 기준 first_row = time={first_row[2]!r} (스케줄 첫 타이밍={first_timing}, 기대 time_str={first_time_str!r}) {'-> 정확히 매칭됨' if first_row[2].strip()==first_time_str else '-> 매칭 실패, 배열 첫 행으로 폴백됨'}")
