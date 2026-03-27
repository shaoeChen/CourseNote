from __future__ import annotations

from pathlib import Path

from course_note.video.frames import FrameSampler


def test_sample_for_scenes_clears_existing_frames_and_uses_scene_starts(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "stale.jpg").write_text("old", encoding="utf-8")

    captured: list[tuple[Path, Path, float]] = []

    class RecordingFrameSampler(FrameSampler):
        def _capture_frame(self, video_path: Path, output_path: Path, timestamp: float) -> None:
            captured.append((video_path, output_path, timestamp))

    sampler = RecordingFrameSampler()
    video_path = tmp_path / "video.mp4"
    scenes = [
        {"start_sec": 0.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 12.0},
    ]

    sampler.sample_for_scenes(video_path, frames_dir, scenes, sample_offset_seconds=1.0)

    assert not (frames_dir / "stale.jpg").exists()
    assert [item[1].name for item in captured] == ["000000.jpg", "000010.jpg"]
    assert [item[2] for item in captured] == [1.0, 11.0]


def test_choose_capture_timestamp_stays_within_scene() -> None:
    sampler = FrameSampler()

    assert sampler._choose_capture_timestamp(5.0, 5.1, 1.0) == 5.0
    assert sampler._choose_capture_timestamp(5.0, 20.0, 2.0) == 7.0
