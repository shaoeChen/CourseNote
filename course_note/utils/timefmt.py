from __future__ import annotations


def format_hhmmss(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_compact_filename(seconds: float) -> str:
    return format_hhmmss(seconds).replace(":", "") + ".jpg"
