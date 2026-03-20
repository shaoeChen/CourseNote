from __future__ import annotations

from course_note.models import SlideSegment
from course_note.prompts.templates import EXTRA_NOTES_PROMPT, TEMPLATES
from course_note.providers.openai_compatible import OpenAICompatibleClient


class LLMService:
    def __init__(self, client: OpenAICompatibleClient | None = None) -> None:
        self.client = client or OpenAICompatibleClient()

    def summarize_segments(
        self,
        segments: list[SlideSegment],
        backend: str,
        model: str,
        note_style: str,
        include_extra_notes: bool,
    ) -> list[dict[str, str | None]]:
        if note_style not in TEMPLATES:
            raise ValueError(f"Unsupported note style: {note_style}")
        config = self.client.resolve_config(backend, model)
        results: list[dict[str, str | None]] = []
        for segment in segments:
            prompt = TEMPLATES[note_style].format(text=segment.transcript or "（本段沒有可用字幕內容）")
            if include_extra_notes:
                prompt = f"{prompt}\n\n{EXTRA_NOTES_PROMPT}"
            content = self.client.chat(config, prompt)
            summary, extra_notes = self._split_extra_notes(content) if include_extra_notes else (content, None)
            segment.summary = summary.strip()
            segment.extra_notes = extra_notes.strip() if extra_notes else None
            results.append({"summary": segment.summary, "extra_notes": segment.extra_notes})
        return results

    def summarize_course(self, segments: list[SlideSegment], backend: str, model: str) -> str:
        config = self.client.resolve_config(backend, model)
        merged = "\n".join(segment.summary for segment in segments if segment.summary)
        prompt = f"請根據以下各段課程重點，輸出整堂課的總結。\n\n{merged}"
        return self.client.chat(config, prompt)

    def extract_key_terms(self, segments: list[SlideSegment], backend: str, model: str) -> list[str]:
        config = self.client.resolve_config(backend, model)
        merged = "\n".join(segment.transcript for segment in segments if segment.transcript)
        prompt = f"請從以下課程內容萃取關鍵術語，使用一行一個詞彙輸出。\n\n{merged}"
        content = self.client.chat(config, prompt)
        return [line.strip("-• ") for line in content.splitlines() if line.strip()]

    @staticmethod
    def _split_extra_notes(content: str) -> tuple[str, str | None]:
        marker = "補充說明"
        if marker not in content:
            return content, None
        head, tail = content.split(marker, 1)
        return head.strip(), tail.lstrip("：:\n ")
