from __future__ import annotations

import json
import importlib.util
import runpy
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from course_note.cli import main as cli_main
from course_note.models import TranscriptSegment
from course_note.segmentation.timeline import SegmentBuilder
from course_note.video.frames import FrameSampler


@pytest.mark.parametrize('mode', ['hybrid', 'reviewed'])
def test_hybrid_options_reach_generate(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    calls = {}
    monkeypatch.setattr(cli_main, "generate_impl", lambda **kwargs: calls.update(kwargs))
    result = CliRunner().invoke(cli_main.app, ["generate", "https://example.com/v", "--course-name", "demo",
        "--scene-mode", mode, "--vision-backend", "vllm",
        "--vision-timeout-seconds", "240"])
    assert result.exit_code == 0, result.output
    assert calls["scene_mode"] == mode
    assert calls["vision_backend"] == "vllm"
    assert "vision_scan_interval" not in calls
    assert calls["vision_timeout_seconds"] == 240


@pytest.mark.parametrize("kwargs", [{"scene_mode": "bad"}, {"scene_mode": "hybrid", "vision_timeout_seconds": 0}])
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
    (course / "subtitles.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nlesson\n", encoding="utf-8")
    (course / "article.md").write_text("original", encoding="utf-8")
    (course / "segments.json").write_text("[]", encoding="utf-8")
    frames = course / "frames"
    frames.mkdir()
    (frames / "old.jpg").write_bytes(b"old")
    calls = []
    monkeypatch.setattr(cli_main, "_validate_slide_options", lambda *args: None)

    def detect(video, workspace, **kwargs):
        calls.append(kwargs)
        analysis = workspace / "slide_analysis"
        analysis.mkdir()
        (analysis / "slide_review.json").write_text(json.dumps({
            "statistics": {"model_requests": 1, "local_requests": 0, "cache_hits": 0},
            "reviews": [{"segment_index": 0, "status": "content_mismatch", "confidence": .9,
                "reason": "slide ahead of narration", "note_mismatch": True,
                "evidence": [{"start_sec": 5., "end_sec": 8., "detail": "next slide appears"}]}],
            "uncertain_ranges": [{"start_sec": 5., "end_sec": 8., "reason": "manual check"}],
        }), encoding="utf-8")
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
    assert calls[0]["transcripts"][0].text == "lesson"
    assert calls[0]["reuse_scenes"] is True
    preview = course / "slide_preview"
    assert json.loads((preview / "scenes.json").read_text())[0]["capture_sec"] == 4.
    markdown = (preview / "preview.md").read_text(encoding="utf-8")
    assert "frames/" in markdown
    assert "簡報按段審查預覽" in markdown
    assert "slide ahead of narration" in markdown and "next slide appears" in markdown
    assert "待人工確認" in markdown and "manual check" in markdown


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


def test_generate_reviewed_uses_completed_proposals_without_detection_or_vision(monkeypatch, tmp_path):
    course = tmp_path / 'demo'
    course.mkdir()
    (course / 'video.mp4').write_bytes(b'cached')
    (course / 'subtitles.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nlesson\n', encoding='utf-8')
    analysis = course / 'slide_analysis'
    analysis.mkdir()
    proposals = [{'start_sec': 0., 'end_sec': 10., 'capture_sec': 3.}]
    (analysis / 'slide_review.json').write_text(json.dumps({
        'status': 'complete', 'source_scenes': [{'start_sec': 0., 'end_sec': 10.}],
        'reviews': [{'segment_index': 0}], 'proposed_scenes': proposals}), encoding='utf-8')
    monkeypatch.setattr(cli_main, '_detect_hybrid_slides', lambda *args, **kwargs: pytest.fail('No vision review'))
    monkeypatch.setattr(cli_main.OpenAICompatibleClient, 'resolve_vision_config', lambda *args, **kwargs: pytest.fail('No vision config needed'))
    monkeypatch.setattr(cli_main.SceneDetectorService, 'detect', lambda *args, **kwargs: pytest.fail('No algorithm recutting'))

    class Sampler(FrameSampler):
        def _capture_frame(self, video_path, output_path, timestamp):
            assert timestamp == 3.
            output_path.write_bytes(b'frame')

    class LLM:
        def summarize_segments(self, segments, **kwargs):
            for segment in segments:
                segment.summary = 'reviewed article'
            return []

    monkeypatch.setattr(cli_main, 'FrameSampler', Sampler)
    monkeypatch.setattr(cli_main, 'LLMService', LLM)
    cli_main.generate_impl('https://example.com/v', 'demo', workdir=tmp_path, scene_mode='reviewed')
    assert json.loads((course / 'scenes.json').read_text()) == proposals
    assert 'reviewed article' in (course / 'article.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('status,proposals', [
    ('failed', [{'start_sec': 0., 'end_sec': 10.}]),
    ('complete', [{'start_sec': 0., 'end_sec': 8.}]),
    ('complete', [{'start_sec': 0., 'end_sec': 10., 'capture_sec': 10.}]),
])
def test_reviewed_rejects_incomplete_or_invalid_proposals(tmp_path, status, proposals):
    analysis = tmp_path / 'slide_analysis'
    analysis.mkdir()
    (analysis / 'slide_review.json').write_text(json.dumps({
        'status': status, 'source_scenes': [{'start_sec': 0., 'end_sec': 10.}],
        'reviews': [{'segment_index': 0}], 'proposed_scenes': proposals}), encoding='utf-8')
    with pytest.raises(ValueError):
        cli_main._load_reviewed_scenes(tmp_path)


def test_periodic_vision_scan_option_is_removed() -> None:
    result = CliRunner().invoke(cli_main.app, ["generate", "https://example.com/v", "--course-name", "demo",
        "--vision-scan-interval", "5"])
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_argparse_review_options_forward_without_periodic_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None if name == "typer" else original(name, *args))
    namespace = runpy.run_path(str(Path(cli_main.__file__)))
    main = namespace["main"]
    calls = {}
    main.__globals__["detect_slides_impl"] = lambda **kwargs: calls.update(kwargs)
    monkeypatch.setattr(sys, "argv", ["course-note", "detect-slides", "--course-name", "demo",
        "--min-segment-seconds", "25", "--vision-timeout-seconds", "240"])
    main()
    assert calls["min_segment_seconds"] == 25 and calls["vision_timeout_seconds"] == 240
    assert "vision_scan_interval" not in calls
    parser = namespace["_build_argparse"]()
    parsed = parser.parse_args(['generate', 'https://example.com/v', '--course-name', 'demo', '--scene-mode', 'reviewed'])
    assert parsed.scene_mode == 'reviewed'
    with pytest.raises(SystemExit):
        parser.parse_args(["detect-slides", "--course-name", "demo", "--vision-scan-interval", "5"])


def test_preview_loads_transcript_json_without_modifying_it(tmp_path: Path) -> None:
    path = tmp_path / "transcript.json"
    data = [{"start_sec": 0., "end_sec": 5., "text": "cached lesson", "lang": "en", "source": "native"}]
    content = json.dumps(data)
    path.write_text(content, encoding="utf-8")
    result = cli_main._load_review_transcripts(tmp_path)
    assert result[0].text == "cached lesson"
    assert path.read_text(encoding="utf-8") == content


def test_failed_review_still_writes_partial_preview(monkeypatch, tmp_path):
    course = tmp_path / 'demo'
    course.mkdir()
    (course / 'video.mp4').write_bytes(b'video')
    (course / 'subtitles.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nlesson\n', encoding='utf-8')
    monkeypatch.setattr(cli_main, '_validate_slide_options', lambda *args: None)

    def fail(*args, **kwargs):
        analysis = course / 'slide_analysis'
        analysis.mkdir()
        (analysis / 'slide_review.json').write_text(json.dumps({
            'status': 'failed', 'error': 'Invalid segment review response schema.',
            'source_scenes': [{'start_sec': 0., 'end_sec': 5.}, {'start_sec': 5., 'end_sec': 10.}],
            'proposed_scenes': [{'start_sec': 0., 'end_sec': 5., 'capture_sec': 2.}],
            'reviews': [], 'statistics': {}, 'uncertain_ranges': [],
        }), encoding='utf-8')
        raise RuntimeError('Invalid segment review response schema.')

    class Sampler(FrameSampler):
        def _capture_frame(self, video_path, output_path, timestamp):
            output_path.write_bytes(b'preview')

    monkeypatch.setattr(cli_main, '_detect_hybrid_slides', fail)
    monkeypatch.setattr(cli_main, 'FrameSampler', Sampler)
    with pytest.raises(RuntimeError, match='schema'):
        cli_main.detect_slides_impl('demo', tmp_path)
    preview = course / 'slide_preview' / 'partial' / 'partial_preview.md'
    assert '部分預覽' in preview.read_text(encoding='utf-8')
    assert '5.000' in preview.read_text(encoding='utf-8')
    assert not (course / 'slide_preview' / 'preview.md').exists()


def test_preview_requires_subtitle_evidence_before_model_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    course = tmp_path / "demo"
    course.mkdir()
    (course / "video.mp4").write_bytes(b"cached")
    monkeypatch.setattr(cli_main, "_validate_slide_options", lambda *args: None)
    monkeypatch.setattr(cli_main, "_detect_hybrid_slides", lambda *args, **kwargs: pytest.fail("No model call without subtitles"))
    with pytest.raises(FileNotFoundError, match="subtitle|transcript"):
        cli_main.detect_slides_impl("demo", workdir=tmp_path)


def test_algorithm_minimum_duration_is_preserved_in_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []

    class Detector:
        def detect(self, video, threshold, minimum, **kwargs):
            calls.append((threshold, minimum))
            return [{"start_sec": 0., "end_sec": 10.}]

    class Reviewer:
        def review(self, video, scenes, transcripts, directory, **kwargs):
            calls.append(transcripts)
            return scenes

    monkeypatch.setattr(cli_main, "SceneDetectorService", Detector)
    monkeypatch.setattr(cli_main, "SegmentSlideReviewer", Reviewer)
    transcript = [TranscriptSegment(0., 10., "lesson", "en", "native")]
    cli_main._detect_hybrid_slides(tmp_path / "video.mp4", tmp_path,
        scene_threshold=15., scene_edge_weight=.3, min_segment_seconds=20,
        transcripts=transcript, vision_backend=None, vision_timeout_seconds=None)
    assert calls == [(15., 20), transcript]


def test_preview_reuses_existing_timeline_without_recutting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    timeline = [{"start_sec": 0., "end_sec": 10.}, {"start_sec": 10., "end_sec": 15.}]
    (tmp_path / "scenes.json").write_text(json.dumps(timeline), encoding="utf-8")
    class Detector:
        def detect(self, *args, **kwargs):
            pytest.fail("Cached timeline must be the basis of the review")
    class Reviewer:
        def review(self, video, scenes, transcripts, directory, **kwargs):
            assert scenes == timeline
            return scenes
    monkeypatch.setattr(cli_main, "SceneDetectorService", Detector)
    monkeypatch.setattr(cli_main, "SegmentSlideReviewer", Reviewer)
    result = cli_main._detect_hybrid_slides(tmp_path / "video.mp4", tmp_path,
        scene_threshold=15., scene_edge_weight=.3, min_segment_seconds=20,
        transcripts=[TranscriptSegment(0., 15., "lesson", "en", "native")],
        vision_backend=None, vision_timeout_seconds=None, reuse_scenes=True)
    assert result == timeline
