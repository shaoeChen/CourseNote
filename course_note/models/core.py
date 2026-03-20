from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class TranscriptSegment:
    start_sec: float
    end_sec: float
    text: str
    lang: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SlideSegment:
    start_sec: float
    end_sec: float
    screenshot: str
    transcript: str
    summary: str
    extra_notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CourseNote:
    title: str
    url: str
    segments: list[SlideSegment]
    course_summary: str | None = None
    key_terms: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "segments": [segment.to_dict() for segment in self.segments],
            "course_summary": self.course_summary,
            "key_terms": self.key_terms,
        }
