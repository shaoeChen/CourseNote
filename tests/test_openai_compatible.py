from __future__ import annotations

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
    )

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)

    with pytest.raises(RuntimeError, match="timed out after 120 seconds"):
        client.chat(config, "hello")
