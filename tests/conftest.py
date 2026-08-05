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
