from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


_EXTERNAL_CONFIGURATION_KEYS = {"HACKMD_API", "HACKMD_API_KEY", "HACKMD_API_BASE"}


def _configuration_keys() -> set[str]:
    return {
        key
        for key in os.environ
        if key.startswith("COURSE_NOTE_") or key in _EXTERNAL_CONFIGURATION_KEYS
    }


@pytest.fixture(autouse=True)
def isolate_configuration_environment() -> Iterator[None]:
    original = {key: os.environ[key] for key in _configuration_keys()}
    for key in original:
        os.environ.pop(key, None)

    try:
        yield
    finally:
        for key in _configuration_keys():
            os.environ.pop(key, None)
        os.environ.update(original)


@pytest.fixture(autouse=True)
def isolate_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep a developer's ./course-note.toml from changing CLI defaults in tests."""
    from course_note.cli import config as cli_config

    missing = tmp_path_factory.mktemp("no-config") / cli_config.CONFIG_FILENAME
    monkeypatch.setattr(cli_config, "default_config_path", lambda: missing)
