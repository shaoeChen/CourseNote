from __future__ import annotations

from pathlib import Path

from course_note.utils.progress import progress_stage
from course_note.utils.subprocess_utils import run_command


class VideoDownloader:
    def download(self, url: str, workdir: Path) -> Path:
        output_path = workdir / "video.mp4"
        with progress_stage("下載影片", workdir / "pipeline.log"):
            run_command(
                [
                    "yt-dlp",
                    "--newline",
                    "--progress",
                    "-f",
                    "mp4/bestvideo+bestaudio/best",
                    "-o",
                    str(output_path),
                    url,
                ],
                stream=True,
            )
        return output_path
