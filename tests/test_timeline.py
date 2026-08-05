from __future__ import annotations

from pathlib import Path

from course_note.models import TranscriptSegment
from course_note.segmentation.timeline import SegmentBuilder


def test_build_uses_scene_end_screenshot_when_available(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "000340.jpg").write_text("frame", encoding="utf-8")

    scenes = [{"start_sec": 60.0, "end_sec": 220.0}]
    transcripts = [
        TranscriptSegment(
            start_sec=60.0,
            end_sec=220.0,
            text="demo",
            lang="zh-TW",
            source="manual",
        )
    ]

    segments = SegmentBuilder().build(scenes, transcripts, frames_dir)

    assert len(segments) == 1
    assert segments[0].screenshot == "000340.jpg"


def test_resolve_screenshot_falls_back_to_nearest_scene_end(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "000338.jpg").write_text("before", encoding="utf-8")
    (frames_dir / "000400.jpg").write_text("after", encoding="utf-8")

    screenshot = SegmentBuilder()._resolve_screenshot(frames_dir, 220.0)

    assert screenshot == "000338.jpg"
