from __future__ import annotations

import subprocess
from pathlib import Path

from course_note.utils.timefmt import format_compact_filename


class FrameSampler:
    def sample(self, video_path: Path, frames_dir: Path, interval_seconds: float) -> None:
        frames_dir.mkdir(parents=True, exist_ok=True)
        tmp_pattern = frames_dir / "raw-%06d.jpg"
        fps = 1 / interval_seconds
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vf",
                f"fps={fps}",
                str(tmp_pattern),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for index, raw_file in enumerate(sorted(frames_dir.glob("raw-*.jpg"))):
            raw_file.rename(frames_dir / format_compact_filename(index * interval_seconds))
