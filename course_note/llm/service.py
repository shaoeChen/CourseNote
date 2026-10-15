from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from course_note.models import SlideSegment
from course_note.prompts.templates import EXTRA_NOTES_PROMPT, TEMPLATES
from course_note.providers.openai_compatible import OpenAICompatibleClient, ProviderConfig


WAIT_LOG_SECONDS = 10.0


class LLMService:
    def __init__(self, client: OpenAICompatibleClient | None = None, *, log_path: Path | None = None) -> None:
        self.client = client or OpenAICompatibleClient()
        self.log_path = log_path
        self._requests = 0
        self._log_lock = threading.Lock()
        self._api_key = ''

    def _emit(self, event: str, message: str) -> None:
        message = ' '.join(message.split())
        if self._api_key:
            message = message.replace(self._api_key, '[REDACTED]')
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} [{event}] {message}"
        with self._log_lock:
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open('a', encoding='utf-8') as log:
                    log.write(line + '\n')
            print(line, flush=True)

    def _chat(self, config: ProviderConfig, prompt: str, task: str) -> str:
        self._api_key = getattr(config, 'api_key', '')
        self._requests += 1
        started = time.perf_counter()
        self._emit('TEXT_REQUEST', f'task={task} model={config.model} requests={self._requests}')
        stop = threading.Event()

        def waiting() -> None:
            while not stop.wait(WAIT_LOG_SECONDS):
                self._emit('TEXT_WAIT', f'task={task} 等待模型回應 elapsed={time.perf_counter() - started:.1f}s requests={self._requests}')

        heartbeat = threading.Thread(target=waiting, daemon=True)
        heartbeat.start()
        try:
            content = self.client.chat(config, prompt)
        except KeyboardInterrupt:
            self._emit('TEXT_CANCELLED', f'task={task} 已中止 requests={self._requests}；已送出的請求仍可能計費。')
            raise
        except Exception as exc:
            self._emit('TEXT_ERROR', f'task={task} elapsed={time.perf_counter() - started:.1f}s requests={self._requests} '
                       f'{type(exc).__name__}: {str(exc)[:500]}')
            raise
        finally:
            stop.set()
            heartbeat.join()
        self._emit('TEXT_RESPONSE', f'task={task} elapsed={time.perf_counter() - started:.1f}s chars={len(content)} requests={self._requests}')
        metadata = getattr(self.client, 'last_response_metadata', None)
        if isinstance(metadata, dict) and isinstance(metadata.get('usage'), dict):
            usage = metadata['usage']
            self._emit('TEXT_USAGE', f'task={task} finish_reason={metadata.get("finish_reason")} '
                       f'prompt_tokens={usage.get("prompt_tokens")} completion_tokens={usage.get("completion_tokens")} '
                       f'total_tokens={usage.get("total_tokens")}')
        return content

    def summarize_segments(
        self,
        segments: list[SlideSegment],
        backend: str | None,
        note_style: str,
        include_extra_notes: bool,
        timeout_seconds: int | None = None,
    ) -> list[dict[str, str | None]]:
        if note_style not in TEMPLATES:
            raise ValueError(f"Unsupported note style: {note_style}")
        config = self.client.resolve_config(backend, timeout_seconds=timeout_seconds)
        self._api_key = getattr(config, 'api_key', '')
        started = time.perf_counter()
        self._emit('TEXT_START', f'開始文字改寫 segments={len(segments)} style={note_style} model={config.model}；日誌：{self.log_path}')
        results: list[dict[str, str | None]] = []
        for index, segment in enumerate(segments, 1):
            self._emit(f'TEXT_SEGMENT {index}/{len(segments)}', f'{segment.start_sec:.3f}–{segment.end_sec:.3f} 秒 requests={self._requests}')
            prompt = TEMPLATES[note_style].format(text=segment.transcript or "（本段沒有可用字幕內容）")
            if include_extra_notes:
                prompt = f"{prompt}\n\n{EXTRA_NOTES_PROMPT}"
            content = self._chat(config, prompt, f'segment={index}/{len(segments)}')
            summary, extra_notes = self._split_extra_notes(content) if include_extra_notes else (content, None)
            segment.summary = summary.strip()
            segment.extra_notes = extra_notes.strip() if extra_notes else None
            results.append({"summary": segment.summary, "extra_notes": segment.extra_notes})
            self._emit(f'TEXT_RESULT {index}/{len(segments)}', f'本段完成 chars={len(segment.summary)} requests={self._requests}')
        self._emit('TEXT_DONE', f'逐段文字改寫完成 segments={len(segments)} elapsed={time.perf_counter() - started:.1f}s requests={self._requests}')
        return results

    def summarize_course(
        self,
        segments: list[SlideSegment],
        backend: str | None,
        timeout_seconds: int | None = None,
    ) -> str:
        config = self.client.resolve_config(backend, timeout_seconds=timeout_seconds)
        merged = "\n".join(segment.summary for segment in segments if segment.summary)
        prompt = f"請根據以下各段課程重點，輸出整堂課的總結。\n\n{merged}"
        self._emit('TEXT_COURSE_SUMMARY', '開始整理課程總結')
        return self._chat(config, prompt, 'course_summary')

    def extract_key_terms(
        self,
        segments: list[SlideSegment],
        backend: str | None,
        timeout_seconds: int | None = None,
    ) -> list[str]:
        config = self.client.resolve_config(backend, timeout_seconds=timeout_seconds)
        merged = "\n".join(segment.transcript for segment in segments if segment.transcript)
        prompt = f"請從以下課程內容萃取關鍵術語，使用一行一個詞彙輸出。\n\n{merged}"
        self._emit('TEXT_KEY_TERMS', '開始整理關鍵詞')
        content = self._chat(config, prompt, 'key_terms')
        return [line.strip("-• ") for line in content.splitlines() if line.strip()]

    @staticmethod
    def _split_extra_notes(content: str) -> tuple[str, str | None]:
        marker = "補充說明"
        if marker not in content:
            return content, None
        head, tail = content.split(marker, 1)
        return head.strip(), tail.lstrip("：:\n ")
