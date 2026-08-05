from course_note.models import CourseNote, SlideSegment
from course_note.renderers.markdown import MarkdownRenderer


def test_render_markdown_includes_optional_sections() -> None:
    note = CourseNote(
        title="Demo Course",
        url="https://example.com/video",
        course_summary="整體摘要",
        key_terms=["CLI", "摘要"],
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
    assert "原稿：" not in output


def test_render_markdown_can_include_transcript() -> None:
    note = CourseNote(
        title="Demo Course",
        url="https://example.com/video",
        segments=[
            SlideSegment(
                start_sec=1485,
                end_sec=1532,
                screenshot="002445.jpg",
                transcript="這是一段原始字軌",
                summary="這是一段摘要",
            )
        ],
    )

    output = MarkdownRenderer().render(note, include_transcript=True)

    assert "簡報：002445.jpg" in output
    assert "時間：00:24:45 ~ 00:25:32" in output
    assert "原稿：" in output
    assert "這是一段原始字軌" in output
    assert "重點：" in output
