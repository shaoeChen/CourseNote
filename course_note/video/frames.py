from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory

from course_note.utils.subprocess_utils import run_command
from course_note.utils.timefmt import scene_frame_filename


class FrameSampler:
    def sample_for_scenes(
        self,
        video_path: Path,
        frames_dir: Path,
        scenes: list[dict[str, float]],
        sample_offset_seconds: float,
    ) -> None:
        for scene in scenes:
            if "capture_sec" in scene:
                capture = scene["capture_sec"]
                if not math.isfinite(capture) or not scene["start_sec"] <= capture < scene["end_sec"]:
                    raise ValueError("Selected capture_sec must lie inside its slide scene.")
        frames_dir.mkdir(parents=True, exist_ok=True)
        if any("capture_sec" in scene for scene in scenes):
            # Keep the current note's images usable if a verified capture fails.
            with TemporaryDirectory(prefix=".slide-frames-", dir=frames_dir.parent) as directory:
                staging_dir = Path(directory)
                self._sample_frames(video_path, staging_dir, scenes, sample_offset_seconds)
                self._clear_existing_frames(frames_dir)
                for frame_path in staging_dir.glob("*.jpg"):
                    frame_path.replace(frames_dir / frame_path.name)
            return
        self._clear_existing_frames(frames_dir)
        self._sample_frames(video_path, frames_dir, scenes, sample_offset_seconds)

    def _sample_frames(
        self, video_path: Path, frames_dir: Path, scenes: list[dict[str, float]], sample_offset_seconds: float,
    ) -> None:
        for scene in scenes:
            start_sec = scene["start_sec"]
            end_sec = scene["end_sec"]
            timestamp = scene.get("capture_sec", self._choose_capture_timestamp(start_sec, end_sec, sample_offset_seconds))
            output_path = frames_dir / scene_frame_filename(scene)
            self._capture_frame(video_path, output_path, timestamp)

    def _clear_existing_frames(self, frames_dir: Path) -> None:
        for frame_path in frames_dir.glob("*.jpg"):
            frame_path.unlink()

    def _choose_capture_timestamp(
        self,
        start_sec: float,
        end_sec: float,
        sample_offset_seconds: float,
    ) -> float:
        scene_duration = max(end_sec - start_sec, 0.0)
        if scene_duration <= 0:
            return max(end_sec, 0.0)
        # Stay slightly inside the scene to avoid grabbing the transition frame.
        max_offset = max(scene_duration - 0.25, 0.0)
        return max(end_sec - min(sample_offset_seconds, max_offset), 0.0)

    def _capture_frame(self, video_path: Path, output_path: Path, timestamp: float) -> None:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{math.floor(timestamp * 1_000_000) / 1_000_000:.6f}".rstrip("0").rstrip("."),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_path),
            ]
        )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg did not produce a frame at {timestamp:.3f}s: {output_path}")
