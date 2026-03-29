from __future__ import annotations

from course_note.models import CourseNote
from course_note.utils.timefmt import format_hhmmss


class MarkdownRenderer:
    def render(self, note: CourseNote, include_transcript: bool = False) -> str:
        sections = [f"# {note.title}", "", f"來源：{note.url}", ""]
        if note.course_summary:
            sections.extend(["## 全課總結", note.course_summary, ""])
        if note.key_terms:
            sections.append("## 關鍵詞")
            sections.extend(f"- {term}" for term in note.key_terms)
            sections.append("")

        for segment in note.segments:
            sections.extend(
                [
                    "##",
                    f"簡報：{segment.screenshot}",
                    f"時間：{format_hhmmss(segment.start_sec)} ~ {format_hhmmss(segment.end_sec)}",
                    "",
                ]
            )
            if include_transcript:
                sections.extend(
                    [
                        "原稿：",
                        segment.transcript or "（無原始字軌）",
                        "",
                    ]
                )
            sections.extend(
                [
                    "重點：",
                    segment.summary or "（無摘要）",
                    "",
                ]
            )
            if segment.extra_notes:
                sections.extend(["補充說明：", segment.extra_notes, ""])
        return "\n".join(sections).strip() + "\n"
