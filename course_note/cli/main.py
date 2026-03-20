from __future__ import annotations

import importlib.util
from pathlib import Path

from course_note.llm.service import LLMService
from course_note.models import CourseNote
from course_note.renderers.markdown import MarkdownRenderer
from course_note.segmentation.scenes import SceneDetectorService
from course_note.segmentation.timeline import SegmentBuilder
from course_note.subtitle.downloader import YouTubeSubtitleDownloader
from course_note.subtitle.parser import SubtitleParser
from course_note.utils.io import ensure_workdir, write_json
from course_note.video.downloader import VideoDownloader
from course_note.video.frames import FrameSampler

HAS_TYPER = importlib.util.find_spec("typer") is not None
if HAS_TYPER:
    import typer
else:
    typer = None


def generate_impl(
    url: str,
    subtitle_lang: str = "zh-TW",
    sample_interval: float = 1.0,
    scene_threshold: float = 27.0,
    min_segment_seconds: int = 20,
    llm_backend: str = "ollama",
    model: str = "qwen2.5:7b",
    note_style: str = "summary",
    with_course_summary: bool = False,
    with_key_terms: bool = False,
    include_extra_notes: bool = False,
    output: Path = Path("output.md"),
    workdir: Path = Path("workdir"),
) -> None:
    workdir = ensure_workdir(workdir)

    subtitle_downloader = YouTubeSubtitleDownloader()
    subtitle_result = subtitle_downloader.download(url, workdir, subtitle_lang)

    transcripts = SubtitleParser().parse_vtt(
        subtitle_result.subtitle_path,
        lang=subtitle_result.language,
        source=subtitle_result.source,
    )
    write_json(subtitle_result.transcript_json_path, [segment.to_dict() for segment in transcripts])

    video_path = VideoDownloader().download(url, workdir)
    FrameSampler().sample(video_path, workdir / "frames", sample_interval)

    scenes = SceneDetectorService().detect(video_path, scene_threshold, min_segment_seconds)
    write_json(workdir / "scenes.json", scenes)

    segments = SegmentBuilder().build(scenes, transcripts, workdir / "frames")
    write_json(workdir / "segments.json", [segment.to_dict() for segment in segments])

    llm_service = LLMService()
    llm_outputs = llm_service.summarize_segments(
        segments,
        backend=llm_backend,
        model=model,
        note_style=note_style,
        include_extra_notes=include_extra_notes,
    )
    write_json(workdir / "llm_outputs.json", llm_outputs)

    course_summary = llm_service.summarize_course(segments, llm_backend, model) if with_course_summary else None
    key_terms = llm_service.extract_key_terms(segments, llm_backend, model) if with_key_terms else None

    note = CourseNote(
        title=subtitle_result.title,
        url=url,
        segments=segments,
        course_summary=course_summary,
        key_terms=key_terms,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(MarkdownRenderer().render(note), encoding="utf-8")
    message = f"Markdown note generated at {output}"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


if HAS_TYPER:
    app = typer.Typer(help="Generate Markdown course notes from online course videos.")

    @app.command()
    def generate(
        url: str = typer.Argument(..., help="Course video URL."),
        subtitle_lang: str = typer.Option("zh-TW", help="Preferred subtitle language."),
        sample_interval: float = typer.Option(1.0, help="Frame sampling interval in seconds."),
        scene_threshold: float = typer.Option(27.0, help="PySceneDetect content threshold."),
        min_segment_seconds: int = typer.Option(20, help="Minimum segment duration in seconds."),
        llm_backend: str = typer.Option("ollama", help="LLM backend name."),
        model: str = typer.Option("qwen2.5:7b", help="LLM model name."),
        note_style: str = typer.Option("summary", help="Prompt template name."),
        with_course_summary: bool = typer.Option(False, help="Generate whole-course summary."),
        with_key_terms: bool = typer.Option(False, help="Extract key terms."),
        include_extra_notes: bool = typer.Option(False, help="Allow explicit extra notes sections."),
        output: Path = typer.Option(Path("output.md"), help="Output Markdown file."),
        workdir: Path = typer.Option(Path("workdir"), help="Working directory for artifacts."),
    ) -> None:
        generate_impl(
            url=url,
            subtitle_lang=subtitle_lang,
            sample_interval=sample_interval,
            scene_threshold=scene_threshold,
            min_segment_seconds=min_segment_seconds,
            llm_backend=llm_backend,
            model=model,
            note_style=note_style,
            with_course_summary=with_course_summary,
            with_key_terms=with_key_terms,
            include_extra_notes=include_extra_notes,
            output=output,
            workdir=workdir,
        )
else:
    import argparse

    app = None

    def _build_argparse() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="course-note", description="Generate Markdown course notes from online course videos.")
        subparsers = parser.add_subparsers(dest="command", required=True)
        generate_parser = subparsers.add_parser("generate")
        generate_parser.add_argument("url")
        generate_parser.add_argument("--subtitle-lang", default="zh-TW")
        generate_parser.add_argument("--sample-interval", type=float, default=1.0)
        generate_parser.add_argument("--scene-threshold", type=float, default=27.0)
        generate_parser.add_argument("--min-segment-seconds", type=int, default=20)
        generate_parser.add_argument("--llm-backend", default="ollama")
        generate_parser.add_argument("--model", default="qwen2.5:7b")
        generate_parser.add_argument("--note-style", default="summary")
        generate_parser.add_argument("--with-course-summary", action="store_true")
        generate_parser.add_argument("--with-key-terms", action="store_true")
        generate_parser.add_argument("--include-extra-notes", action="store_true")
        generate_parser.add_argument("--output", type=Path, default=Path("output.md"))
        generate_parser.add_argument("--workdir", type=Path, default=Path("workdir"))
        return parser

    def main() -> None:
        parser = _build_argparse()
        args = parser.parse_args()
        if args.command == "generate":
            generate_impl(
                url=args.url,
                subtitle_lang=args.subtitle_lang,
                sample_interval=args.sample_interval,
                scene_threshold=args.scene_threshold,
                min_segment_seconds=args.min_segment_seconds,
                llm_backend=args.llm_backend,
                model=args.model,
                note_style=args.note_style,
                with_course_summary=args.with_course_summary,
                with_key_terms=args.with_key_terms,
                include_extra_notes=args.include_extra_notes,
                output=args.output,
                workdir=args.workdir,
            )


def run() -> None:
    if HAS_TYPER:
        app()
    else:
        main()


if __name__ == "__main__":
    run()
