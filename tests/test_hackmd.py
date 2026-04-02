from __future__ import annotations

from pathlib import Path

import pytest

from course_note.hackmd import publisher as hackmd_publisher
from course_note.hackmd.publisher import HackMDClient, HackMDNote, HackMDPublisher, HackMDRequestError


class DummyHackMDClient:
    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []
        self.updated: list[dict[str, str]] = []
        self.uploaded: list[tuple[str, Path]] = []
        self._create_calls = 0

    def create_note(
        self,
        title: str,
        content: str,
        *,
        read_permission: str | None = None,
        write_permission: str | None = None,
    ) -> HackMDNote:
        self.created.append(
            {
                "title": title,
                "content": content,
                "read_permission": read_permission or "",
                "write_permission": write_permission or "",
            }
        )
        self._create_calls += 1
        if self._create_calls == 1:
            return HackMDNote(note_id="asset-note-123", short_id="asset-short-123", publish_link="https://hackmd.io/@demo/asset-note")
        return HackMDNote(note_id="note-123", short_id="short-123", publish_link="https://hackmd.io/@demo/note-123")

    def update_note(self, note: HackMDNote, *, content: str) -> None:
        self.updated.append({"note_id": note.note_id, "short_id": note.short_id or "", "content": content})

    def upload_image(self, note_id: str, image_path: Path) -> str:
        self.uploaded.append((note_id, image_path))
        return f"https://hackmd.io/_uploads/{image_path.name}"


class DummyResponse:
    def __init__(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body.encode("utf-8")
        self.headers = headers or {}

    def __enter__(self) -> DummyResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body


def test_publish_markdown_uploads_images_and_creates_final_note(tmp_path: Path) -> None:
    note_path = tmp_path / "output.md"
    note_path.write_text(
        "# Demo Title\n\n##\n簡報：000243.jpg\n時間：00:02:43 ~ 00:04:51\n重點：內容\n",
        encoding="utf-8",
    )
    image_dir = tmp_path / "workdir" / "frames"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "000243.jpg"
    image_path.write_bytes(b"fake-image")

    client = DummyHackMDClient()
    result = HackMDPublisher(client=client).publish_markdown(note_path, image_dir=image_dir)

    assert len(client.created) == 2
    assert client.created[0]["title"].startswith("[CourseNote Assets]")
    assert client.created[0]["read_permission"] == "owner"
    assert client.created[0]["write_permission"] == "owner"
    assert client.created[1]["title"] == "Demo Title"
    assert "簡報：000243.jpg\n![000243.jpg](https://hackmd.io/_uploads/000243.jpg)\n時間：00:02:43 ~ 00:04:51" in client.created[1]["content"]
    assert client.uploaded == [("asset-note-123", image_path.resolve())]
    assert client.updated == []
    assert result.image_count == 1
    assert result.publish_link == "https://hackmd.io/@demo/note-123"
    assert result.asset_note_id == "asset-note-123"


def test_publish_markdown_uses_file_stem_when_heading_missing(tmp_path: Path) -> None:
    note_path = tmp_path / "lesson-notes.md"
    note_path.write_text("##\n簡報：000001.jpg\n", encoding="utf-8")
    image_dir = tmp_path / "workdir" / "frames"
    image_dir.mkdir(parents=True)
    (image_dir / "000001.jpg").write_bytes(b"fake-image")

    client = DummyHackMDClient()
    HackMDPublisher(client=client).publish_markdown(note_path, image_dir=image_dir)

    assert client.created[-1]["title"] == "lesson-notes"


def test_publish_markdown_without_images_creates_single_note(tmp_path: Path) -> None:
    note_path = tmp_path / "output.md"
    note_path.write_text("# Demo Title\n\n純文字內容\n", encoding="utf-8")

    client = DummyHackMDClient()
    result = HackMDPublisher(client=client).publish_markdown(note_path, image_dir=tmp_path / "frames")

    assert len(client.created) == 1
    assert client.created[0]["title"] == "Demo Title"
    assert client.uploaded == []
    assert result.image_count == 0
    assert result.asset_note_id is None


def test_update_note_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HackMDClient(api_base="https://api.hackmd.test/v1", api_token="token", update_retry_delay_seconds=0)
    note = HackMDNote(note_id="note-123", short_id="short-123")
    calls: list[str] = []
    sleeps: list[float] = []
    attempts = iter(
        [
            HackMDRequestError("temporary failure", status_code=500, retryable=True),
            HackMDRequestError("temporary failure", status_code=500, retryable=True),
            None,
        ]
    )

    def fake_request_json(
        method: str,
        path: str,
        *,
        payload: dict[str, str],
        content_type: str = "application/json",
        expected_statuses: tuple[int, ...] = (200,),
    ) -> None:
        assert method == "PATCH"
        assert payload["content"] == "updated body"
        assert content_type == "application/json"
        assert expected_statuses == (200, 202)
        calls.append(path)
        result = next(attempts)
        if result is not None:
            raise result

    monkeypatch.setattr(client, "_request_json", fake_request_json)
    monkeypatch.setattr(client, "_sleep_before_retry", lambda delay: sleeps.append(delay))

    client.update_note(note, content="updated body")

    assert calls == ["/notes/note-123", "/notes/note-123", "/notes/note-123"]
    assert sleeps == [0.0, 0.0]


def test_upload_image_retries_transient_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = HackMDClient(
        api_base="https://api.hackmd.test/v1",
        api_token="token",
        upload_retry_delay_seconds=0,
        upload_spacing_seconds=0,
    )
    image_path = tmp_path / "demo.jpg"
    image_path.write_bytes(b"image")
    sleeps: list[float] = []
    attempts = iter(
        [
            HackMDRequestError("rate limited", status_code=429, retryable=True),
            {"data": {"link": "https://hackmd.io/_uploads/demo.jpg"}},
        ]
    )

    def fake_request_json(
        method: str,
        path: str,
        *,
        payload: bytes,
        content_type: str,
        expected_statuses: tuple[int, ...],
    ) -> dict[str, dict[str, str]]:
        assert method == "POST"
        assert path == "/notes/note-123/images"
        assert content_type.startswith("multipart/form-data; boundary=")
        assert expected_statuses == (200, 201)
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client, "_request_json", fake_request_json)
    monkeypatch.setattr(client, "_sleep_before_retry", lambda delay: sleeps.append(delay))

    link = client.upload_image("note-123", image_path)

    assert link == "https://hackmd.io/_uploads/demo.jpg"
    assert sleeps == [15.0]


def test_request_json_waits_for_rate_limit_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HackMDClient(api_base="https://api.hackmd.test/v1", api_token="token")
    client._rate_limit_remaining = 0
    client._rate_limit_reset_at = 200.0
    sleeps: list[float] = []

    def fake_urlopen(request, timeout: int) -> DummyResponse:
        return DummyResponse(
            200,
            '{"ok": true}',
            headers={
                "X-RateLimit-UserLimit": "100",
                "X-RateLimit-UserRemaining": "99",
                "X-RateLimit-UserReset": "260",
            },
        )

    monkeypatch.setattr(client, "_sleep_before_retry", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(hackmd_publisher.time, "time", lambda: 150.0)
    monkeypatch.setattr(hackmd_publisher.urllib.request, "urlopen", fake_urlopen)

    body = client._request_json("GET", "/notes", expected_statuses=(200,))

    assert body == {"ok": True}
    assert sleeps == [50.0]
    assert client._rate_limit_limit == 100
    assert client._rate_limit_remaining == 99
    assert client._rate_limit_reset_at == 260.0
