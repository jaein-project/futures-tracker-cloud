"""
'진폭' 스프레드시트 자동 백업 (2026-09-05 신설 - 재인님 요청)
"백업이 부족하다"는 피드백을 받고 만든 이중 백업 장치:

  1) 구글 드라이브 내부 백업: 스프레드시트 전체(모든 탭, 수식, 서식 포함)를
     드라이브 안의 "진폭_백업" 폴더에 날짜가 찍힌 이름으로 통째로 복사.
     구글시트 API의 파일 복사 기능(Drive API files.copy)을 그대로 이용하는
     거라, 원본과 완전히 동일한 사본이 하나 더 생기는 것과 같음.
  2) GitHub 저장소 스냅샷: 같은 시점의 스프레드시트를 xlsx 파일로 통째로
     내보내서(Drive API export) 이 저장소의 backups/ 폴더에 커밋.
     구글 계정 자체에 문제가 생기는 경우(해킹, 정지 등)에도 완전히 다른
     플랫폼(GitHub)에 사본이 남아있게 하기 위함.

두 백업 모두 gspread가 아니라 Drive API를 REST로 직접 호출함 - gspread에는
"폴더 안에 복사"나 "xlsx로 내보내기" 같은 기능이 없어서, google_sheet.py가
이미 갖고 있는 인증 토큰(get_credentials())의 Bearer 토큰을 그대로 재사용해
requests로 직접 호출하는 방식을 씀 (새 패키지 설치 불필요).

오래된 백업이 무한정 쌓이지 않도록 보관 개수를 정해서, 그보다 오래된 것은
자동으로 지움 (드라이브 쪽 DRIVE_KEEP개, GitHub 쪽 REPO_KEEP개).
"""

import os
import time as _time
from datetime import datetime

import requests
import pytz

from google_sheet import get_credentials, SPREADSHEET_ID
from alerts import alert_backup_error

DRIVE_API = "https://www.googleapis.com/drive/v3"
TIMEZONE = "Asia/Seoul"

BACKUP_FOLDER_NAME = "진폭_백업"  # 구글 드라이브 안에 자동으로 만들어질 백업 전용 폴더
DRIVE_KEEP = 30   # 드라이브 쪽 백업 사본은 최근 30개만 유지 (나머지는 자동 삭제)
REPO_KEEP = 90    # GitHub 저장소 backups/ 폴더는 최근 90개(약 3개월치)만 유지

REPO_BACKUP_DIR = "backups"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"


def _headers(creds):
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }


def _retry(fn, what, retry=3):
    for attempt in range(retry):
        try:
            return fn()
        except Exception as e:
            print(f"   ⚠️ {what} 오류 ({attempt + 1}/{retry}): {e}")
            if attempt < retry - 1:
                _time.sleep(5)
            else:
                raise


def _find_or_create_backup_folder(creds):
    """드라이브에서 '진폭_백업' 폴더를 찾고, 없으면 새로 만들어서 폴더 ID를 반환"""
    h = _headers(creds)
    q = f"name = '{BACKUP_FOLDER_NAME}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    resp = requests.get(f"{DRIVE_API}/files", headers=h, params={"q": q, "fields": "files(id, name)"})
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]

    print(f"   📁 '{BACKUP_FOLDER_NAME}' 폴더가 없어서 새로 만듭니다...")
    body = {"name": BACKUP_FOLDER_NAME, "mimeType": FOLDER_MIME}
    resp = requests.post(f"{DRIVE_API}/files", headers=h, json=body)
    resp.raise_for_status()
    return resp.json()["id"]


def _drive_copy_spreadsheet(creds, folder_id, name):
    """스프레드시트 전체(수식/서식 포함)를 지정 폴더 안에 새 이름으로 통째로 복사"""
    h = _headers(creds)
    body = {"name": name, "parents": [folder_id]}
    resp = requests.post(f"{DRIVE_API}/files/{SPREADSHEET_ID}/copy", headers=h, json=body)
    resp.raise_for_status()
    return resp.json()


def _cleanup_drive_backups(creds, folder_id, keep=DRIVE_KEEP):
    """폴더 안 백업 사본이 keep개를 넘으면, 오래된 것부터 지워서 개수를 유지"""
    h = _headers(creds)
    q = f"'{folder_id}' in parents and trashed = false"
    resp = requests.get(
        f"{DRIVE_API}/files", headers=h,
        params={"q": q, "fields": "files(id, name, createdTime)", "orderBy": "createdTime desc", "pageSize": 1000},
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if len(files) <= keep:
        return
    to_delete = files[keep:]
    for f in to_delete:
        del_resp = requests.delete(f"{DRIVE_API}/files/{f['id']}", headers=h)
        if del_resp.status_code not in (200, 204):
            print(f"   ⚠️ 오래된 드라이브 백업 삭제 실패: {f['name']} ({del_resp.status_code})")
        else:
            print(f"   🗑️ 오래된 드라이브 백업 삭제: {f['name']}")


def _export_xlsx_bytes(creds):
    """스프레드시트 전체(모든 탭)를 xlsx 바이너리로 내보내기"""
    h = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.get(
        f"{DRIVE_API}/files/{SPREADSHEET_ID}/export",
        headers=h, params={"mimeType": XLSX_MIME},
    )
    resp.raise_for_status()
    return resp.content


def _cleanup_repo_backups(keep=REPO_KEEP):
    """backups/ 폴더 안 파일이 keep개를 넘으면, 오래된 것(파일명 = 날짜)부터 지움.
    git 커밋 이력 자체에는 계속 남아있으니 완전한 유실은 아니고, 그냥 작업 폴더만 정리하는 것."""
    if not os.path.isdir(REPO_BACKUP_DIR):
        return
    files = sorted(
        f for f in os.listdir(REPO_BACKUP_DIR)
        if f.endswith(".xlsx")
    )
    if len(files) <= keep:
        return
    for f in files[: len(files) - keep]:
        path = os.path.join(REPO_BACKUP_DIR, f)
        os.remove(path)
        print(f"   🗑️ 오래된 저장소 백업 삭제: {f}")


def main():
    now = datetime.now(pytz.timezone(TIMEZONE))
    date_str = now.strftime("%Y-%m-%d")
    datetime_str = now.strftime("%Y-%m-%d_%H%M")

    creds = get_credentials()

    # 1) 구글 드라이브 내부 백업
    print("📁 구글 드라이브 백업 폴더 확인...")
    folder_id = _retry(lambda: _find_or_create_backup_folder(creds), "드라이브 폴더 확인/생성")

    print("📄 스프레드시트 전체 복사 중...")
    copied = _retry(
        lambda: _drive_copy_spreadsheet(creds, folder_id, f"진폭_백업_{datetime_str}"),
        "드라이브 복사",
    )
    print(f"   ✅ 드라이브 백업 완료: {copied.get('name')}")

    _retry(lambda: _cleanup_drive_backups(creds, folder_id), "드라이브 백업 정리")

    # 2) GitHub 저장소 스냅샷 (xlsx 파일로 저장 - 커밋/푸시는 워크플로 yml에서 처리)
    print("📦 xlsx 스냅샷 내보내는 중...")
    xlsx_bytes = _retry(lambda: _export_xlsx_bytes(creds), "xlsx 내보내기")

    os.makedirs(REPO_BACKUP_DIR, exist_ok=True)
    out_path = os.path.join(REPO_BACKUP_DIR, f"{date_str}.xlsx")
    with open(out_path, "wb") as f:
        f.write(xlsx_bytes)
    print(f"   ✅ 저장소 스냅샷 저장: {out_path} ({len(xlsx_bytes) / 1024:.0f} KB)")

    _cleanup_repo_backups()

    print("🎉 백업 완료!")


if __name__ == "__main__":
    # 2026-09-05 추가 (재인님 요청): 평소엔 조용히 백업만 하고, 실패했을 때만 Slack으로 알림.
    # 2026-09-05 수정(2차): #trading-notify는 실제 트레이딩 데이터 문제 전용으로 남겨두기로
    # 해서, 전용 채널(#system-notify, 봇 systembot)로 보내는 alert_backup_error로 교체.
    # 원인을 6가지 알려진 패턴으로 분류해서 쉬운 말 설명 + 대응방법까지 붙여서 보내줌
    # (재인님이 원본 파이썬 에러만 봐서는 뭐가 문제인지 못 알아보시겠다고 해서).
    # 진폭 통합 폴링(gh_actions_poll.py)의 자동 복구 확인 큐(google_sheet.register_workflow_error)
    # 에는 등록하지 않음 - 그 큐는 "다음 5분 폴링이 정상 완료되면 복구된 것으로 간주"하는
    # 로직인데, 백업 실패와는 별개 워크플로우라 5분 폴링이 잘 도는 것과 백업이 실제로 다시
    # 성공하는 것은 무관함 (엉뚱한 "복구됨" 오탐 방지).
    # 알림 후에는 예외를 다시 던져서 GitHub Actions 쪽에도 그대로 실패(빨간 X)로 표시되게 함.
    try:
        main()
    except Exception as e:
        print(f"❌ 백업 실패: {e}")
        alert_backup_error(e)
        raise
