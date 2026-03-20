from course_note.models import CourseNote, SlideSegment
from course_note.renderers.markdown import MarkdownRenderer


def test_render_markdown_includes_optional_sections() -> None:
    note = CourseNote(
        title="Demo Course",
        url="https://example.com/video",
        course_summary="整體摘要",
        key_terms=["CLI", "字幕"],
        segments=[
            SlideSegment(
                start_sec=0,
                end_sec=72,
                screenshot="000000.jpg",
                transcript="demo transcript",
                summary="這是摘要",
                extra_notes="補充內容",
            )
        ],
    )

    output = MarkdownRenderer().render(note)

    assert "## 全課總結" in output
    assert "簡報：000000.jpg" in output
    assert "補充說明：" in output
