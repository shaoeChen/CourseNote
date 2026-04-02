from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from course_note.hackmd import HackMDPublisher
from course_note.llm.service import LLMService
from course_note.models import CourseNote, SlideSegment
from course_note.renderers.markdown import MarkdownRenderer
from course_note.segmentation.scenes import SceneDetectorService
from course_note.segmentation.timeline import SegmentBuilder
from course_note.subtitle.downloader import YouTubeSubtitleDownloader
from course_note.subtitle.parser import SubtitleParser
from course_note.utils.env import load_env_file
from course_note.utils.io import ensure_workdir, write_json
from course_note.video.downloader import VideoDownloader
from course_note.video.frames import FrameSampler

HAS_TYPER = importlib.util.find_spec("typer") is not None
if HAS_TYPER:
    import typer
else:
    typer = None


GENERATE_HELP = {
    "url": "Course video URL.",
    "subtitle_lang": "Preferred subtitle language code, for example zh-TW or en.",
    "sample_interval": "How many seconds before each segment end to capture the representative frame. A value of 1 means capture near the segment end, not near the segment start.",
    "scene_threshold": "PySceneDetect content threshold. Lower values split scenes more aggressively, higher values require larger visual changes.",
    "min_segment_seconds": "Minimum allowed scene duration in seconds. Scene cuts closer than this are merged to avoid overly short segments.",
    "scene_edge_weight": "Extra weight for edge changes during scene detection; useful when slides change layout but colors stay similar.",
    "llm_backend": "LLM backend name, such as ollama, vllm, llama.cpp, or zai.",
    "model": "LLM model name used for rewriting notes.",
    "note_style": "Prompt template name, such as summary, study_notes, structured, or near_transcript.",
    "with_course_summary": "Generate a whole-course summary section after segment notes.",
    "with_key_terms": "Extract key terms for the whole course.",
    "include_extra_notes": "Allow the LLM to emit an extra notes section when needed.",
    "include_transcript": "Include the original transcript text in each segment of the rendered Markdown.",
    "output": "Output Markdown file path.",
    "workdir": "Working directory for downloaded files and intermediate artifacts.",
}

REWRITE_HELP = {
    "workdir": "Working directory containing segments.json and llm_outputs.json.",
    "source_note": "Existing Markdown note used to recover title and URL when title/url are not passed explicitly.",
    "title": "Override note title in the regenerated Markdown.",
    "url": "Override source video URL in the regenerated Markdown.",
    "llm_backend": GENERATE_HELP["llm_backend"],
    "model": GENERATE_HELP["model"],
    "note_style": GENERATE_HELP["note_style"],
    "with_course_summary": GENERATE_HELP["with_course_summary"],
    "with_key_terms": GENERATE_HELP["with_key_terms"],
    "include_extra_notes": GENERATE_HELP["include_extra_notes"],
    "include_transcript": GENERATE_HELP["include_transcript"],
    "output": GENERATE_HELP["output"],
}

PUBLISH_HELP = {
    "note_path": "Markdown file to publish.",
    "title": "HackMD note title. Defaults to the first Markdown heading.",
    "image_dir": "Directory containing local slide images referenced by the Markdown.",
}


def generate_impl(
    url: str,
    subtitle_lang: str = "zh-TW",
    sample_interval: float = 1.0,
    scene_threshold: float = 27.0,
    min_segment_seconds: int = 20,
    scene_edge_weight: float = 0.0,
    llm_backend: str = "ollama",
    model: str = "qwen2.5:7b",
    note_style: str = "summary",
    with_course_summary: bool = False,
    with_key_terms: bool = False,
    include_extra_notes: bool = False,
    include_transcript: bool = False,
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
    scenes = SceneDetectorService().detect(
        video_path,
        scene_threshold,
        min_segment_seconds,
        edge_weight=scene_edge_weight,
        stats_csv_path=workdir / "scene_stats.csv",
    )
    write_json(workdir / "scenes.json", scenes)
    FrameSampler().sample_for_scenes(video_path, workdir / "frames", scenes, sample_interval)

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
    output.write_text(MarkdownRenderer().render(note, include_transcript=include_transcript), encoding="utf-8")
    message = f"Markdown note generated at {output}"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def rewrite_notes_impl(
    workdir: Path = Path("workdir"),
    source_note: Path | None = Path("output.md"),
    title: str | None = None,
    url: str | None = None,
    llm_backend: str = "ollama",
    model: str = "qwen2.5:7b",
    note_style: str = "summary",
    with_course_summary: bool = False,
    with_key_terms: bool = False,
    include_extra_notes: bool = False,
    include_transcript: bool = False,
    output: Path = Path("output.md"),
) -> None:
    segments = _load_segments(workdir / "segments.json")
    resolved_title, resolved_url = _resolve_note_metadata(source_note, title=title, url=url)

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
        title=resolved_title or output.stem,
        url=resolved_url or "",
        segments=segments,
        course_summary=course_summary,
        key_terms=key_terms,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(MarkdownRenderer().render(note, include_transcript=include_transcript), encoding="utf-8")
    message = f"Markdown note rewritten at {output}"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def publish_hackmd_impl(
    note_path: Path,
    title: str | None = None,
    image_dir: Path | None = Path("workdir/frames"),
) -> None:
    load_env_file()
    result = HackMDPublisher().publish_markdown(note_path, title=title, image_dir=image_dir)
    link = result.publish_link or result.permalink or f"note_id={result.note_id}"
    message = f"HackMD note created: {link} (uploaded {result.image_count} image(s))"
    if result.asset_note_id:
        message += f" (asset note {result.asset_note_id} retained for image hosting)"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def _load_segments(path: Path) -> list[SlideSegment]:
    if not path.exists():
        raise FileNotFoundError(f"Segments file not found: {path}")
    raw_segments = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_segments, list):
        raise ValueError(f"Invalid segments payload in {path}")
    return [_deserialize_slide_segment(item) for item in raw_segments]


def _deserialize_slide_segment(payload: dict[str, Any]) -> SlideSegment:
    return SlideSegment(
        start_sec=float(payload["start_sec"]),
        end_sec=float(payload["end_sec"]),
        screenshot=str(payload["screenshot"]),
        transcript=str(payload.get("transcript", "")),
        summary=str(payload.get("summary", "")),
        extra_notes=str(payload["extra_notes"]) if payload.get("extra_notes") is not None else None,
    )


def _resolve_note_metadata(
    source_note: Path | None,
    title: str | None = None,
    url: str | None = None,
) -> tuple[str | None, str | None]:
    resolved_title = title
    resolved_url = url
    if source_note is None or (resolved_title and resolved_url):
        return resolved_title, resolved_url
    parsed_title, parsed_url = _extract_note_metadata(source_note)
    return resolved_title or parsed_title, resolved_url or parsed_url


def _extract_note_metadata(note_path: Path) -> tuple[str | None, str | None]:
    if not note_path.exists():
        return None, None
    content = note_path.read_text(encoding="utf-8")
    parsed_title = next(
        (line[2:].strip() for line in content.splitlines() if line.startswith("# ")),
        None,
    )
    match = re.search(r"https?://\S+", content)
    parsed_url = match.group(0) if match else None
    return parsed_title, parsed_url


if HAS_TYPER:
    app = typer.Typer(
        help="Generate Markdown course notes from online course videos.",
        no_args_is_help=True,
    )

    @app.callback()
    def main() -> None:
        """CLI entrypoint for CourseNote commands."""
        return None

    @app.command()
    def generate(
        url: str = typer.Argument(..., help=GENERATE_HELP["url"]),
        subtitle_lang: str = typer.Option("zh-TW", help=GENERATE_HELP["subtitle_lang"]),
        sample_interval: float = typer.Option(1.0, help=GENERATE_HELP["sample_interval"]),
        scene_threshold: float = typer.Option(27.0, help=GENERATE_HELP["scene_threshold"]),
        min_segment_seconds: int = typer.Option(20, help=GENERATE_HELP["min_segment_seconds"]),
        scene_edge_weight: float = typer.Option(0.0, help=GENERATE_HELP["scene_edge_weight"]),
        llm_backend: str = typer.Option("ollama", help=GENERATE_HELP["llm_backend"]),
        model: str = typer.Option("qwen2.5:7b", help=GENERATE_HELP["model"]),
        note_style: str = typer.Option("summary", help=GENERATE_HELP["note_style"]),
        with_course_summary: bool = typer.Option(False, help=GENERATE_HELP["with_course_summary"]),
        with_key_terms: bool = typer.Option(False, help=GENERATE_HELP["with_key_terms"]),
        include_extra_notes: bool = typer.Option(False, help=GENERATE_HELP["include_extra_notes"]),
        include_transcript: bool = typer.Option(False, help=GENERATE_HELP["include_transcript"]),
        output: Path = typer.Option(Path("output.md"), help=GENERATE_HELP["output"]),
        workdir: Path = typer.Option(Path("workdir"), help=GENERATE_HELP["workdir"]),
    ) -> None:
        generate_impl(
            url=url,
            subtitle_lang=subtitle_lang,
            sample_interval=sample_interval,
            scene_threshold=scene_threshold,
            min_segment_seconds=min_segment_seconds,
            scene_edge_weight=scene_edge_weight,
            llm_backend=llm_backend,
            model=model,
            note_style=note_style,
            with_course_summary=with_course_summary,
            with_key_terms=with_key_terms,
            include_extra_notes=include_extra_notes,
            include_transcript=include_transcript,
            output=output,
            workdir=workdir,
        )

    @app.command("rewrite-notes")
    def rewrite_notes(
        workdir: Path = typer.Option(Path("workdir"), help=REWRITE_HELP["workdir"]),
        source_note: Path | None = typer.Option(Path("output.md"), help=REWRITE_HELP["source_note"]),
        title: str | None = typer.Option(None, help=REWRITE_HELP["title"]),
        url: str | None = typer.Option(None, help=REWRITE_HELP["url"]),
        llm_backend: str = typer.Option("ollama", help=REWRITE_HELP["llm_backend"]),
        model: str = typer.Option("qwen2.5:7b", help=REWRITE_HELP["model"]),
        note_style: str = typer.Option("summary", help=REWRITE_HELP["note_style"]),
        with_course_summary: bool = typer.Option(False, help=REWRITE_HELP["with_course_summary"]),
        with_key_terms: bool = typer.Option(False, help=REWRITE_HELP["with_key_terms"]),
        include_extra_notes: bool = typer.Option(False, help=REWRITE_HELP["include_extra_notes"]),
        include_transcript: bool = typer.Option(False, help=REWRITE_HELP["include_transcript"]),
        output: Path = typer.Option(Path("output.md"), help=REWRITE_HELP["output"]),
    ) -> None:
        rewrite_notes_impl(
            workdir=workdir,
            source_note=source_note,
            title=title,
            url=url,
            llm_backend=llm_backend,
            model=model,
            note_style=note_style,
            with_course_summary=with_course_summary,
            with_key_terms=with_key_terms,
            include_extra_notes=include_extra_notes,
            include_transcript=include_transcript,
            output=output,
        )

    @app.command("publish-hackmd")
    def publish_hackmd(
        note_path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, help=PUBLISH_HELP["note_path"]),
        title: str | None = typer.Option(None, help=PUBLISH_HELP["title"]),
        image_dir: Path = typer.Option(Path("workdir/frames"), help=PUBLISH_HELP["image_dir"]),
    ) -> None:
        publish_hackmd_impl(note_path=note_path, title=title, image_dir=image_dir)
else:
    import argparse

    app = None

    def _build_argparse() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="course-note", description="Generate Markdown course notes from online course videos.")
        subparsers = parser.add_subparsers(dest="command", required=True)
        generate_parser = subparsers.add_parser("generate", help="Run the full video-to-notes pipeline.")
        generate_parser.add_argument("url", help=GENERATE_HELP["url"])
        generate_parser.add_argument("--subtitle-lang", default="zh-TW", help=GENERATE_HELP["subtitle_lang"])
        generate_parser.add_argument("--sample-interval", type=float, default=1.0, help=GENERATE_HELP["sample_interval"])
        generate_parser.add_argument("--scene-threshold", type=float, default=27.0, help=GENERATE_HELP["scene_threshold"])
        generate_parser.add_argument("--min-segment-seconds", type=int, default=20, help=GENERATE_HELP["min_segment_seconds"])
        generate_parser.add_argument("--scene-edge-weight", type=float, default=0.0, help=GENERATE_HELP["scene_edge_weight"])
        generate_parser.add_argument("--llm-backend", default="ollama", help=GENERATE_HELP["llm_backend"])
        generate_parser.add_argument("--model", default="qwen2.5:7b", help=GENERATE_HELP["model"])
        generate_parser.add_argument("--note-style", default="summary", help=GENERATE_HELP["note_style"])
        generate_parser.add_argument("--with-course-summary", action="store_true", help=GENERATE_HELP["with_course_summary"])
        generate_parser.add_argument("--with-key-terms", action="store_true", help=GENERATE_HELP["with_key_terms"])
        generate_parser.add_argument("--include-extra-notes", action="store_true", help=GENERATE_HELP["include_extra_notes"])
        generate_parser.add_argument("--include-transcript", action="store_true", help=GENERATE_HELP["include_transcript"])
        generate_parser.add_argument("--output", type=Path, default=Path("output.md"), help=GENERATE_HELP["output"])
        generate_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=GENERATE_HELP["workdir"])
        rewrite_parser = subparsers.add_parser("rewrite-notes", help="Reuse existing segments and only regenerate note content.")
        rewrite_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=REWRITE_HELP["workdir"])
        rewrite_parser.add_argument("--source-note", type=Path, default=Path("output.md"), help=REWRITE_HELP["source_note"])
        rewrite_parser.add_argument("--title", help=REWRITE_HELP["title"])
        rewrite_parser.add_argument("--url", help=REWRITE_HELP["url"])
        rewrite_parser.add_argument("--llm-backend", default="ollama", help=REWRITE_HELP["llm_backend"])
        rewrite_parser.add_argument("--model", default="qwen2.5:7b", help=REWRITE_HELP["model"])
        rewrite_parser.add_argument("--note-style", default="summary", help=REWRITE_HELP["note_style"])
        rewrite_parser.add_argument("--with-course-summary", action="store_true", help=REWRITE_HELP["with_course_summary"])
        rewrite_parser.add_argument("--with-key-terms", action="store_true", help=REWRITE_HELP["with_key_terms"])
        rewrite_parser.add_argument("--include-extra-notes", action="store_true", help=REWRITE_HELP["include_extra_notes"])
        rewrite_parser.add_argument("--include-transcript", action="store_true", help=REWRITE_HELP["include_transcript"])
        rewrite_parser.add_argument("--output", type=Path, default=Path("output.md"), help=REWRITE_HELP["output"])
        publish_parser = subparsers.add_parser("publish-hackmd", help="Publish a rendered Markdown note to HackMD.")
        publish_parser.add_argument("note_path", type=Path, help=PUBLISH_HELP["note_path"])
        publish_parser.add_argument("--title", help=PUBLISH_HELP["title"])
        publish_parser.add_argument("--image-dir", type=Path, default=Path("workdir/frames"), help=PUBLISH_HELP["image_dir"])
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
                scene_edge_weight=args.scene_edge_weight,
                llm_backend=args.llm_backend,
                model=args.model,
                note_style=args.note_style,
                with_course_summary=args.with_course_summary,
                with_key_terms=args.with_key_terms,
                include_extra_notes=args.include_extra_notes,
                include_transcript=args.include_transcript,
                output=args.output,
                workdir=args.workdir,
            )
        if args.command == "rewrite-notes":
            rewrite_notes_impl(
                workdir=args.workdir,
                source_note=args.source_note,
                title=args.title,
                url=args.url,
                llm_backend=args.llm_backend,
                model=args.model,
                note_style=args.note_style,
                with_course_summary=args.with_course_summary,
                with_key_terms=args.with_key_terms,
                include_extra_notes=args.include_extra_notes,
                include_transcript=args.include_transcript,
                output=args.output,
            )
        if args.command == "publish-hackmd":
            publish_hackmd_impl(
                note_path=args.note_path,
                title=args.title,
                image_dir=args.image_dir,
            )


def run() -> None:
    load_env_file()
    if HAS_TYPER:
        app()
    else:
        main()


if __name__ == "__main__":
    run()
