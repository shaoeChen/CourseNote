from pathlib import Path

from course_note.subtitle.parser import SubtitleParser


def test_parse_vtt(tmp_path: Path) -> None:
    subtitle_file = tmp_path / "sample.vtt"
    subtitle_file.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n\n00:00:02.500 --> 00:00:05.000\nSecond line\n",
        encoding="utf-8",
    )

    segments = SubtitleParser().parse_vtt(subtitle_file, lang="en", source="native")

    assert len(segments) == 2
    assert segments[0].start_sec == 0.0
    assert segments[1].text == "Second line"
