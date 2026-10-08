from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from course_note.providers.openai_compatible import OpenAICompatibleClient, ProviderConfig


@pytest.fixture(autouse=True)
def clean_course_note_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("COURSE_NOTE_"):
            monkeypatch.delenv(name)


def test_vision_defaults_are_independent_of_text_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_PROVIDER", "ollama")
    monkeypatch.setenv("COURSE_NOTE_MODEL", "text-model")
    monkeypatch.setenv("COURSE_NOTE_VLLM_MODEL", "another-text-model")
    monkeypatch.setenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", "invalid")
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision-model")

    config = OpenAICompatibleClient().resolve_vision_config()

    assert config == ProviderConfig("vllm", "vision-model", "http://localhost:8000/v1", "EMPTY", 120)


@pytest.mark.parametrize("model", [None, "", "   "])
def test_vision_requires_an_explicit_model(monkeypatch: pytest.MonkeyPatch, model: str | None) -> None:
    monkeypatch.setenv("COURSE_NOTE_MODEL", "text-model")
    monkeypatch.setenv("COURSE_NOTE_VLLM_MODEL", "provider-text-model")
    if model is not None:
        monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", model)

    with pytest.raises(RuntimeError, match="COURSE_NOTE_VISION_MODEL"):
        OpenAICompatibleClient().resolve_vision_config()


def test_vision_provider_reuses_connection_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "llamacpp")
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_HOST", "vision-host")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_PORT", "8090")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_API_KEY", "provider-key")

    config = OpenAICompatibleClient().resolve_vision_config()

    assert config.backend == "llama.cpp"
    assert config.api_base == "http://vision-host:8090/v1"
    assert config.api_key == "provider-key"


def test_vision_overrides_connection_and_cli_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision")
    monkeypatch.setenv("COURSE_NOTE_VISION_API_BASE", "http://vision-host/v1")
    monkeypatch.setenv("COURSE_NOTE_VISION_API_KEY", "vision-key")
    monkeypatch.setenv("COURSE_NOTE_VISION_TIMEOUT_SECONDS", "250")

    config = OpenAICompatibleClient().resolve_vision_config("deepseek-flash", 300)

    assert config == ProviderConfig("deepseek", "vision", "http://vision-host/v1", "vision-key", 300)
    assert OpenAICompatibleClient().resolve_vision_config().timeout_seconds == 250


@pytest.mark.parametrize("timeout", ["0", "-1", "bad", "1.5"])
def test_vision_rejects_invalid_timeout(monkeypatch: pytest.MonkeyPatch, timeout: str) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision")
    monkeypatch.setenv("COURSE_NOTE_VISION_TIMEOUT_SECONDS", timeout)

    with pytest.raises(RuntimeError, match="COURSE_NOTE_VISION_TIMEOUT_SECONDS.*positive integer"):
        OpenAICompatibleClient().resolve_vision_config()


@pytest.mark.parametrize("timeout", [0, -5, 1.5, True])
def test_vision_rejects_invalid_cli_timeout(monkeypatch: pytest.MonkeyPatch, timeout: int | float) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision")

    with pytest.raises(RuntimeError, match="positive integer"):
        OpenAICompatibleClient().resolve_vision_config(timeout_seconds=timeout)  # type: ignore[arg-type]


def test_vision_rejects_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "vision")

    with pytest.raises(RuntimeError, match="Unsupported LLM backend"):
        OpenAICompatibleClient().resolve_vision_config("unsupported")


@pytest.fixture
def config() -> ProviderConfig:
    return ProviderConfig("vllm", "vision-model", "http://vision.test/v1", "key", 180)


def test_chat_images_sends_ordered_local_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: ProviderConfig
) -> None:
    images = [tmp_path / "last.JPG", tmp_path / "next.png"]
    image_bytes = [b"first jpeg bytes", b"second png bytes"]
    for path, data in zip(images, image_bytes):
        path.write_bytes(data)
    requests: list[tuple[urllib.request.Request, int]] = []

    def intercept(request: urllib.request.Request, timeout: int) -> io.BytesIO:
        requests.append((request, timeout))
        return io.BytesIO(b'{"choices":[{"message":{"content":"  same slide  "}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", intercept)

    assert OpenAICompatibleClient().chat_images(config, "compare slides", images) == "same slide"
    request, timeout = requests[0]
    assert request.full_url == "http://vision.test/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer key"
    assert request.get_header("Content-type") == "application/json"
    assert timeout == 180
    assert json.loads(request.data) == {
        "model": "vision-model",
        "temperature": 0.2,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "compare slides"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes[0]).decode("ascii")}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_bytes[1]).decode("ascii")}},
        ]}],
    }


def test_text_chat_retains_string_content(monkeypatch: pytest.MonkeyPatch, config: ProviderConfig) -> None:
    payloads: list[dict[str, object]] = []

    def intercept(request: urllib.request.Request, timeout: int) -> io.BytesIO:
        payloads.append(json.loads(request.data))
        return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr(urllib.request, "urlopen", intercept)

    assert OpenAICompatibleClient().chat(config, "hello") == "ok"
    assert payloads[0]["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize("filename", ["missing.jpg", "slide.gif"])
def test_chat_images_rejects_invalid_local_files_before_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: ProviderConfig, filename: str
) -> None:
    image = tmp_path / filename
    if filename == "slide.gif":
        image.write_bytes(b"gif bytes")

    def unexpected_request(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid local images must not cause a network request")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_request)

    with pytest.raises(RuntimeError, match="image"):
        OpenAICompatibleClient().chat_images(config, "compare", [image])


@pytest.mark.parametrize("body, message", [
    ("not json", "valid JSON"),
    ("[]", "JSON object"),
    ('{"data":42}', "choices"),
    ('{"choices":[]}', "choices"),
    ('{"choices":{}}', "choices"),
    ('{"choices":[{}]}', "message content"),
    ('{"choices":[{"message":{"content":null}}]}', "message content"),
    ('{"choices":[{"message":{"content":[]}}]}', "message content"),
    ('{"choices":[{"message":{"content":"   "}}]}', "message content"),
])
def test_chat_reports_malformed_response(
    monkeypatch: pytest.MonkeyPatch, config: ProviderConfig, body: str, message: str
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body.encode()))

    with pytest.raises(RuntimeError, match=message):
        OpenAICompatibleClient().chat(config, "hello")


def test_chat_reports_non_utf8_response(monkeypatch: pytest.MonkeyPatch, config: ProviderConfig) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"\xff"))

    with pytest.raises(RuntimeError, match="UTF-8"):
        OpenAICompatibleClient().chat(config, "hello")


@pytest.mark.parametrize("error, message", [
    (TimeoutError("timed out"), "timed out after 180 seconds"),
    (urllib.error.URLError("offline"), "Failed to reach"),
    (urllib.error.HTTPError("http://vision.test", 400, "bad", {}, io.BytesIO(b"invalid image")), "status 400: invalid image"),
])
def test_chat_images_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: ProviderConfig, error: Exception, message: str
) -> None:
    image = tmp_path / "slide.jpg"
    image.write_bytes(b"jpeg bytes")

    def intercept(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", intercept)

    with pytest.raises(RuntimeError, match=message):
        OpenAICompatibleClient().chat_images(config, "compare", [image])
