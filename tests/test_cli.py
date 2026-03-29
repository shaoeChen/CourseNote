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
            "--scene-edge-weight",
            "0.2",
            "--output",
            "notes.md",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://example.com/watch?v=demo"
    assert captured["scene_edge_weight"] == 0.2
    assert captured["output"] == Path("notes.md")
