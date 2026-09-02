"""임시 진단 스크립트 (재인님 요청 - 8월 경제발표 재업로드 후 이상 현상 확인용)
'경제발표' 탭 전체를 훑어서:
1) 실제 컬럼 구조(각 셀 raw 값)를 있는 그대로 출력 - 추측하지 않고 확인용
2) 완전히 빈 행(공백 gap)이 어디 있는지
3) '2026/08' 로 시작하는 날짜가 있는 행이 몇 번인지, 어디 몰려있는지
사용 후 삭제 예정 (dump_recent_temp.py 등과 동일한 패턴).
"""
from google_sheet import get_client

SPREADSHEET_ID = "1XJAcEoUpCUs63VzhebyuXaBBeuNLSs7KAeqbPq-EA_0"
SHEET_NAME = "경제발표"


def main():
    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.worksheet(SHEET_NAME)
    all_values = ws.get_all_values()
    total = len(all_values)
    print(f"전체 행 개수 (get_all_values 기준): {total}")
    print(f"시트 grid 크기: rows={ws.row_count}, cols={ws.col_count}")

    print("\n[헤더 + 상위 25행 RAW 전체 셀 출력] (행번호: [셀0, 셀1, 셀2, ...])")
    for i, row in enumerate(all_values[:25], start=1):
        print(f"  {i:4d}: {row}")

    # 완전 공백 행 구간 탐지
    blank_ranges = []
    cur_start = None
    for i, row in enumerate(all_values, start=1):
        is_blank = all((c or "").strip() == "" for c in row)
        if is_blank and cur_start is None:
            cur_start = i
        elif not is_blank and cur_start is not None:
            blank_ranges.append((cur_start, i - 1))
            cur_start = None
    if cur_start is not None:
        blank_ranges.append((cur_start, total))

    print(f"\n완전 공백 행 구간 개수: {len(blank_ranges)}")
    for s, e in blank_ranges[:30]:
        print(f"  - {s}행 ~ {e}행 (총 {e - s + 1}행)")

    print(f"\n[하위 15행 RAW] (전체 {total}행 기준)")
    start_idx = max(0, total - 15)
    for i, row in enumerate(all_values[start_idx:], start=start_idx + 1):
        print(f"  {i:4d}: {row}")

    # '2026/08'을 포함하는 셀이 있는 행 전부 찾기 (컬럼 위치 추측 없이 행 전체에서 검색)
    aug_rows = []
    for i, row in enumerate(all_values, start=1):
        joined = "|".join(row)
        if "2026/08" in joined or "2026-08" in joined:
            aug_rows.append(i)
    print(f"\n'2026/08' 문자열을 포함한 행: 총 {len(aug_rows)}개")
    if aug_rows:
        print(f"  최소 행번호: {min(aug_rows)}, 최대 행번호: {max(aug_rows)}")
        print(f"  행번호 목록: {aug_rows}")

    # '2026/01'을 포함하는 행도 확인 (스크린샷에서 8월 바로 아래 1월 데이터가 보였다고 함)
    jan_rows = []
    for i, row in enumerate(all_values, start=1):
        joined = "|".join(row)
        if "2026/01" in joined:
            jan_rows.append(i)
    print(f"\n'2026/01' 문자열을 포함한 행: 총 {len(jan_rows)}개")
    if jan_rows:
        print(f"  최소 행번호: {min(jan_rows)}, 최대 행번호: {max(jan_rows)}")
        print(f"  행번호 목록(앞 30개): {jan_rows[:30]}")


if __name__ == "__main__":
    main()
