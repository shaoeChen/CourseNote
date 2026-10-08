from __future__ import annotations


def scene_frame_filename(scene: dict[str, float]) -> str:
    """Preserve legacy names; use millisecond precision for verified slide scenes."""
    filename = format_compact_filename(scene["end_sec"])
    if "capture_sec" in scene:
        return f"{filename[:-4]}_{round(scene['end_sec'] * 1000):010d}.jpg"
    return filename


def format_hhmmss(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_compact_filename(seconds: float) -> str:
    return format_hhmmss(seconds).replace(":", "") + ".jpg"
