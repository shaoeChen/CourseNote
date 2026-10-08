from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from course_note.cli import main as cli_main
from course_note.models import TranscriptSegment
from course_note.segmentation.timeline import SegmentBuilder
from course_note.video.frames import FrameSampler


def test_hybrid_options_reach_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}
    monkeypatch.setattr(cli_main, "generate_impl", lambda **kwargs: calls.update(kwargs))
    result = CliRunner().invoke(cli_main.app, ["generate", "https://example.com/v", "--course-name", "demo",
        "--scene-mode", "hybrid", "--vision-backend", "vllm", "--vision-scan-interval", "3",
        "--vision-timeout-seconds", "240"])
    assert result.exit_code == 0, result.output
    assert calls["scene_mode"] == "hybrid"
    assert calls["vision_backend"] == "vllm"
    assert calls["vision_scan_interval"] == 3
    assert calls["vision_timeout_seconds"] == 240


@pytest.mark.parametrize("kwargs", [{"scene_mode": "bad"}, {"scene_mode": "hybrid", "vision_scan_interval": 0},
    {"scene_mode": "hybrid", "vision_scan_interval": float("nan")}])
def test_invalid_slide_options_fail_before_workspace_mutation(tmp_path: Path, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        cli_main.generate_impl("https://example.com/v", "demo", workdir=tmp_path, **kwargs)
    assert not list(tmp_path.iterdir())


def test_selected_capture_is_used_and_short_slide_names_do_not_collide(tmp_path: Path) -> None:
    calls = []

    class Sampler(FrameSampler):
        def _capture_frame(self, video_path, output_path, timestamp):
            calls.append((output_path.name, timestamp))
            output_path.write_bytes(b"frame")

    scenes = [{"start_sec": 0., "end_sec": 5.1, "capture_sec": 2.},
        {"start_sec": 5.1, "end_sec": 5.8, "capture_sec": 5.5}]
    frames = tmp_path / "frames"
    Sampler().sample_for_scenes(tmp_path / "video.mp4", frames, scenes, 1.)
    assert [t for _, t in calls] == [2., 5.5]
    assert len({name for name, _ in calls}) == 2
    segments = SegmentBuilder().build(scenes, [], frames)
    assert [s.screenshot for s in segments] == [name for name, _ in calls]


def test_invalid_selected_capture_does_not_delete_existing_frames(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    old = frames / "old.jpg"
    old.write_bytes(b"old")
    with pytest.raises(ValueError):
        FrameSampler().sample_for_scenes(tmp_path / "video.mp4", frames,
            [{"start_sec": 0., "end_sec": 5., "capture_sec": 6.}], 1.)
    assert old.read_bytes() == b"old"


def test_ffmpeg_success_without_a_frame_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("course_note.video.frames.run_command", lambda command: "")
    with pytest.raises(RuntimeError, match="did not produce"):
        FrameSampler()._capture_frame(tmp_path / "video.mp4", tmp_path / "missing.jpg", 9.95)


def test_verified_capture_failure_preserves_existing_frames(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "old.jpg").write_bytes(b"original")
    monkeypatch.setattr("course_note.video.frames.run_command", lambda command: "")
    with pytest.raises(RuntimeError, match="did not produce"):
        FrameSampler().sample_for_scenes(tmp_path / "video.mp4", frames,
            [{"start_sec": 0., "end_sec": 10., "capture_sec": 9.9}], 1.)
    assert (frames / "old.jpg").read_bytes() == b"original"
    assert list(tmp_path.glob(".slide-frames-*")) == []


def test_preview_uses_cached_video_without_touching_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    course = tmp_path / "demo"
    course.mkdir()
    (course / "video.mp4").write_bytes(b"video")
    (course / "article.md").write_text("original", encoding="utf-8")
    (course / "segments.json").write_text("[]", encoding="utf-8")
    frames = course / "frames"
    frames.mkdir()
    (frames / "old.jpg").write_bytes(b"old")
    calls = []
    monkeypatch.setattr(cli_main, "_validate_slide_options", lambda *args: None)

    def detect(video, workspace, **kwargs):
        calls.append(kwargs)
        return [{"start_sec": 0., "end_sec": 10., "capture_sec": 4.}]

    monkeypatch.setattr(cli_main, "_detect_hybrid_slides", detect)

    class Sampler(FrameSampler):
        def _capture_frame(self, video_path, output_path, timestamp):
            output_path.write_bytes(b"preview")

    monkeypatch.setattr(cli_main, "FrameSampler", Sampler)
    cli_main.detect_slides_impl("demo", workdir=tmp_path, vision_backend="vllm")
    assert (course / "article.md").read_text() == "original"
    assert (course / "segments.json").read_text() == "[]"
    assert (frames / "old.jpg").read_bytes() == b"old"
    assert calls[0]["vision_backend"] == "vllm"
    preview = course / "slide_preview"
    assert json.loads((preview / "scenes.json").read_text())[0]["capture_sec"] == 4.
    assert "frames/" in (preview / "preview.md").read_text(encoding="utf-8")


def test_subtitles_at_exact_boundary_belong_to_next_slide(tmp_path: Path) -> None:
    segments = SegmentBuilder().build([{"start_sec": 0., "end_sec": 5.}, {"start_sec": 5., "end_sec": 10.}],
        [TranscriptSegment(5., 6., "next", "en", "native")], tmp_path)
    assert segments[0].transcript == ""
    assert segments[1].transcript == "next"


def test_generate_hybrid_wires_selected_slides_to_rendered_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    course = tmp_path / "demo"
    course.mkdir()
    (course / "video.mp4").write_bytes(b"cached")
    (course / "subtitles.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nlesson\n", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_validate_slide_options", lambda *args: None)
    monkeypatch.setattr(cli_main, "_detect_hybrid_slides", lambda *args, **kwargs: [
        {"start_sec": 0., "end_sec": 10., "capture_sec": 3.}])
    captures = []

    class Sampler(FrameSampler):
        def _capture_frame(self, video_path, output_path, timestamp):
            captures.append(timestamp)
            output_path.write_bytes(b"frame")

    class LLM:
        def summarize_segments(self, segments, **kwargs):
            for segment in segments:
                segment.summary = "readable note"
            return [{"summary": s.summary} for s in segments]

    monkeypatch.setattr(cli_main, "FrameSampler", Sampler)
    monkeypatch.setattr(cli_main, "LLMService", LLM)
    cli_main.generate_impl("https://example.com/v", "demo", workdir=tmp_path, scene_mode="hybrid")
    assert captures == [3.]
    assert "readable note" in (course / "article.md").read_text(encoding="utf-8")
    saved = json.loads((course / "segments.json").read_text(encoding="utf-8"))
    assert saved[0]["screenshot"] == "000010_0000010000.jpg"
    assert saved[0]["transcript"] == "lesson"
