from __future__ import annotations

from pathlib import Path

import pytest

typer = pytest.importorskip("typer")
from typer.testing import CliRunner

from course_note.cli import main as cli_main


def test_typer_exposes_generate_subcommand() -> None:
    runner = CliRunner()

    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    assert "generate" in result.stdout
    assert "rewrite-notes" in result.stdout
    assert "publish-hackmd" in result.stdout


def test_generate_subcommand_accepts_url(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_generate_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "generate_impl", fake_generate_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "generate",
            "https://example.com/watch?v=demo",
            "--course-name",
            "demo-course",
            "--scene-edge-weight",
            "0.2",
            "--output",
            "notes.md",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://example.com/watch?v=demo"
    assert captured["course_name"] == "demo-course"
    assert captured["scene_edge_weight"] == 0.2
    assert captured["output"] == Path("notes.md")


def test_generate_subcommand_accepts_llm_timeout_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_generate_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "generate_impl", fake_generate_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "generate",
            "https://example.com/watch?v=demo",
            "--course-name",
            "demo-course",
            "--llm-timeout-seconds",
            "300",
        ],
    )

    assert result.exit_code == 0
    assert captured["llm_timeout_seconds"] == 300


def test_generate_subcommand_leaves_llm_backend_and_model_to_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_generate_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "generate_impl", fake_generate_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "generate",
            "https://example.com/watch?v=demo",
            "--course-name",
            "demo-course",
        ],
    )

    assert result.exit_code == 0
    assert captured["llm_backend"] is None
    assert captured["model"] is None


def test_generate_subcommand_requires_course_name() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli_main.app,
        [
            "generate",
            "https://example.com/watch?v=demo",
        ],
    )

    assert result.exit_code != 0
    assert "--course-name" in result.output


def test_publish_hackmd_subcommand_accepts_note_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}
    note_path = Path("output.md")
    note_path.write_text("# demo\n", encoding="utf-8")

    def fake_publish_hackmd_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "publish_hackmd_impl", fake_publish_hackmd_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "publish-hackmd",
            "output.md",
            "--course-name",
            "demo-course",
            "--title",
            "HackMD Demo",
            "--image-dir",
            "frames",
        ],
    )

    assert result.exit_code == 0
    assert captured["note_path"] == Path("output.md")
    assert captured["course_name"] == "demo-course"
    assert captured["title"] == "HackMD Demo"
    assert captured["image_dir"] == Path("frames")
    note_path.unlink()


def test_publish_hackmd_subcommand_requires_course_name() -> None:
    runner = CliRunner()
    note_path = Path("output.md")
    note_path.write_text("# demo\n", encoding="utf-8")

    result = runner.invoke(
        cli_main.app,
        [
            "publish-hackmd",
            "output.md",
        ],
    )

    assert result.exit_code != 0
    assert "--course-name" in result.output
    note_path.unlink()


def test_rewrite_notes_subcommand_accepts_workdir_and_source_note(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_rewrite_notes_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "rewrite_notes_impl", fake_rewrite_notes_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "rewrite-notes",
            "--workdir",
            "saved-workdir",
            "--source-note",
            "old-output.md",
            "--title",
            "Updated title",
            "--output",
            "new-output.md",
        ],
    )

    assert result.exit_code == 0
    assert captured["workdir"] == Path("saved-workdir")
    assert captured["source_note"] == Path("old-output.md")
    assert captured["title"] == "Updated title"
    assert captured["output"] == Path("new-output.md")
