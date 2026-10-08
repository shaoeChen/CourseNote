from __future__ import annotations

from pathlib import Path

from course_note.models import SlideSegment, TranscriptSegment
from course_note.utils.timefmt import format_compact_filename, scene_frame_filename


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
                if item.end_sec > start and item.start_sec < end
            ]
            if "capture_sec" in scene:
                screenshot_name = scene_frame_filename(scene)
                if not (frames_dir / screenshot_name).is_file():
                    raise FileNotFoundError(f"Selected slide frame is missing: {screenshot_name}")
            else:
                screenshot_name = self._resolve_screenshot(frames_dir, end)
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

    def _resolve_screenshot(self, frames_dir: Path, target_sec: float) -> str:
        desired_name = format_compact_filename(target_sec)
        desired_path = frames_dir / desired_name
        if desired_path.exists():
            return desired_name
        available = sorted(frames_dir.glob("*.jpg"))
        if not available:
            return desired_name
        return min(
            available,
            key=lambda path: abs(self._parse_compact_filename(path.stem) - target_sec),
        ).name

    @staticmethod
    def _parse_compact_filename(stem: str) -> float:
        if len(stem) != 6 or not stem.isdigit():
            return 0.0
        hours = int(stem[:2])
        minutes = int(stem[2:4])
        seconds = int(stem[4:6])
        return float(hours * 3600 + minutes * 60 + seconds)
