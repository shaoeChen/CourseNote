from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

CONFIG_FILENAME = "course-note.toml"

# Built-in defaults for every `generate` option except url/course_name. A value
# resolves as: explicit CLI flag > course-note.toml [generate] > these defaults.
GENERATE_DEFAULTS: dict[str, Any] = {
    "subtitle_lang": "zh-TW",
    "sample_interval": 1.0,
    "scene_threshold": 27.0,
    "min_segment_seconds": 20,
    "scene_edge_weight": 0.0,
    "scene_mode": "algorithm",
    "vision_backend": None,
    "vision_timeout_seconds": None,
    "llm_backend": None,
    "llm_timeout_seconds": None,
    "note_style": "summary",
    "with_course_summary": False,
    "with_key_terms": False,
    "include_extra_notes": False,
    "include_transcript": False,
    "transcribe_fallback": False,
    "whisper_model": "small",
    "whisper_device": "auto",
    "workdir": Path("workdir"),
}

QUEUE_DEFAULTS: dict[str, Any] = {
    "delay_seconds": 30.0,
    "stop_on_error": False,
}

_OPTIONAL_TYPES: dict[str, type] = {
    "vision_backend": str,
    "vision_timeout_seconds": int,
    "llm_backend": str,
    "llm_timeout_seconds": int,
}


class ConfigError(ValueError):
    """Raised when course-note.toml is unreadable or contains invalid settings."""


def default_config_path() -> Path:
    return Path.cwd() / CONFIG_FILENAME


def load_config(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load and validate a config file; an explicit missing path is an error."""
    explicit = path is not None
    config_path = path if explicit else default_config_path()
    if not config_path.is_file():
        if explicit:
            raise ConfigError(f"Config file not found: {config_path}")
        return {}
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    unknown_sections = set(raw) - {"generate", "queue"}
    if unknown_sections:
        raise ConfigError(f"Unknown section(s) in {config_path}: {', '.join(sorted(unknown_sections))}")
    return {
        "generate": _validate_section(raw.get("generate", {}), GENERATE_DEFAULTS, config_path, "generate"),
        "queue": _validate_section(raw.get("queue", {}), QUEUE_DEFAULTS, config_path, "queue"),
    }


def resolve_options(
    cli_values: dict[str, Any],
    config_section: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Merge options; a CLI value of None means the flag was not passed."""
    resolved = dict(defaults)
    resolved.update(config_section)
    resolved.update({key: value for key, value in cli_values.items() if value is not None})
    return resolved


def _validate_section(
    section: Any, defaults: dict[str, Any], config_path: Path, name: str,
) -> dict[str, Any]:
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] in {config_path} must be a table.")
    validated: dict[str, Any] = {}
    for raw_key, value in section.items():
        key = raw_key.replace("-", "_")
        if key not in defaults:
            raise ConfigError(f"Unknown option '{raw_key}' in [{name}] of {config_path}.")
        validated[key] = _coerce(key, value, defaults[key], config_path, name)
    return validated


def _coerce(key: str, value: Any, default: Any, config_path: Path, section: str) -> Any:
    expected = _OPTIONAL_TYPES.get(key) or type(default)
    location = f"'{key}' in [{section}] of {config_path}"
    if expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{location} must be true or false.")
        return value
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{location} must be a number.")
        return float(value)
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{location} must be an integer.")
        return value
    if issubclass(expected, Path):
        if not isinstance(value, str):
            raise ConfigError(f"{location} must be a path string.")
        return Path(value)
    if not isinstance(value, str):
        raise ConfigError(f"{location} must be a string.")
    return value
