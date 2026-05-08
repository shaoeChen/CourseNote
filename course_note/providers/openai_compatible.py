from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass


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
    }

    BACKEND_ALIASES = {
        "ollama": "ollama",
        "vllm": "vllm",
        "llama.cpp": "llama.cpp",
        "llamacpp": "llama.cpp",
        "zai": "zai",
        "deepseek": "deepseek",
        "deepseek-flash": "deepseek",
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
        payload = json.dumps(
            {
                "model": config.model,
                "messages": [{"role": "user", "content": prompt}],
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
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"LLM request failed with status {exc.code}: {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeError(
                f"LLM request to {config.api_base} timed out after {config.timeout_seconds} seconds. "
                f"Check whether the backend is running, the model '{config.model}' is available, "
                "or increase --llm-timeout-seconds / COURSE_NOTE_LLM_TIMEOUT_SECONDS."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Failed to reach the LLM backend at {config.api_base}: {exc.reason}"
            ) from exc
        result = json.loads(body)
        return result["choices"][0]["message"]["content"].strip()
