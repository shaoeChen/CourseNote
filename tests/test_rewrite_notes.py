from __future__ import annotations

import json
from pathlib import Path

from course_note.cli import main as cli_main


class _FakeLLMService:
    def summarize_segments(
        self,
        segments,
        backend: str,
        model: str,
        note_style: str,
        include_extra_notes: bool,
    ):
        for index, segment in enumerate(segments, start=1):
            segment.summary = f"{note_style}:{segment.transcript}"
            segment.extra_notes = f"extra-{index}" if include_extra_notes else None
        return [
            {"summary": segment.summary, "extra_notes": segment.extra_notes}
            for segment in segments
        ]

    def summarize_course(self, segments, backend: str, model: str) -> str:
        return "course summary"

    def extract_key_terms(self, segments, backend: str, model: str) -> list[str]:
        return ["term-a", "term-b"]


def test_rewrite_notes_impl_reuses_existing_segments_and_note_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "segments.json").write_text(
        json.dumps(
            [
                {
                    "start_sec": 60.0,
                    "end_sec": 220.0,
                    "screenshot": "000340.jpg",
                    "transcript": "segment one",
                    "summary": "",
                    "extra_notes": None,
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    source_note = tmp_path / "output.md"
    source_note.write_text(
        "# Demo Title\n\nhttps://example.com/watch?v=demo\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "rewritten.md"

    monkeypatch.setattr(cli_main, "LLMService", lambda: _FakeLLMService())

    cli_main.rewrite_notes_impl(
        workdir=workdir,
        source_note=source_note,
        llm_backend="ollama",
        model="demo-model",
        note_style="summary",
        with_course_summary=True,
        with_key_terms=True,
        include_extra_notes=True,
        include_transcript=True,
        output=output_path,
    )

    rendered = output_path.read_text(encoding="utf-8")
    llm_outputs = json.loads((workdir / "llm_outputs.json").read_text(encoding="utf-8"))

    assert "Demo Title" in rendered
    assert "https://example.com/watch?v=demo" in rendered
    assert "summary:segment one" in rendered
    assert "course summary" in rendered
    assert "term-a" in rendered
    assert llm_outputs == [{"summary": "summary:segment one", "extra_notes": "extra-1"}]


def test_rewrite_notes_impl_allows_title_and_url_overrides(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "segments.json").write_text(
        json.dumps(
            [
                {
                    "start_sec": 0.0,
                    "end_sec": 10.0,
                    "screenshot": "000010.jpg",
                    "transcript": "segment two",
                    "summary": "",
                    "extra_notes": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "rewritten.md"

    monkeypatch.setattr(cli_main, "LLMService", lambda: _FakeLLMService())

    cli_main.rewrite_notes_impl(
        workdir=workdir,
        source_note=None,
        title="Manual Title",
        url="https://example.com/manual",
        llm_backend="ollama",
        model="demo-model",
        note_style="structured",
        output=output_path,
    )

    rendered = output_path.read_text(encoding="utf-8")

    assert "Manual Title" in rendered
    assert "https://example.com/manual" in rendered
    assert "structured:segment two" in rendered
