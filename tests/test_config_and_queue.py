from __future__ import annotations

from pathlib import Path

import pytest

from course_note.cli import main as cli_main
from course_note.cli.config import ConfigError, load_config
from course_note.cli.queue import QueueItem, QueueLockedError, parse_queue_file, queue_lock, run_queue
from course_note.utils.io import CourseWorkspaceMetadata, write_course_workspace_metadata


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_config_supplies_defaults_and_cli_overrides_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write(tmp_path / "course-note.toml", """
[generate]
scene_threshold = 15
scene-edge-weight = 0.3
note_style = "near_transcript"
with_key_terms = true
workdir = "notes"
""")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli_main, "generate_impl", lambda **kwargs: captured.update(kwargs))

    cli_main.generate_from_cli(
        url="https://youtu.be/x", course_name=None, config=config,
        note_style="summary", with_key_terms=None, scene_threshold=None,
    )

    assert captured["scene_threshold"] == 15.0
    assert captured["scene_edge_weight"] == 0.3
    assert captured["note_style"] == "summary"
    assert captured["with_key_terms"] is True
    assert captured["workdir"] == Path("notes")
    assert captured["subtitle_lang"] == "zh-TW"


def test_config_rejects_unknown_option_and_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown option 'scene_treshold'"):
        load_config(_write(tmp_path / "a.toml", "[generate]\nscene_treshold = 15\n"))
    with pytest.raises(ConfigError, match="must be true or false"):
        load_config(_write(tmp_path / "b.toml", '[generate]\nwith_key_terms = "yes"\n'))
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_resolve_course_name_prefers_existing_workspace_then_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    course_dir = tmp_path / "Old Name"
    course_dir.mkdir()
    write_course_workspace_metadata(course_dir, CourseWorkspaceMetadata("Old Name", "https://youtu.be/a", "t"))
    monkeypatch.setattr(cli_main, "fetch_video_title", lambda url: "Fresh Title")

    assert cli_main.resolve_course_name("https://youtu.be/a", None, tmp_path) == "Old Name"
    assert cli_main.resolve_course_name("https://youtu.be/b", None, tmp_path) == "Fresh Title"
    assert cli_main.resolve_course_name("https://youtu.be/b", "Mine", tmp_path) == "Mine"


def test_parse_queue_file_keeps_names_with_pipes(tmp_path: Path) -> None:
    queue = _write(tmp_path / "queue.txt", """
# comment
https://youtu.be/a
https://youtu.be/b   CS329A | Part 4 | Tools/Code

""")

    items = parse_queue_file(queue)

    assert [(i.line_number, i.url, i.course_name) for i in items] == [
        (3, "https://youtu.be/a", None),
        (4, "https://youtu.be/b", "CS329A | Part 4 | Tools/Code"),
    ]
    with pytest.raises(ValueError, match=":2:"):
        parse_queue_file(_write(tmp_path / "bad.txt", "# x\nnot-a-url\n"))


def test_run_queue_is_sequential_continues_on_error_and_pauses_only_after_work() -> None:
    items = [QueueItem(n, f"https://youtu.be/{n}") for n in range(1, 5)]
    order: list[str] = []

    def process(item: QueueItem) -> bool:
        order.append(f"run{item.line_number}")
        if item.line_number == 1:
            return False  # skipped: no network, so no pause before item 2
        if item.line_number == 2:
            raise RuntimeError("boom")
        return True

    result = run_queue(items, process=process, delay_seconds=5, stop_on_error=False, sleep=lambda s: order.append(f"sleep{s:g}"))

    assert order == ["run1", "run2", "sleep5", "run3", "sleep5", "run4"]
    assert [i.line_number for i in result.skipped] == [1]
    assert [i.line_number for i, _ in result.failed] == [2]
    assert [i.line_number for i in result.succeeded] == [3, 4]


def test_run_queue_stop_on_error() -> None:
    items = [QueueItem(n, f"https://youtu.be/{n}") for n in range(1, 4)]

    def process(item: QueueItem) -> bool:
        raise RuntimeError("boom")

    result = run_queue(items, process=process, delay_seconds=0, stop_on_error=True)

    assert len(result.failed) == 1


def test_queue_lock_blocks_second_runner(tmp_path: Path) -> None:
    with queue_lock(tmp_path):
        with pytest.raises(QueueLockedError):
            with queue_lock(tmp_path):
                pass
    assert not (tmp_path / ".queue.lock").exists()


def test_queue_impl_skips_completed_and_reports_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "wd"
    done = workdir / "Done"
    done.mkdir(parents=True)
    write_course_workspace_metadata(done, CourseWorkspaceMetadata("Done", "https://youtu.be/done", "t"))
    (done / "article.md").write_text("# t", encoding="utf-8")
    config = _write(tmp_path / "c.toml", f'[generate]\nworkdir = "{workdir.as_posix()}"\nnote_style = "near_transcript"\n[queue]\ndelay_seconds = 0\n')
    queue = _write(tmp_path / "q.txt", "https://youtu.be/done\nhttps://youtu.be/new Named\nhttps://youtu.be/bad\n")
    calls: list[dict[str, object]] = []

    def fake_generate(**kwargs: object) -> None:
        calls.append(kwargs)
        if kwargs["url"] == "https://youtu.be/bad":
            raise RuntimeError("no subtitles")

    monkeypatch.setattr(cli_main, "generate_impl", fake_generate)

    with pytest.raises(SystemExit) as exc_info:
        cli_main.queue_impl(queue_file=queue, config=config)

    assert exc_info.value.code == 1
    assert [c["url"] for c in calls] == ["https://youtu.be/new", "https://youtu.be/bad"]
    assert calls[0]["course_name"] == "Named"
    assert calls[0]["note_style"] == "near_transcript"
    assert not (workdir / ".queue.lock").exists()
