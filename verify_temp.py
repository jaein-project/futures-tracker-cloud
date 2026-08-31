import sys
sys.path.insert(0, ".")
from google_sheet import get_client, SPREADSHEET_ID, SHEET_NAME
from gh_actions_poll import format_ticks_detail, format_ticks_comparison

client = get_client()
ss = client.open_by_key(SPREADSHEET_ID)
ws = ss.worksheet(SHEET_NAME)
all_values = ws.get_all_values()

TARGET_DATES = ["2026. 8. 31", "2026. 8. 28"]

for target_date_str in TARGET_DATES:
    print(f"\n\n===== {target_date_str} =====")
    idxs = [i for i, r in enumerate(all_values) if len(r) > 1 and r[1].strip() == target_date_str]
    print(f"이 날짜로 기록된 행 인덱스: {idxs}")
    for i in idxs:
        r = all_values[i]
        note = r[10].strip() if len(r) > 10 else ""
        print(f"  row[{i}] time={r[2]!r} note={note!r} ticks={r[3:10]}")

    # 1) process_checkpoints의 prev_row 탐색 로직을 그대로 재현해서, 체크포인트 행마다
    #    실제로 어떤 Slack 알림 문구(화살표 포함/미포함)가 나갔을지 재구성
    checkpoint_idxs = [i for i in idxs if not (len(all_values[i]) > 10 and all_values[i][10].strip())]
    print(f"\n순수 체크포인트 행(경제발표 제외): {checkpoint_idxs}")
    for i in checkpoint_idxs:
        r = all_values[i]
        prev_row = None
        for j in range(i - 1, 1, -1):
            rr = all_values[j]
            if len(rr) > 10 and rr[10].strip():
                continue
            if len(rr) > 1 and rr[1].strip() == target_date_str:
                prev_row = rr
            break
        local_row = [r[1], r[2]] + r[3:10] + [r[10] if len(r) > 10 else ""]
        detail = format_ticks_detail(local_row, prev_row)
        print(f"\n--- row[{i}] time={r[2]} (prev_row {'있음:' + prev_row[2] if prev_row else '없음(날짜경계)'}) ---")
        print(detail)

    # 2) alert_daily_summary가 실제로 쓰는 day_rows[0] 로직 재현 (현재 코드: 경제발표 행 필터링 없음)
    day_rows_current = [r for r in all_values if len(r) > 1 and r[1].strip() == target_date_str]
    if day_rows_current:
        fr = day_rows_current[0]
        note0 = fr[10].strip() if len(fr) > 10 else ""
        print(f"\n[현재 코드 기준] day_rows[0] = time={fr[2]!r} note={note0!r} {'<- 경제발표 행! 버그 가능성' if note0 else '<- 체크포인트 행, 정상'}")

    # 3) 경제발표 행을 제외하고 골랐을 때(제안하는 수정)의 day_rows[0]
    if checkpoint_idxs:
        fr2 = all_values[checkpoint_idxs[0]]
        print(f"[체크포인트만 필터링 시] day_rows[0] = time={fr2[2]!r} (index {checkpoint_idxs[0]})")
