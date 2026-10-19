from __future__ import annotations

from course_note.utils.subprocess_utils import run_command


def fetch_video_title(url: str) -> str:
    """Read only the video title; --no-playlist keeps watch?v=...&list=... to one video."""
    output = run_command(["yt-dlp", "--skip-download", "--no-playlist", "--print", "title", url])
    title = output.stdout.strip().splitlines()[0].strip() if output.stdout.strip() else ""
    if not title:
        raise RuntimeError(f"yt-dlp returned no title for {url}; pass --course-name explicitly.")
    return title
