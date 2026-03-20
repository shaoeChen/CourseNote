from __future__ import annotations

from pathlib import Path

from course_note.models import SlideSegment, TranscriptSegment
from course_note.utils.timefmt import format_compact_filename


class SegmentBuilder:
    def build(
        self,
        scenes: list[dict[str, float]],
        transcripts: list[TranscriptSegment],
        frames_dir: Path,
    ) -> list[SlideSegment]:
        segments: list[SlideSegment] = []
        for scene in scenes:
            start = scene["start_sec"]
            end = scene["end_sec"]
            text_parts = [
                item.text
                for item in transcripts
                if item.end_sec >= start and item.start_sec <= end
            ]
            screenshot_name = self._resolve_screenshot(frames_dir, start)
            segments.append(
                SlideSegment(
                    start_sec=start,
                    end_sec=end,
                    screenshot=screenshot_name,
                    transcript=" ".join(text_parts).strip(),
                    summary="",
                    extra_notes=None,
                )
            )
        return segments

    def _resolve_screenshot(self, frames_dir: Path, start_sec: float) -> str:
        desired_name = format_compact_filename(start_sec)
        desired_path = frames_dir / desired_name
        if desired_path.exists():
            return desired_name
        available = sorted(frames_dir.glob("*.jpg"))
        if not available:
            return desired_name
        return available[min(int(start_sec), len(available) - 1)].name
