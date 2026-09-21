"""구글 드라이브 읽기·쓰기. 서비스 계정으로 인증한다.

muwon406(src/muwon/cloud/gdrive_sync.py)과 같은 방식이다. GitHub Actions는
사람이 브라우저로 로그인할 수 없으니, 사람 개입 없이 인증할 수 있는
서비스 계정을 쓴다. 이 저장소의 서비스 계정은 muwon406과 다른 별도
계정이다.

대상 폴더가 공유 드라이브 안에 있어서 모든 호출에
`supportsAllDrives=True`가 필요하다. 빠뜨리면 파일이 있어도 못 찾은
것처럼 동작한다.
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


def upload_text(service, folder_id: str, filename: str, content: str) -> None:
    import io

    file_id = find_child(service, folder_id, filename)
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/csv", resumable=True)
    if file_id is None:
        metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        print(f"신규 업로드 완료: {filename}")
    else:
        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        print(f"업데이트 완료: {filename}")
