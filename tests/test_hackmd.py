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


class FailingUpdateHackMDClient(DummyHackMDClient):
    def update_note(self, note: HackMDNote, *, content: str) -> None:
        super().update_note(note, content=content)
        raise HackMDRequestError("temporary update failure", status_code=500, retryable=True)


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

    assert len(client.created) == 1
    assert client.created[0]["title"] == "Demo Title"
    assert client.created[0]["content"] == "# Demo Title\n\n##\n簡報：000243.jpg\n時間：00:02:43 ~ 00:04:51\n重點：內容\n"
    assert client.created[0]["read_permission"] == ""
    assert client.created[0]["write_permission"] == ""
    assert client.uploaded == [("asset-note-123", image_path.resolve())]
    assert client.updated == [
        {
            "note_id": "asset-note-123",
            "short_id": "asset-short-123",
            "content": "# Demo Title\n\n##\n簡報：000243.jpg\n![000243.jpg](https://hackmd.io/_uploads/000243.jpg)\n時間：00:02:43 ~ 00:04:51\n重點：內容\n",
        }
    ]
    assert result.image_count == 1
    assert result.publish_link == "https://hackmd.io/@demo/asset-note"


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


def test_publish_markdown_waits_before_final_update_when_images_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    note_path = tmp_path / "output.md"
    note_path.write_text(
        "# Demo Title\n\n##\n簡報：000243.jpg\n時間：00:02:43 ~ 00:04:51\n重點：內容\n",
        encoding="utf-8",
    )
    image_dir = tmp_path / "workdir" / "frames"
    image_dir.mkdir(parents=True)
    (image_dir / "000243.jpg").write_bytes(b"fake-image")

    client = DummyHackMDClient()
    sleeps: list[float] = []
    publisher = HackMDPublisher(client=client, post_upload_settle_seconds=7.5)
    monkeypatch.setattr(publisher, "_sleep_before_finalize", lambda delay: sleeps.append(delay))

    publisher.publish_markdown(note_path, image_dir=image_dir)

    assert sleeps == [7.5]


def test_publish_markdown_keeps_existing_note_when_update_after_upload_fails(tmp_path: Path) -> None:
    note_path = tmp_path / "output.md"
    note_path.write_text(
        "# Demo Title\n\n##\n簡報：000243.jpg\n時間：00:02:43 ~ 00:04:51\n重點：內容\n",
        encoding="utf-8",
    )
    image_dir = tmp_path / "workdir" / "frames"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "000243.jpg"
    image_path.write_bytes(b"fake-image")

    client = FailingUpdateHackMDClient()
    with pytest.raises(HackMDRequestError):
        HackMDPublisher(client=client, post_upload_settle_seconds=0).publish_markdown(note_path, image_dir=image_dir)

    assert len(client.created) == 1
    assert client.uploaded == [("asset-note-123", image_path.resolve())]
    assert client.updated == [
        {
            "note_id": "asset-note-123",
            "short_id": "asset-short-123",
            "content": "# Demo Title\n\n##\n簡報：000243.jpg\n![000243.jpg](https://hackmd.io/_uploads/000243.jpg)\n時間：00:02:43 ~ 00:04:51\n重點：內容\n",
        }
    ]
    import json
    state = json.loads((tmp_path / 'hackmd_publish.json').read_text())
    assert state['status'] == 'failed' and state['note']['note_id'] == 'asset-note-123'
    assert len(state['images']) == 1


def test_failed_image_publish_resumes_same_note_without_reuploading_successes(tmp_path, capsys):
    import json
    note = tmp_path / 'article.md'
    note.write_text('# Demo\n簡報：a.jpg\n簡報：b.jpg\n', encoding='utf-8')
    for name in ('a.jpg', 'b.jpg'):
        (tmp_path / name).write_bytes(name.encode())

    class OnceFailingClient(DummyHackMDClient):
        def upload_image(self, note_id, image_path):
            if image_path.name == 'b.jpg':
                raise RuntimeError('image unavailable')
            return super().upload_image(note_id, image_path)

    first = OnceFailingClient()
    with pytest.raises(RuntimeError, match='image unavailable'):
        HackMDPublisher(first, post_upload_settle_seconds=0).publish_markdown(note, image_dir=tmp_path)
    second = DummyHackMDClient()
    result = HackMDPublisher(second, post_upload_settle_seconds=0).publish_markdown(note, image_dir=tmp_path)
    assert not second.created
    assert [path.name for _, path in second.uploaded] == ['b.jpg']
    assert result.note_id == 'asset-note-123' and result.image_count == 2
    output = capsys.readouterr().out
    assert '[HACKMD_UPLOAD 1/2]' in output and '[HACKMD_RESUME]' in output and '[HACKMD_CACHE]' in output
    assert (tmp_path / 'hackmd_publish.log').is_file()
    assert json.loads((tmp_path / 'hackmd_publish.json').read_text())['status'] == 'complete'


def test_completed_publish_rerun_does_not_modify_remote_note(tmp_path):
    note = tmp_path / 'article.md'
    note.write_text('# Demo\n', encoding='utf-8')
    client = DummyHackMDClient()
    publisher = HackMDPublisher(client)
    publisher.publish_markdown(note)
    publisher.publish_markdown(note)
    assert len(client.created) == 1 and not client.updated


def test_ambiguous_creation_does_not_create_duplicate_on_retry(tmp_path):
    note = tmp_path / 'article.md'
    note.write_text('# Demo\n', encoding='utf-8')

    class AmbiguousClient(DummyHackMDClient):
        def create_note(self, *args, **kwargs):
            super().create_note(*args, **kwargs)
            raise TimeoutError('creation outcome unknown')

    client = AmbiguousClient()
    publisher = HackMDPublisher(client)
    with pytest.raises(TimeoutError):
        publisher.publish_markdown(note)
    with pytest.raises(RuntimeError, match='outcome is unknown'):
        publisher.publish_markdown(note)
    assert len(client.created) == 1


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


def test_upload_image_normalizes_root_relative_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = HackMDClient(
        api_base="https://api.hackmd.test/v1",
        api_token="token",
        upload_spacing_seconds=0,
    )
    image_path = tmp_path / "demo.jpg"
    image_path.write_bytes(b"image")

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
        return {"data": {"link": "/_uploads/demo.jpg"}}

    monkeypatch.setattr(client, "_request_json", fake_request_json)
    monkeypatch.setattr(client, "_sleep_before_retry", lambda delay: None)

    link = client.upload_image("note-123", image_path)

    assert link == "https://hackmd.io/_uploads/demo.jpg"


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


def test_request_json_normalizes_millisecond_rate_limit_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HackMDClient(api_base="https://api.hackmd.test/v1", api_token="token")

    def fake_urlopen(request, timeout: int) -> DummyResponse:
        return DummyResponse(
            200,
            '{"ok": true}',
            headers={
                "X-RateLimit-UserReset": "1714300000123",
            },
        )

    monkeypatch.setattr(hackmd_publisher.urllib.request, "urlopen", fake_urlopen)

    body = client._request_json("GET", "/notes", expected_statuses=(200,))

    assert body == {"ok": True}
    assert client._rate_limit_reset_at == 1714300000.123


def test_sleep_before_retry_clamps_large_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HackMDClient(api_base="https://api.hackmd.test/v1", api_token="token")
    sleeps: list[float] = []

    monkeypatch.setattr(hackmd_publisher.time, "sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(HackMDRequestError, match="Resume publishing later"):
        client._sleep_before_retry(10_000_000_000.0)

    assert sleeps == []


def test_image_retry_does_not_wait_for_available_general_api_quota() -> None:
    client = HackMDClient(api_base="https://api.hackmd.test/v1", api_token="token")
    error = HackMDRequestError("Too Many Requests", status_code=429, retryable=True,
                              rate_limit_remaining=9968, rate_limit_reset_at=10_000_000_000.0)
    assert client._retry_delay_seconds(error, 0, 5.0) == 15.0
