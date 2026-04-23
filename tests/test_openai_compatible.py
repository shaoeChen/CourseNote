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


def test_resolve_config_uses_provider_specific_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleClient()
    monkeypatch.setenv("COURSE_NOTE_PROVIDER", "llamacpp")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_HOST", "192.168.1.50")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_PORT", "8088")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_MODEL", "llama-3")

    config = client.resolve_config(None, None)

    assert config.backend == "llama.cpp"
    assert config.model == "llama-3"
    assert config.api_base == "http://192.168.1.50:8088/v1"
    assert config.api_key == "EMPTY"


def test_resolve_config_prefers_provider_specific_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAICompatibleClient()
    monkeypatch.setenv("COURSE_NOTE_PROVIDER", "vllm")
    monkeypatch.setenv("COURSE_NOTE_API_BASE", "http://legacy-host:9999/v1")
    monkeypatch.setenv("COURSE_NOTE_VLLM_API_BASE", "http://vllm-service:9000/custom")

    config = client.resolve_config(None, "demo-model")

    assert config.backend == "vllm"
    assert config.api_base == "http://vllm-service:9000/custom"
