from __future__ import annotations

import os
import urllib.request

import pytest

from course_note.providers.openai_compatible import OpenAICompatibleClient, ProviderConfig


def test_chat_timeout_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleClient()
    config = ProviderConfig(
        backend="ollama",
        model="demo",
        api_base="http://localhost:11434/v1",
        api_key="ollama",
        timeout_seconds=300,
    )

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)

    with pytest.raises(RuntimeError, match="timed out after 300 seconds"):
        client.chat(config, "hello")


def test_resolve_config_reads_llm_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleClient()
    monkeypatch.setenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", "240")

    config = client.resolve_config("ollama", "demo")

    assert config.api_base == "http://localhost:11434/v1"
    assert config.timeout_seconds == 240
