from __future__ import annotations

from pathlib import Path

from course_note.models import TranscriptSegment


class SubtitleError(RuntimeError):
    """Raised when subtitles cannot be loaded or parsed."""


class SubtitleParser:
    def parse_vtt(self, subtitle_path: Path, lang: str, source: str) -> list[TranscriptSegment]:
        if not subtitle_path.exists():
            raise SubtitleError(f"Subtitle file not found: {subtitle_path}")
        content = subtitle_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
        segments: list[TranscriptSegment] = []
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines or lines[0] == "WEBVTT":
                continue
            if "-->" not in lines[0]:
                lines = lines[1:]
            if not lines or "-->" not in lines[0]:
                continue
            start_raw, end_raw = [part.strip().split(" ")[0] for part in lines[0].split("-->")]
            text = " ".join(lines[1:]).strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start_sec=self._parse_timestamp(start_raw),
                    end_sec=self._parse_timestamp(end_raw),
                    text=text,
                    lang=lang,
                    source=source,
                )
            )
        if not segments:
            raise SubtitleError("Subtitle file contains no usable transcript segments.")
        return segments

    @staticmethod
    def _parse_timestamp(raw: str) -> float:
        normalized = raw.replace(",", ".")
        hhmmss, millis = normalized.split(".")
        hours, minutes, seconds = [int(part) for part in hhmmss.split(":")]
        return hours * 3600 + minutes * 60 + seconds + int(millis[:3].ljust(3, "0")) / 1000
