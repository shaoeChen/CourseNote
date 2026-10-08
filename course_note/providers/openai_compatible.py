from __future__ import annotations

import base64
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProviderConfig:
    backend: str
    model: str
    api_base: str
    api_key: str
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ProviderDefaults:
    env_prefix: str
    default_api_base: str
    default_api_key: str
    default_model: str


class OpenAICompatibleClient:
    DEFAULTS = {
        "ollama": ProviderDefaults(
            env_prefix="OLLAMA",
            default_api_base="http://localhost:11434/v1",
            default_api_key="ollama",
            default_model="qwen2.5:7b",
        ),
        "vllm": ProviderDefaults(
            env_prefix="VLLM",
            default_api_base="http://localhost:8000/v1",
            default_api_key="EMPTY",
            default_model="Qwen/Qwen2.5-7B-Instruct",
        ),
        "llama.cpp": ProviderDefaults(
            env_prefix="LLAMACPP",
            default_api_base="http://localhost:8080/v1",
            default_api_key="EMPTY",
            default_model="Qwen/Qwen2.5-7B-Instruct",
        ),
        "zai": ProviderDefaults(
            env_prefix="ZAI",
            default_api_base="https://open.bigmodel.cn/api/paas/v4",
            default_api_key="",
            default_model="glm-4.5-air",
        ),
        "deepseek": ProviderDefaults(
            env_prefix="DEEPSEEK",
            default_api_base="https://api.deepseek.com",
            default_api_key="",
            default_model="deepseek-v4-flash",
        ),
        "clinepass": ProviderDefaults(
            env_prefix="CLINEPASS",
            default_api_base="https://api.cline.bot/api/v1",
            default_api_key="",
            default_model="cline-pass/deepseek-v4-flash",
        ),
    }

    BACKEND_ALIASES = {
        "ollama": "ollama",
        "vllm": "vllm",
        "llama.cpp": "llama.cpp",
        "llamacpp": "llama.cpp",
        "zai": "zai",
        "deepseek": "deepseek",
        "deepseek-flash": "deepseek",
        "clinepass": "clinepass",
        "cline-pass": "clinepass",
    }

    def resolve_config(
        self,
        backend: str | None,
        timeout_seconds: int | None = None,
    ) -> ProviderConfig:
        resolved_backend = self._resolve_backend_name(backend)
        defaults = self.DEFAULTS[resolved_backend]
        api_base = self._resolve_api_base(defaults)
        api_key = self._resolve_api_key(defaults)
        resolved_model = self._resolve_model(defaults)
        resolved_timeout = timeout_seconds if timeout_seconds is not None else int(os.getenv("COURSE_NOTE_LLM_TIMEOUT_SECONDS", "120"))
        if not api_base:
            raise RuntimeError(f"No API base configured for backend '{resolved_backend}'.")
        return ProviderConfig(
            backend=resolved_backend,
            model=resolved_model,
            api_base=api_base,
            api_key=api_key,
            timeout_seconds=resolved_timeout,
        )

    def _resolve_backend_name(self, backend: str | None) -> str:
        requested_backend = backend or os.getenv("COURSE_NOTE_PROVIDER") or "ollama"
        normalized_backend = self.BACKEND_ALIASES.get(requested_backend.strip().lower())
        if normalized_backend is None:
            supported = ", ".join(sorted(self.DEFAULTS))
            raise RuntimeError(
                f"Unsupported LLM backend '{requested_backend}'. Supported backends: {supported}."
            )
        return normalized_backend

    def resolve_vision_config(
        self,
        backend: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ProviderConfig:
        resolved_backend = self._resolve_backend_name(
            backend or os.getenv("COURSE_NOTE_VISION_PROVIDER") or "vllm"
        )
        defaults = self.DEFAULTS[resolved_backend]
        model = (os.getenv("COURSE_NOTE_VISION_MODEL") or "").strip()
        if not model:
            raise RuntimeError("COURSE_NOTE_VISION_MODEL must explicitly name a vision-capable model.")
        timeout_value = (
            timeout_seconds if timeout_seconds is not None
            else os.getenv("COURSE_NOTE_VISION_TIMEOUT_SECONDS", "120")
        )
        timeout_error = "COURSE_NOTE_VISION_TIMEOUT_SECONDS / --vision-timeout-seconds must be a positive integer."
        if timeout_seconds is not None and (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)):
            raise RuntimeError(timeout_error)
        try:
            resolved_timeout = int(timeout_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(timeout_error) from exc
        if resolved_timeout <= 0:
            raise RuntimeError(timeout_error)
        api_base = os.getenv("COURSE_NOTE_VISION_API_BASE", self._resolve_api_base(defaults)).strip()
        api_key = os.getenv("COURSE_NOTE_VISION_API_KEY", self._resolve_api_key(defaults))
        if not api_base:
            raise RuntimeError(f"No API base configured for vision backend '{resolved_backend}'.")
        return ProviderConfig(resolved_backend, model, api_base, api_key, resolved_timeout)

    def _resolve_api_base(self, defaults: ProviderDefaults) -> str:
        env_prefix = defaults.env_prefix
        provider_api_base = os.getenv(f"COURSE_NOTE_{env_prefix}_API_BASE")
        if provider_api_base:
            return provider_api_base

        legacy_api_base = os.getenv("COURSE_NOTE_API_BASE")
        if legacy_api_base:
            return legacy_api_base

        host = os.getenv(f"COURSE_NOTE_{env_prefix}_HOST")
        if not host:
            return defaults.default_api_base

        scheme = os.getenv(f"COURSE_NOTE_{env_prefix}_SCHEME", "http")
        port = os.getenv(f"COURSE_NOTE_{env_prefix}_PORT")
        path = os.getenv(f"COURSE_NOTE_{env_prefix}_PATH", "/v1")
        normalized_path = path if path.startswith("/") else f"/{path}"
        host_with_port = f"{host}:{port}" if port else host
        return f"{scheme}://{host_with_port}{normalized_path}"

    def _resolve_api_key(self, defaults: ProviderDefaults) -> str:
        env_prefix = defaults.env_prefix
        return (
            os.getenv(f"COURSE_NOTE_{env_prefix}_API_KEY")
            or os.getenv("COURSE_NOTE_API_KEY")
            or defaults.default_api_key
        )

    def _resolve_model(self, defaults: ProviderDefaults) -> str:
        env_prefix = defaults.env_prefix
        return (
            os.getenv(f"COURSE_NOTE_{env_prefix}_MODEL")
            or os.getenv("COURSE_NOTE_MODEL")
            or defaults.default_model
        )

    def chat(self, config: ProviderConfig, prompt: str) -> str:
        return self._chat(config, prompt)

    def chat_images(self, config: ProviderConfig, prompt: str, images: list[Path]) -> str:
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for image in images:
            image_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(image.suffix.lower())
            if image_type is None:
                raise RuntimeError(f"Unsupported image format for '{image}'; use local JPEG or PNG files.")
            try:
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            except OSError as exc:
                raise RuntimeError(f"Unable to read image '{image}': {exc}") from exc
            content.append({"type": "image_url", "image_url": {"url": f"data:{image_type};base64,{encoded}"}})
        return self._chat(config, content, vision=True)

    def _chat(
        self, config: ProviderConfig, content: str | list[dict[str, object]], *, vision: bool = False
    ) -> str:
        payload = json.dumps(
            {
                "model": config.model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url=f"{config.api_base}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("LLM response did not contain valid UTF-8 text.") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"LLM request failed with status {exc.code}: {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            timeout_setting = (
                "--vision-timeout-seconds / COURSE_NOTE_VISION_TIMEOUT_SECONDS" if vision
                else "--llm-timeout-seconds / COURSE_NOTE_LLM_TIMEOUT_SECONDS"
            )
            raise RuntimeError(
                f"LLM request to {config.api_base} timed out after {config.timeout_seconds} seconds. "
                f"Check whether the backend is running, the model '{config.model}' is available, "
                f"or increase {timeout_setting}."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Failed to reach the LLM backend at {config.api_base}: {exc.reason}"
            ) from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM response did not contain valid JSON: {body[:500]}") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"LLM response must be a JSON object: {body[:500]}")
        choices = result.get("choices")
        data = result.get("data")
        if not choices and isinstance(data, dict):
            choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"LLM response did not include choices: {body[:500]}")
        first_choice = choices[0]
        message = first_choice.get("message") if isinstance(first_choice, dict) else None
        response_content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(response_content, str) or not response_content.strip():
            raise RuntimeError(f"LLM response did not include nonempty text message content: {body[:500]}")
        return response_content.strip()
