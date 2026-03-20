from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class ProviderConfig:
    backend: str
    model: str
    api_base: str
    api_key: str


class OpenAICompatibleClient:
    DEFAULTS = {
        "ollama": ("http://localhost:11434/v1", "ollama"),
        "vllm": ("http://localhost:8000/v1", "EMPTY"),
        "llama.cpp": ("http://localhost:8080/v1", "EMPTY"),
        "zai": ("https://open.bigmodel.cn/api/paas/v4", ""),
    }

    def resolve_config(self, backend: str, model: str) -> ProviderConfig:
        default_base, default_key = self.DEFAULTS.get(backend, (os.getenv("OPENAI_API_BASE", ""), os.getenv("OPENAI_API_KEY", "")))
        api_base = os.getenv("COURSE_NOTE_API_BASE", default_base)
        api_key = os.getenv("COURSE_NOTE_API_KEY", default_key)
        if not api_base:
            raise RuntimeError(f"No API base configured for backend '{backend}'.")
        return ProviderConfig(backend=backend, model=model, api_base=api_base, api_key=api_key)

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
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"LLM request failed with status {exc.code}: {detail}") from exc
        result = json.loads(body)
        return result["choices"][0]["message"]["content"].strip()
