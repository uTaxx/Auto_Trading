import json

from auto_trading import gdrive


class _FakeService:
    """next_result_number는 service 객체를 직접 만지지 않고 download_text·
    upload_text를 거치므로, 그 둘만 갈아 끼우면 된다. service 자체는 안
    쓰인다."""


def test_카운터_파일이_없으면_1부터_시작한다(monkeypatch):
    monkeypatch.setattr(gdrive, "download_text", lambda service, folder_id, filename: None)
    written = {}
    monkeypatch.setattr(
        gdrive, "upload_text", lambda service, folder_id, filename, content: written.update(filename=filename, content=content)
    )

    number = gdrive.next_result_number(_FakeService(), "folder-1")

    assert number == 1
    assert written["filename"] == gdrive.COUNTER_FILENAME
    assert json.loads(written["content"]) == {"마지막번호": 1}


def test_카운터_파일이_있으면_1을_더한다(monkeypatch):
    monkeypatch.setattr(
        gdrive, "download_text", lambda service, folder_id, filename: json.dumps({"마지막번호": 6})
    )
    written = {}
    monkeypatch.setattr(
        gdrive, "upload_text", lambda service, folder_id, filename, content: written.update(content=content)
    )

    number = gdrive.next_result_number(_FakeService(), "folder-1")

    assert number == 7
    assert json.loads(written["content"]) == {"마지막번호": 7}


def test_호출할_때마다_번호가_하나씩_올라간다(monkeypatch):
    state = {"last": 0}
    monkeypatch.setattr(
        gdrive, "download_text", lambda service, folder_id, filename: json.dumps({"마지막번호": state["last"]})
    )

    def fake_upload(service, folder_id, filename, content):
        state["last"] = json.loads(content)["마지막번호"]

    monkeypatch.setattr(gdrive, "upload_text", fake_upload)

    numbers = [gdrive.next_result_number(_FakeService(), "folder-1") for _ in range(3)]

    assert numbers == [1, 2, 3]
