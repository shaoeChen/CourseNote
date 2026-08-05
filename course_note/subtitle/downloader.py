from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from course_note.utils.subprocess_utils import run_command


@dataclass(slots=True)
class SubtitleDownloadResult:
    title: str
    subtitle_path: Path
    transcript_json_path: Path
    source: str
    language: str


class YouTubeSubtitleDownloader:
    def download(self, url: str, workdir: Path, language: str) -> SubtitleDownloadResult:
        info = self._extract_info(url)
        title = info.get("title") or "Untitled Course"
        subtitle_target = workdir / "subtitles"
        self._run(
            [
                "yt-dlp",
                "--skip-download",
                "--write-sub",
                "--sub-langs",
                language,
                "--sub-format",
                "vtt",
                "-o",
                str(subtitle_target),
                url,
            ]
        )
        native_vtt = subtitle_target.with_suffix(f".{language}.vtt")
        if native_vtt.exists():
            final_path = workdir / "subtitles.vtt"
            native_vtt.replace(final_path)
            return SubtitleDownloadResult(title, final_path, workdir / "transcript.json", "native", language)

        self._run(
            [
                "yt-dlp",
                "--skip-download",
                "--write-auto-sub",
                "--sub-langs",
                language,
                "--sub-format",
                "vtt",
                "-o",
                str(subtitle_target),
                url,
            ]
        )
        auto_vtt = subtitle_target.with_suffix(f".{language}.vtt")
        if auto_vtt.exists():
            final_path = workdir / "subtitles.vtt"
            auto_vtt.replace(final_path)
            return SubtitleDownloadResult(title, final_path, workdir / "transcript.json", "auto", language)
        raise RuntimeError(f"No subtitles available for language '{language}'.")

    def _extract_info(self, url: str) -> dict:
        output = run_command(["yt-dlp", "--dump-single-json", "--skip-download", url])
        return json.loads(output.stdout)

    @staticmethod
    def _run(command: list[str]) -> None:
        run_command(command)
