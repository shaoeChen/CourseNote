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


@pytest.mark.parametrize("provider", list(OpenAICompatibleClient.DEFAULTS))
def test_vision_reuses_selected_provider_configuration(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    prefix = OpenAICompatibleClient.DEFAULTS[provider].env_prefix
    monkeypatch.setenv("COURSE_NOTE_PROVIDER", "ollama")
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", provider)
    monkeypatch.setenv(f"COURSE_NOTE_{prefix}_MODEL", "configured-model")
    monkeypatch.setenv(f"COURSE_NOTE_{prefix}_API_BASE", "http://provider.test/v1")
    monkeypatch.setenv(f"COURSE_NOTE_{prefix}_API_KEY", "provider-key")
    monkeypatch.setenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", "240")
    client = OpenAICompatibleClient()

    assert client.resolve_vision_config() == client.resolve_config(provider)
    assert client.resolve_vision_config().model == "configured-model"


@pytest.mark.parametrize("provider", list(OpenAICompatibleClient.DEFAULTS))
def test_vision_uses_existing_provider_model_defaults(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", provider)
    client = OpenAICompatibleClient()
    assert client.resolve_vision_config() == client.resolve_config(provider)


def test_vision_defaults_to_the_active_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_PROVIDER", "ollama")
    monkeypatch.setenv("COURSE_NOTE_OLLAMA_MODEL", "gemma-model")
    client = OpenAICompatibleClient()
    assert client.resolve_vision_config() == client.resolve_config(None)


def test_vision_reuses_shared_model_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "vllm")
    monkeypatch.setenv("COURSE_NOTE_MODEL", "shared-model")
    assert OpenAICompatibleClient().resolve_vision_config().model == "shared-model"


def test_vision_provider_alias_reuses_model_and_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "llamacpp")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_MODEL", "vision")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_HOST", "vision-host")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_PORT", "8090")
    monkeypatch.setenv("COURSE_NOTE_LLAMACPP_API_KEY", "provider-key")
    config = OpenAICompatibleClient().resolve_vision_config()
    assert config == ProviderConfig("llama.cpp", "vision", "http://vision-host:8090/v1", "provider-key", 120)


def test_vision_cli_override_selects_the_other_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("COURSE_NOTE_OLLAMA_MODEL", "ollama-model")
    monkeypatch.setenv("COURSE_NOTE_DEEPSEEK_MODEL", "selected-model")
    monkeypatch.setenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", "250")
    client = OpenAICompatibleClient()
    assert client.resolve_vision_config("deepseek-flash", 300) == client.resolve_config("deepseek", 300)
    assert client.resolve_vision_config().timeout_seconds == 250


@pytest.mark.parametrize("timeout", ["0", "-1", "bad", "1.5"])
def test_vision_rejects_invalid_shared_timeout(monkeypatch: pytest.MonkeyPatch, timeout: str) -> None:
    monkeypatch.setenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", timeout)
    with pytest.raises(RuntimeError, match="COURSE_NOTE_LLM_TIMEOUT_SECONDS.*positive integer"):
        OpenAICompatibleClient().resolve_vision_config()


@pytest.mark.parametrize("timeout", [0, -5, 1.5, True])
def test_vision_rejects_invalid_cli_timeout(monkeypatch: pytest.MonkeyPatch, timeout: int | float) -> None:
    with pytest.raises(RuntimeError, match="positive integer"):
        OpenAICompatibleClient().resolve_vision_config(timeout_seconds=timeout)  # type: ignore[arg-type]


def test_vision_rejects_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_official_deepseek_vision_requests_bounded_json_without_thinking(monkeypatch, tmp_path):
    image = tmp_path / 'slide.jpg'
    image.write_bytes(b'jpeg')
    payloads = []

    def intercept(request, **kwargs):
        payloads.append(json.loads(request.data))
        return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')

    monkeypatch.setattr(urllib.request, 'urlopen', intercept)
    client = OpenAICompatibleClient()
    cfg = ProviderConfig('deepseek', 'deepseek-flash', 'https://api.deepseek.com', 'key', 120)
    client.chat_images(cfg, 'Return JSON', [image])
    assert payloads[0]['thinking'] == {'type': 'disabled'}
    assert payloads[0]['response_format'] == {'type': 'json_object'}
    assert payloads[0]['max_tokens'] == 2048
    client.chat(cfg, 'Write course notes')
    assert 'thinking' not in payloads[1] and 'max_tokens' not in payloads[1]


def test_empty_final_content_reports_finish_reason_and_preserves_complete_body(monkeypatch, config):
    body = json.dumps({'choices': [{'finish_reason': 'length', 'message': {
        'content': '', 'reasoning_content': 'private reasoning ' * 100}}],
        'usage': {'completion_tokens': 2048, 'prompt_tokens': 200, 'total_tokens': 2248}})
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(body.encode()))
    with pytest.raises(RuntimeError, match='finish_reason=length') as caught:
        OpenAICompatibleClient().chat(config, 'hello')
    assert caught.value.response_body == body
    assert 'private reasoning' not in str(caught.value)


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
    (TimeoutError("timed out"), "--vision-timeout-seconds / COURSE_NOTE_LLM_TIMEOUT_SECONDS"),
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
