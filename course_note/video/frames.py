from __future__ import annotations

import subprocess
from pathlib import Path

from course_note.utils.timefmt import format_compact_filename


class FrameSampler:
    def sample_for_scenes(
        self,
        video_path: Path,
        frames_dir: Path,
        scenes: list[dict[str, float]],
        sample_offset_seconds: float,
    ) -> None:
        frames_dir.mkdir(parents=True, exist_ok=True)
        self._clear_existing_frames(frames_dir)
        for scene in scenes:
            start_sec = scene["start_sec"]
            end_sec = scene["end_sec"]
            timestamp = self._choose_capture_timestamp(start_sec, end_sec, sample_offset_seconds)
            output_path = frames_dir / format_compact_filename(start_sec)
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
            return max(start_sec, 0.0)
        # Stay slightly inside the scene to avoid grabbing the transition frame.
        max_offset = max(scene_duration - 0.25, 0.0)
        return max(start_sec + min(sample_offset_seconds, max_offset), 0.0)

    def _capture_frame(self, video_path: Path, output_path: Path, timestamp: float) -> None:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
