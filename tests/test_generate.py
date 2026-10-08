from __future__ import annotations

from pathlib import Path

import pytest

from course_note.cli import main as cli_main
from course_note.models import SlideSegment
from course_note.subtitle.downloader import SubtitleDownloadResult
from course_note.utils.io import (
    CourseWorkspaceMetadata,
    load_course_workspace_metadata,
    resolve_course_workdir,
    write_course_workspace_metadata,
)


class _FakeLLMService:
    def summarize_segments(
        self,
        segments: list[SlideSegment],
        backend: str,
        note_style: str,
        include_extra_notes: bool,
    ) -> list[dict[str, str | None]]:
        for segment in segments:
            segment.summary = f"{note_style}:{segment.transcript}"
            segment.extra_notes = "extra" if include_extra_notes else None
        return [
            {"summary": segment.summary, "extra_notes": segment.extra_notes}
            for segment in segments
        ]

    def summarize_course(self, segments, backend: str) -> str:
        raise AssertionError("summarize_course should not be called in this test")

    def extract_key_terms(self, segments, backend: str) -> list[str]:
        raise AssertionError("extract_key_terms should not be called in this test")


def test_generate_impl_reuses_cached_video_and_subtitle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_workdir = tmp_path / "workdir"
    course_workdir = resolve_course_workdir(base_workdir, "Demo Course")
    (course_workdir / "subtitles.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhello world\n",
        encoding="utf-8",
    )
    (course_workdir / "video.mp4").write_text("video", encoding="utf-8")
    write_course_workspace_metadata(
        course_workdir,
        CourseWorkspaceMetadata(
            course_name="Demo Course",
            source_url="https://example.com/watch?v=demo",
            title="Stored Title",
            subtitle_language="zh-TW",
            subtitle_source="native",
        ),
    )

    frame_calls: list[tuple[Path, Path, list[dict[str, float]], float]] = []
    detect_calls: list[tuple[Path, Path]] = []

    class _FailingSubtitleDownloader:
        def download(self, url: str, workdir: Path, language: str) -> None:
            raise AssertionError("subtitle download should be skipped when cache is present")

    class _FailingVideoDownloader:
        def download(self, url: str, workdir: Path) -> Path:
            raise AssertionError("video download should be skipped when cache is present")

    class _FakeSceneDetectorService:
        def detect(
            self,
            video_path: Path,
            threshold: float,
            min_segment_seconds: int,
            *,
            edge_weight: float,
            stats_csv_path: Path,
        ) -> list[dict[str, float]]:
            detect_calls.append((video_path, stats_csv_path))
            return [{"start_sec": 0.0, "end_sec": 5.0}]

    class _FakeFrameSampler:
        def sample_for_scenes(
            self,
            video_path: Path,
            frames_dir: Path,
            scenes: list[dict[str, float]],
            sample_offset_seconds: float,
        ) -> None:
            frame_calls.append((video_path, frames_dir, scenes, sample_offset_seconds))

    class _FakeSegmentBuilder:
        def build(
            self,
            scenes: list[dict[str, float]],
            transcripts,
            frames_dir: Path,
        ) -> list[SlideSegment]:
            return [
                SlideSegment(
                    start_sec=scenes[0]["start_sec"],
                    end_sec=scenes[0]["end_sec"],
                    screenshot="000005.jpg",
                    transcript=transcripts[0].text,
                    summary="",
                    extra_notes=None,
                )
            ]

    class _FakeMarkdownRenderer:
        def render(self, note, include_transcript: bool = False) -> str:
            return f"# {note.title}\n\n{note.url}\n"

    monkeypatch.setattr(cli_main, "YouTubeSubtitleDownloader", lambda: _FailingSubtitleDownloader())
    monkeypatch.setattr(cli_main, "VideoDownloader", lambda: _FailingVideoDownloader())
    monkeypatch.setattr(cli_main, "SceneDetectorService", lambda: _FakeSceneDetectorService())
    monkeypatch.setattr(cli_main, "FrameSampler", lambda: _FakeFrameSampler())
    monkeypatch.setattr(cli_main, "SegmentBuilder", lambda: _FakeSegmentBuilder())
    monkeypatch.setattr(cli_main, "LLMService", lambda: _FakeLLMService())
    monkeypatch.setattr(cli_main, "MarkdownRenderer", lambda: _FakeMarkdownRenderer())

    cli_main.generate_impl(
        url="https://example.com/watch?v=demo",
        course_name="Demo Course",
        workdir=base_workdir,
    )

    metadata = load_course_workspace_metadata(course_workdir)

    assert (course_workdir / "article.md").read_text(encoding="utf-8").startswith("# Stored Title")
    assert detect_calls == [(course_workdir / "video.mp4", course_workdir / "scene_stats.csv")]
    assert frame_calls == [
        (
            course_workdir / "video.mp4",
            course_workdir / "frames",
            [{"start_sec": 0.0, "end_sec": 5.0}],
            1.0,
        )
    ]
    assert (course_workdir / "transcript.json").exists()
    assert (course_workdir / "segments.json").exists()
    assert (course_workdir / "llm_outputs.json").exists()
    assert metadata is not None
    assert metadata.title == "Stored Title"
    assert metadata.source_url == "https://example.com/watch?v=demo"


def test_generate_impl_rejects_reusing_course_name_with_different_url(tmp_path: Path) -> None:
    base_workdir = tmp_path / "workdir"
    course_workdir = resolve_course_workdir(base_workdir, "Demo Course")
    write_course_workspace_metadata(
        course_workdir,
        CourseWorkspaceMetadata(
            course_name="Demo Course",
            source_url="https://example.com/watch?v=old",
            title="Stored Title",
            subtitle_language="zh-TW",
            subtitle_source="native",
        ),
    )

    with pytest.raises(ValueError, match="different --course-name"):
        cli_main.generate_impl(
            url="https://example.com/watch?v=new",
            course_name="Demo Course",
            workdir=base_workdir,
        )


def test_download_subtitles_impl_writes_subtitle_transcript_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_workdir = tmp_path / "workdir"
    course_workdir = resolve_course_workdir(base_workdir, "Demo Course")

    class _FakeSubtitleDownloader:
        def download(self, url: str, workdir: Path, language: str) -> SubtitleDownloadResult:
            assert url == "https://example.com/watch?v=demo"
            assert workdir == course_workdir
            assert language == "en"
            subtitle_path = workdir / "subtitles.vtt"
            subtitle_path.write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhello world\n",
                encoding="utf-8",
            )
            return SubtitleDownloadResult(
                title="Downloaded Title",
                subtitle_path=subtitle_path,
                transcript_json_path=workdir / "transcript.json",
                source="native",
                language=language,
            )

    class _FailingVideoDownloader:
        def download(self, url: str, workdir: Path) -> Path:
            raise AssertionError("video download should not run when only downloading subtitles")

    class _FailingLLMService:
        def summarize_segments(self, *args, **kwargs):
            raise AssertionError("LLM should not run when only downloading subtitles")

    monkeypatch.setattr(cli_main, "YouTubeSubtitleDownloader", lambda **kwargs: _FakeSubtitleDownloader())
    monkeypatch.setattr(cli_main, "VideoDownloader", lambda: _FailingVideoDownloader())
    monkeypatch.setattr(cli_main, "LLMService", lambda: _FailingLLMService())

    cli_main.download_subtitles_impl(
        url="https://example.com/watch?v=demo",
        course_name="Demo Course",
        subtitle_lang="en",
        workdir=base_workdir,
    )

    metadata = load_course_workspace_metadata(course_workdir)

    assert (course_workdir / "subtitles.vtt").exists()
    assert (course_workdir / "transcript.json").exists()
    assert not (course_workdir / "video.mp4").exists()
    assert not (course_workdir / "segments.json").exists()
    assert metadata is not None
    assert metadata.course_name == "Demo Course"
    assert metadata.source_url == "https://example.com/watch?v=demo"
    assert metadata.title == "Downloaded Title"
    assert metadata.subtitle_language == "en"
    assert metadata.subtitle_source == "native"
