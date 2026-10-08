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
    assert "download-subtitles" in result.stdout
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
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://example.com/watch?v=demo"
    assert captured["course_name"] == "demo-course"
    assert captured["scene_edge_weight"] == 0.2
    assert "output" not in captured


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


def test_generate_subcommand_leaves_llm_backend_to_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "model" not in captured


def test_generate_subcommand_rejects_removed_model_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli_main.app,
        [
            "generate",
            "https://example.com/watch?v=demo",
            "--course-name",
            "demo-course",
            "--model",
            "demo-model",
        ],
    )

    assert result.exit_code != 0
    assert any(
        message in result.output
        for message in ("No such option: --model", "No such option '--model'")
    )


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


def test_download_subtitles_subcommand_accepts_url_and_course_name(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_download_subtitles_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "download_subtitles_impl", fake_download_subtitles_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "download-subtitles",
            "https://example.com/watch?v=demo",
            "--course-name",
            "demo-course",
            "--subtitle-lang",
            "en",
            "--workdir",
            "saved-workdir",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://example.com/watch?v=demo"
    assert captured["course_name"] == "demo-course"
    assert captured["subtitle_lang"] == "en"
    assert captured["workdir"] == Path("saved-workdir")


def test_publish_hackmd_subcommand_accepts_course_name(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_publish_hackmd_impl(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main, "publish_hackmd_impl", fake_publish_hackmd_impl)

    result = runner.invoke(
        cli_main.app,
        [
            "publish-hackmd",
            "--course-name",
            "demo-course",
            "--title",
            "HackMD Demo",
            "--image-dir",
            "frames",
        ],
    )

    assert result.exit_code == 0
    assert captured["course_name"] == "demo-course"
    assert captured["title"] == "HackMD Demo"
    assert captured["image_dir"] == Path("frames")


def test_publish_hackmd_subcommand_requires_course_name() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli_main.app,
        [
            "publish-hackmd",
        ],
    )

    assert result.exit_code != 0
    assert "--course-name" in result.output


def test_rewrite_notes_subcommand_accepts_workdir_and_course_name(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--course-name",
            "demo-course",
            "--title",
            "Updated title",
        ],
    )

    assert result.exit_code == 0
    assert captured["workdir"] == Path("saved-workdir")
    assert captured["course_name"] == "demo-course"
    assert captured["title"] == "Updated title"
    assert "output" not in captured
