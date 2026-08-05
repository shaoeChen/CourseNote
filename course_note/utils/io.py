from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COURSE_METADATA_FILENAME = "course_metadata.json"
_INVALID_WINDOWS_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(slots=True)
class CourseWorkspaceMetadata:
    course_name: str
    source_url: str
    title: str
    subtitle_language: str | None = None
    subtitle_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_name": self.course_name,
            "source_url": self.source_url,
            "title": self.title,
            "subtitle_language": self.subtitle_language,
            "subtitle_source": self.subtitle_source,
        }


def ensure_workdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(parents=True, exist_ok=True)
    return path


def resolve_course_workdir(base_path: Path, course_name: str) -> Path:
    sanitized_name = sanitize_course_name(course_name)
    return ensure_workdir(base_path / sanitized_name)


def sanitize_course_name(course_name: str) -> str:
    collapsed = re.sub(r"\s+", " ", course_name).strip()
    sanitized = _INVALID_WINDOWS_PATH_CHARS.sub("-", collapsed).strip(" .")
    if not sanitized:
        raise ValueError("Course name must contain at least one usable character.")
    return sanitized


def load_course_workspace_metadata(workdir: Path) -> CourseWorkspaceMetadata | None:
    metadata_path = workdir / COURSE_METADATA_FILENAME
    if not metadata_path.exists():
        return None
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid course metadata payload in {metadata_path}")
    return CourseWorkspaceMetadata(
        course_name=str(payload["course_name"]),
        source_url=str(payload["source_url"]),
        title=str(payload["title"]),
        subtitle_language=str(payload["subtitle_language"]) if payload.get("subtitle_language") else None,
        subtitle_source=str(payload["subtitle_source"]) if payload.get("subtitle_source") else None,
    )


def write_course_workspace_metadata(workdir: Path, metadata: CourseWorkspaceMetadata) -> None:
    write_json(workdir / COURSE_METADATA_FILENAME, metadata.to_dict())


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
