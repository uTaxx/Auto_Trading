"""구글 드라이브 읽기·쓰기. 서비스 계정으로 인증한다.

muwon406(src/muwon/cloud/gdrive_sync.py)과 같은 방식이다. GitHub Actions는
사람이 브라우저로 로그인할 수 없으니, 사람 개입 없이 인증할 수 있는
서비스 계정을 쓴다. 이 저장소의 서비스 계정은 muwon406과 다른 별도
계정이다.

대상 폴더가 공유 드라이브 안에 있어서 모든 호출에
`supportsAllDrives=True`가 필요하다. 빠뜨리면 파일이 있어도 못 찾은
것처럼 동작한다.

**서비스 계정은 새 파일을 처음 만들 때(create)만 storageQuotaExceeded로
막힌다**(2026-09-21에 실제로 겪었다). 공유 드라이브 멤버로 넣어도, 폴더를
새로 만들거나 있는 파일을 고치는 것은 되는데 파일을 새로 만드는 것만
안 됐다. 구글 API 오류 메시지가 권하는 대로 도메인 위임을 쓴다.
`GDRIVE_IMPERSONATE_EMAIL`이 있으면 그 사람 명의로 행동해서, 서비스 계정이
아니라 그 사람 소유로 파일이 만들어진다. 이 환경변수가 없으면 예전처럼
서비스 계정 그대로 인증한다(도메인 위임을 아직 안 걸었을 때도 동작하게
하기 위해서다).
"""

from __future__ import annotations

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _build_service():
    key_json = os.environ.get("GDRIVE_SA_KEY_JSON")
    if not key_json:
        raise SystemExit("GDRIVE_SA_KEY_JSON 환경변수가 없습니다 (서비스 계정 JSON 키 원문).")
    info = json.loads(key_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    impersonate = os.environ.get("GDRIVE_IMPERSONATE_EMAIL")
    if impersonate:
        creds = creds.with_subject(impersonate)

    return build("drive", "v3", credentials=creds)


def find_child(service, parent_id: str, name: str, folder_only: bool = False) -> str | None:
    query = f"name = '{name}' and '{parent_id}' in parents and trashed = false"
    if folder_only:
        query += " and mimeType = 'application/vnd.google-apps.folder'"
    result = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            spaces="drive",
            corpora="allDrives",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = result.get("files", [])
    return files[0]["id"] if files else None


def find_or_create_folder(service, parent_id: str, name: str) -> str:
    existing = find_child(service, parent_id, name, folder_only=True)
    if existing:
        return existing
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    print(f"폴더를 새로 만들었습니다: {name}")
    return created["id"]


def download_text(service, folder_id: str, filename: str) -> str | None:
    """파일이 없으면 None. 있으면 문자열 내용."""
    file_id = find_child(service, folder_id, filename)
    if file_id is None:
        return None
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = bytearray()

    class _Buf:
        def write(self, b):
            buffer.extend(b)

    downloader = MediaIoBaseDownload(_Buf(), request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return bytes(buffer).decode("utf-8")


def upload_bytes(service, folder_id: str, filename: str, content: bytes, mimetype: str) -> None:
    import io

    file_id = find_child(service, folder_id, filename)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=True)
    if file_id is None:
        metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        print(f"신규 업로드 완료: {filename}")
    else:
        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        print(f"업데이트 완료: {filename}")


def upload_text(service, folder_id: str, filename: str, content: str) -> None:
    mimetype = "application/json" if filename.endswith(".json") else "text/csv"
    upload_bytes(service, folder_id, filename, content.encode("utf-8"), mimetype)
