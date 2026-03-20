from __future__ import annotations

import subprocess
from pathlib import Path


class VideoDownloader:
    def download(self, url: str, workdir: Path) -> Path:
        output_path = workdir / "video.mp4"
        subprocess.run(
            [
                "yt-dlp",
                "-f",
                "mp4/bestvideo+bestaudio/best",
                "-o",
                str(output_path),
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output_path
