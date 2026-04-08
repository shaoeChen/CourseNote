from __future__ import annotations

from pathlib import Path

from course_note.utils.subprocess_utils import run_command


class VideoDownloader:
    def download(self, url: str, workdir: Path) -> Path:
        output_path = workdir / "video.mp4"
        run_command(
            [
                "yt-dlp",
                "-f",
                "mp4/bestvideo+bestaudio/best",
                "-o",
                str(output_path),
                url,
            ]
        )
        return output_path
