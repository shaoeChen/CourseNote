from __future__ import annotations

from pathlib import Path

from course_note.cli import main as cli_main


def test_publish_hackmd_impl_uses_course_workspace_frames(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeHackMDPublisher:
        def publish_markdown(self, note_path: Path, *, title: str | None = None, image_dir: Path | None = None):
            captured["note_path"] = note_path
            captured["title"] = title
            captured["image_dir"] = image_dir
            return type(
                "Result",
                (),
                {
                    "publish_link": "https://hackmd.io/demo",
                    "permalink": None,
                    "note_id": "note-123",
                    "image_count": 1,
                },
            )()

    monkeypatch.setattr(cli_main, "load_env_file", lambda: None)
    monkeypatch.setattr(cli_main, "HackMDPublisher", lambda: _FakeHackMDPublisher())

    note_path = tmp_path / "output.md"
    note_path.write_text("# Demo\n", encoding="utf-8")

    cli_main.publish_hackmd_impl(
        note_path=note_path,
        course_name="Demo Course",
        workdir=tmp_path / "workdir",
    )

    assert captured["note_path"] == note_path
    assert captured["image_dir"] == tmp_path / "workdir" / "Demo Course" / "frames"


def test_publish_hackmd_impl_allows_image_dir_override(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _FakeHackMDPublisher:
        def publish_markdown(self, note_path: Path, *, title: str | None = None, image_dir: Path | None = None):
            captured["image_dir"] = image_dir
            return type(
                "Result",
                (),
                {
                    "publish_link": "https://hackmd.io/demo",
                    "permalink": None,
                    "note_id": "note-123",
                    "image_count": 0,
                },
            )()

    monkeypatch.setattr(cli_main, "load_env_file", lambda: None)
    monkeypatch.setattr(cli_main, "HackMDPublisher", lambda: _FakeHackMDPublisher())

    note_path = tmp_path / "output.md"
    note_path.write_text("# Demo\n", encoding="utf-8")
    override_dir = tmp_path / "custom-frames"

    cli_main.publish_hackmd_impl(
        note_path=note_path,
        course_name="Demo Course",
        workdir=tmp_path / "workdir",
        image_dir=override_dir,
    )

    assert captured["image_dir"] == override_dir
