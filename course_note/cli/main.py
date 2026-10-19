from __future__ import annotations

import importlib.util
import json
import math
import re
from pathlib import Path
from typing import Any

from course_note.cli.config import (
    GENERATE_DEFAULTS,
    QUEUE_DEFAULTS,
    load_config,
    resolve_options,
)
from course_note.cli.queue import (
    QueueItem,
    format_queue_summary,
    parse_queue_file,
    queue_lock,
    run_queue,
)
from course_note.hackmd import HackMDPublisher
from course_note.llm.service import LLMService
from course_note.models import CourseNote, SlideSegment, TranscriptSegment
from course_note.providers.openai_compatible import OpenAICompatibleClient
from course_note.renderers.markdown import MarkdownRenderer
from course_note.segmentation.scenes import SceneDetectorService
from course_note.segmentation.segment_review import SegmentSlideReviewer
from course_note.segmentation.timeline import SegmentBuilder
from course_note.subtitle.downloader import SubtitleDownloadResult, YouTubeSubtitleDownloader
from course_note.subtitle.parser import SubtitleParser
from course_note.utils.env import load_env_file
from course_note.utils.io import (
    CourseWorkspaceMetadata,
    find_course_workspace_by_url,
    load_course_workspace_metadata,
    resolve_course_workdir,
    sanitize_course_name,
    write_course_workspace_metadata,
    write_json,
)
from course_note.utils.timefmt import scene_frame_filename
from course_note.video.downloader import VideoDownloader
from course_note.video.info import fetch_video_title
from course_note.video.frames import FrameSampler

HAS_TYPER = importlib.util.find_spec("typer") is not None
if HAS_TYPER:
    import typer
else:
    typer = None


ARTICLE_FILENAME = "article.md"

GENERATE_HELP = {
    "url": "Course video URL.",
    "course_name": "Course identifier; the same name reuses the same per-course workspace under --workdir. Defaults to the existing workspace bound to this URL, otherwise the video title.",
    "subtitle_lang": "Preferred subtitle track language, for example zh-TW or en. Audio transcription detects its language automatically; note output remains Traditional Chinese.",
    "sample_interval": "How many seconds before each segment end to capture the representative frame. A value of 1 means capture near the segment end, not near the segment start.",
    "scene_threshold": "PySceneDetect content threshold. Lower values split scenes more aggressively, higher values require larger visual changes.",
    "min_segment_seconds": "Minimum allowed scene duration in seconds. Scene cuts closer than this are merged to avoid overly short segments.",
    "scene_edge_weight": "Extra weight for edge changes during scene detection; useful when slides change layout but colors stay similar.",
    "scene_mode": "algorithm (default) detects visual cuts; hybrid reviews algorithm segments with the vision model; reviewed applies a completed slide_analysis/slide_review.json without scene detection or vision requests.",
    "vision_backend": "Optional provider override for slide recognition; defaults to COURSE_NOTE_VISION_PROVIDER, then COURSE_NOTE_PROVIDER. Reuses the provider's existing model and connection settings.",
    "vision_timeout_seconds": "Optional vision request timeout override; defaults to COURSE_NOTE_LLM_TIMEOUT_SECONDS or 120.",
    "llm_backend": "LLM backend name, such as ollama, vllm, llama.cpp, zai, deepseek, or clinepass. If omitted, use COURSE_NOTE_PROVIDER or the built-in default.",
    "llm_timeout_seconds": "LLM request timeout in seconds. Increase this for slower local models such as larger Ollama models.",
    "note_style": "Prompt template name, such as summary, study_notes, structured, or near_transcript.",
    "with_course_summary": "Generate a whole-course summary section after segment notes.",
    "with_key_terms": "Extract key terms for the whole course.",
    "include_extra_notes": "Allow the LLM to emit an extra notes section when needed.",
    "include_transcript": "Include the original transcript text in each segment of the rendered Markdown.",
    "transcribe_fallback": "When no native or auto subtitles match --subtitle-lang, download audio and transcribe it in its automatically detected original language with faster-whisper.",
    "whisper_model": "faster-whisper model name used by --transcribe-fallback, for example small, medium, or large-v3.",
    "whisper_device": "faster-whisper device: auto (CUDA if available), cuda, or cpu.",
    "workdir": "Base working directory that stores per-course subfolders and intermediate artifacts.",
    "config": "Settings file whose generate table supplies defaults for options not passed on the command line. Defaults to ./course-note.toml when it exists.",
}

QUEUE_HELP = {
    "queue_file": "Text file with one URL per line, optionally followed by whitespace and a course name; blank lines and # comments are ignored.",
    "config": "Settings file whose generate table applies to every video and whose queue table sets queue options. Defaults to ./course-note.toml when it exists.",
    "delay_seconds": "Pause between videos that were actually processed, to avoid hammering YouTube. Defaults to delay_seconds in the queue table, or 30.",
    "stop_on_error": "Stop at the first failed video instead of continuing with the rest of the queue.",
}

REWRITE_HELP = {
    "workdir": "Base working directory, or a direct path to a course workspace containing segments.json and llm_outputs.json.",
    "course_name": "Required course identifier used to resolve the per-course workspace under --workdir.",
    "source_note": "Existing Markdown note used to recover title and URL when title/url are not passed explicitly. Defaults to the course article.md.",
    "title": "Override note title in the regenerated Markdown.",
    "url": "Override source video URL in the regenerated Markdown.",
    "llm_backend": GENERATE_HELP["llm_backend"],
    "llm_timeout_seconds": GENERATE_HELP["llm_timeout_seconds"],
    "note_style": GENERATE_HELP["note_style"],
    "with_course_summary": GENERATE_HELP["with_course_summary"],
    "with_key_terms": GENERATE_HELP["with_key_terms"],
    "include_extra_notes": GENERATE_HELP["include_extra_notes"],
    "include_transcript": GENERATE_HELP["include_transcript"],
}

DOWNLOAD_SUBTITLES_HELP = {
    "url": GENERATE_HELP["url"],
    "course_name": GENERATE_HELP["course_name"],
    "subtitle_lang": GENERATE_HELP["subtitle_lang"],
    "workdir": GENERATE_HELP["workdir"],
}

PUBLISH_HELP = {
    "course_name": "Required course identifier used to resolve the per-course frames directory under --workdir.",
    "title": "HackMD note title. Defaults to the first Markdown heading.",
    "workdir": "Base working directory that stores per-course subfolders.",
    "image_dir": "Optional override for the directory containing local slide images referenced by the Markdown.",
}


def generate_impl(
    url: str,
    course_name: str | None = None,
    subtitle_lang: str = "zh-TW",
    sample_interval: float = 1.0,
    scene_threshold: float = 27.0,
    min_segment_seconds: int = 20,
    scene_edge_weight: float = 0.0,
    llm_backend: str | None = None,
    llm_timeout_seconds: int | None = None,
    note_style: str = "summary",
    with_course_summary: bool = False,
    with_key_terms: bool = False,
    include_extra_notes: bool = False,
    include_transcript: bool = False,
    transcribe_fallback: bool = False,
    whisper_model: str = "small",
    whisper_device: str = "auto",
    workdir: Path = Path("workdir"),
    scene_mode: str = "algorithm",
    vision_backend: str | None = None,
    vision_timeout_seconds: int | None = None,
) -> None:
    _validate_slide_options(scene_mode, vision_backend, vision_timeout_seconds)
    course_name = resolve_course_name(url, course_name, workdir)
    workdir = resolve_course_workdir(workdir, course_name)
    metadata = load_course_workspace_metadata(workdir)
    _validate_course_workspace(metadata, course_name=course_name, url=url, workdir=workdir)

    reviewed_scenes = _load_reviewed_scenes(workdir) if scene_mode == 'reviewed' else None

    subtitle_result = _load_or_download_subtitles(
        url=url,
        workdir=workdir,
        subtitle_lang=subtitle_lang,
        course_name=course_name,
        metadata=metadata,
        transcribe_fallback=transcribe_fallback,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
    )

    transcripts = SubtitleParser().parse_vtt(
        subtitle_result.subtitle_path,
        lang=subtitle_result.language,
        source=subtitle_result.source,
    )
    write_json(subtitle_result.transcript_json_path, [segment.to_dict() for segment in transcripts])

    video_path = _load_or_download_video(url, workdir)
    if scene_mode == 'reviewed':
        assert reviewed_scenes is not None
        scenes = reviewed_scenes
        print(f'Using {len(scenes)} completed reviewed segments; vision requests=0. Text note generation will call the configured LLM.', flush=True)
    elif scene_mode == "hybrid":
        scenes = _detect_hybrid_slides(
            video_path, workdir, scene_threshold=scene_threshold, scene_edge_weight=scene_edge_weight,
            vision_backend=vision_backend, vision_timeout_seconds=vision_timeout_seconds,
            min_segment_seconds=min_segment_seconds, transcripts=transcripts,
        )
    else:
        scenes = SceneDetectorService().detect(
            video_path, scene_threshold, min_segment_seconds, edge_weight=scene_edge_weight,
            stats_csv_path=workdir / "scene_stats.csv",
        )
    write_json(workdir / "scenes.json", scenes)
    print(f'Sampling {len(scenes)} representative frames for article generation.', flush=True)
    FrameSampler().sample_for_scenes(video_path, workdir / "frames", scenes, sample_interval)

    print('Building subtitle-aligned article segments.', flush=True)
    segments = SegmentBuilder().build(scenes, transcripts, workdir / "frames")
    write_json(workdir / "segments.json", [segment.to_dict() for segment in segments])

    llm_service = LLMService()
    llm_service.log_path = workdir / 'text_generation.log'
    summarize_kwargs: dict[str, Any] = {}
    if llm_timeout_seconds is not None:
        summarize_kwargs["timeout_seconds"] = llm_timeout_seconds
    llm_outputs = llm_service.summarize_segments(
        segments,
        backend=llm_backend,
        note_style=note_style,
        include_extra_notes=include_extra_notes,
        **summarize_kwargs,
    )
    write_json(workdir / "llm_outputs.json", llm_outputs)

    course_summary = (
        llm_service.summarize_course(segments, llm_backend, **summarize_kwargs)
        if with_course_summary
        else None
    )
    key_terms = (
        llm_service.extract_key_terms(segments, llm_backend, **summarize_kwargs)
        if with_key_terms
        else None
    )

    note = CourseNote(
        title=subtitle_result.title,
        url=url,
        segments=segments,
        course_summary=course_summary,
        key_terms=key_terms,
    )
    write_course_workspace_metadata(
        workdir,
        CourseWorkspaceMetadata(
            course_name=course_name,
            source_url=url,
            title=note.title,
            subtitle_language=subtitle_result.language,
            subtitle_source=subtitle_result.source,
            subtitle_requested_language=subtitle_lang,
        ),
    )

    output = _course_article_path(workdir)
    output.write_text(MarkdownRenderer().render(note, include_transcript=include_transcript), encoding="utf-8")
    message = f"Markdown note generated at {output}"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def resolve_course_name(url: str, course_name: str | None, workdir: Path) -> str:
    """Use the explicit name, else the workspace already bound to url, else the video title."""
    if course_name:
        return course_name
    existing = find_course_workspace_by_url(workdir, url)
    if existing is not None:
        print(f"未指定 --course-name，沿用此網址既有的課程工作區：{existing.course_name}", flush=True)
        return existing.course_name
    title = fetch_video_title(url)
    print(f"未指定 --course-name，使用影片標題：{title}", flush=True)
    return title


def generate_from_cli(
    url: str, course_name: str | None, config: Path | None, **cli_values: Any,
) -> None:
    """Merge CLI flags (None = not passed) over course-note.toml and built-in defaults."""
    settings = load_config(config)
    options = resolve_options(cli_values, settings.get("generate", {}), GENERATE_DEFAULTS)
    generate_impl(url=url, course_name=course_name, **options)


def queue_impl(
    queue_file: Path = Path("queue.txt"),
    config: Path | None = None,
    delay_seconds: float | None = None,
    stop_on_error: bool | None = None,
) -> None:
    """Run generate for each queued URL, strictly one video at a time."""
    settings = load_config(config)
    options = resolve_options({}, settings.get("generate", {}), GENERATE_DEFAULTS)
    queue_options = resolve_options(
        {"delay_seconds": delay_seconds, "stop_on_error": stop_on_error},
        settings.get("queue", {}),
        QUEUE_DEFAULTS,
    )
    if queue_options["delay_seconds"] < 0:
        raise ValueError("--delay-seconds must not be negative.")
    items = parse_queue_file(queue_file)
    if not items:
        print(f"{queue_file} 沒有待處理的網址。", flush=True)
        return
    base_workdir: Path = options["workdir"]

    def process(item: QueueItem) -> bool:
        done = _completed_course_name(base_workdir, item)
        if done is not None:
            print(f"[QUEUE] 已有 article.md，略過：{done}", flush=True)
            return False
        generate_impl(url=item.url, course_name=item.course_name, **options)
        return True

    print(f"[QUEUE] 共 {len(items)} 筆，依序逐部處理（不平行）。", flush=True)
    with queue_lock(base_workdir):
        result = run_queue(
            items,
            process=process,
            delay_seconds=queue_options["delay_seconds"],
            stop_on_error=queue_options["stop_on_error"],
        )
    print(format_queue_summary(result, len(items)), flush=True)
    if result.failed:
        raise SystemExit(1)


def _completed_course_name(base_workdir: Path, item: QueueItem) -> str | None:
    """Return the course name when this queue item already has a generated article."""
    if item.course_name:
        course_dir = base_workdir / sanitize_course_name(item.course_name)
        metadata = load_course_workspace_metadata(course_dir) if course_dir.is_dir() else None
        if metadata is None or metadata.source_url != item.url:
            return None
    else:
        metadata = find_course_workspace_by_url(base_workdir, item.url)
        if metadata is None:
            return None
        course_dir = base_workdir / sanitize_course_name(metadata.course_name)
    return metadata.course_name if (course_dir / ARTICLE_FILENAME).is_file() else None


def _validate_slide_options(
    scene_mode: str, vision_backend: str | None, timeout_seconds: int | None,
) -> None:
    if scene_mode not in {"algorithm", "hybrid", "reviewed"}:
        raise ValueError("--scene-mode must be algorithm, hybrid or reviewed.")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("--vision-timeout-seconds must be greater than zero.")
    if scene_mode == "hybrid":
        OpenAICompatibleClient().resolve_vision_config(vision_backend, timeout_seconds)


def _load_reviewed_scenes(workdir: Path) -> list[dict[str, float]]:
    path = workdir / 'slide_analysis' / 'slide_review.json'
    if not path.is_file():
        raise FileNotFoundError(f'--scene-mode reviewed requires a completed detect-slides report: {path}')
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report, dict) or report.get('status') != 'complete':
        raise ValueError('Cannot apply an incomplete slide review; finish detect-slides first.')

    def timeline(raw: Any) -> list[dict[str, float]]:
        if not isinstance(raw, list) or not raw:
            raise ValueError('Reviewed timelines must be nonempty lists.')
        result = []
        previous_end = None
        for scene in raw:
            if not isinstance(scene, dict):
                raise ValueError('Invalid reviewed scene.')
            start, end = scene.get('start_sec'), scene.get('end_sec')
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                    or (previous_end is not None and start != previous_end)):
                raise ValueError('Reviewed bounds must be finite, ordered and contiguous.')
            selected = {'start_sec': float(start), 'end_sec': float(end)}
            if 'capture_sec' in scene:
                capture = scene['capture_sec']
                if type(capture) not in (int, float) or not math.isfinite(capture) or not start <= capture < end:
                    raise ValueError('Reviewed capture must be inside its segment.')
                selected['capture_sec'] = float(capture)
            result.append(selected)
            previous_end = end
        return result

    source = timeline(report.get('source_scenes'))
    scenes = timeline(report.get('proposed_scenes'))
    reviews = report.get('reviews')
    if (not isinstance(reviews, list) or len(reviews) != len(source)
            or any(not isinstance(review, dict) or type(review.get('segment_index')) is not int
                   or review['segment_index'] != index for index, review in enumerate(reviews))):
        raise ValueError('Completed report must include a review for every original segment.')
    if scenes[0]['start_sec'] != source[0]['start_sec'] or scenes[-1]['end_sec'] != source[-1]['end_sec']:
        raise ValueError('Reviewed proposals must cover the entire original timeline.')
    return scenes


def _detect_hybrid_slides(
    video_path: Path, workdir: Path, *, scene_threshold: float, scene_edge_weight: float,
    min_segment_seconds: int, transcripts: list[TranscriptSegment],
    vision_backend: str | None, vision_timeout_seconds: int | None, reuse_scenes: bool = False,
) -> list[dict[str, float]]:
    analysis_dir = workdir / "slide_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    source = workdir / "scenes.json"
    if reuse_scenes and source.is_file():
        print(f"Reusing cached timeline: {source}", flush=True)
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("Cached scenes.json must contain a nonempty timeline.")
        candidates = [{"start_sec": scene["start_sec"], "end_sec": scene["end_sec"]} for scene in raw]
    else:
        print(f"Detecting algorithm scenes: threshold={scene_threshold}, edge_weight={scene_edge_weight}, min_segment_seconds={min_segment_seconds}", flush=True)
        candidates = SceneDetectorService().detect(
            video_path, scene_threshold, min_segment_seconds, edge_weight=scene_edge_weight,
            stats_csv_path=analysis_dir / "scene_stats.csv",
        )
    write_json(analysis_dir / "candidate_scenes.json", candidates)
    notes_path = workdir / "segments.json"
    notes = _load_segments(notes_path) if notes_path.is_file() else None
    print(f"Reviewing {len(candidates)} segments; at most one base request per segment and 10 extra requests.", flush=True)
    scenes = SegmentSlideReviewer().review(
        video_path, candidates, transcripts, analysis_dir, backend=vision_backend,
        timeout_seconds=vision_timeout_seconds, existing_notes=notes,
    )
    write_json(analysis_dir / "scenes.json", scenes)
    print(f"Slide analysis: {len(scenes)} segments; review {analysis_dir / 'slide_review.json'}", flush=True)
    return scenes


def _load_review_transcripts(course_dir: Path) -> list[TranscriptSegment]:
    subtitle_path = course_dir / "subtitles.vtt"
    if subtitle_path.is_file():
        metadata = load_course_workspace_metadata(course_dir)
        return SubtitleParser().parse_vtt(
            subtitle_path, lang=(metadata.subtitle_language if metadata else None) or "unknown",
            source=(metadata.subtitle_source if metadata else None) or "cached",
        )
    transcript_path = course_dir / "transcript.json"
    if not transcript_path.is_file():
        raise FileNotFoundError("detect-slides requires cached subtitles.vtt or transcript.json for segment review.")
    raw = json.loads(transcript_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cached transcript.json must contain subtitle evidence.")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid cached transcript entry.")
        start, end, text = item.get("start_sec"), item.get("end_sec"), item.get("text")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                or not isinstance(text, str) or not text.strip()):
            raise ValueError("Invalid cached transcript timing or text.")
        result.append(TranscriptSegment(start, end, text, item.get("lang", "unknown"), item.get("source", "cached")))
    return result


def detect_slides_impl(
    course_name: str, workdir: Path = Path("workdir"), vision_backend: str | None = None,
    vision_timeout_seconds: int | None = None, scene_threshold: float = 15.0,
    scene_edge_weight: float = 0.3, min_segment_seconds: int = 20,
) -> None:
    """Review cached segments and preview changes without replacing course notes."""
    _validate_slide_options("hybrid", vision_backend, vision_timeout_seconds)
    course_dir = workdir / sanitize_course_name(course_name)
    video_path = course_dir / "video.mp4"
    if not video_path.is_file():
        raise FileNotFoundError(f"detect-slides requires an existing course video: {video_path}")
    print(f"Loading cached subtitles/transcript: {course_dir}", flush=True)
    transcripts = _load_review_transcripts(course_dir)
    print(f"Loaded {len(transcripts)} subtitle cues.", flush=True)
    try:
        scenes = _detect_hybrid_slides(
            video_path, course_dir, scene_threshold=scene_threshold, scene_edge_weight=scene_edge_weight,
            min_segment_seconds=min_segment_seconds, transcripts=transcripts, reuse_scenes=True,
            vision_backend=vision_backend, vision_timeout_seconds=vision_timeout_seconds,
        )
    except RuntimeError:
        report_path = course_dir / 'slide_analysis' / 'slide_review.json'
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
                if report.get('status') == 'failed' and report.get('proposed_scenes'):
                    _write_slide_preview(course_dir, report['proposed_scenes'], partial=True)
            except Exception as preview_error:
                print(f'Partial preview could not be written: {preview_error}', flush=True)
        raise
    _write_slide_preview(course_dir, scenes)


def _write_slide_preview(course_dir: Path, scenes: list[dict[str, float]], *, partial: bool = False) -> Path:
    """Render saved proposals locally; this function makes no model requests."""
    video_path = course_dir / 'video.mp4'
    preview_dir = course_dir / ("slide_preview/partial" if partial else "slide_preview")
    print(f"Writing preview and {len(scenes)} representative frames: {preview_dir}", flush=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    FrameSampler().sample_for_scenes(video_path, preview_dir / "frames", scenes, 1.0)
    write_json(preview_dir / "scenes.json", scenes)
    report_link = '../../slide_analysis/slide_review.json' if partial else '../slide_analysis/slide_review.json'
    lines = ["# 簡報按段審查部分預覽" if partial else "# 簡報按段審查預覽", "",
             f"[完整判斷與證據]({report_link})", ""]
    if partial:
        lines.extend([f"審查未完成。此預覽僅包含已保存的修正提案，涵蓋 {scenes[0]['start_sec']:.3f}–{scenes[-1]['end_sec']:.3f} 秒；後續區段尚未完成審查。", ""])
    report_path = course_dir / "slide_analysis" / "slide_review.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if partial:
            lines.extend([f"停止原因：{report.get('error', report.get('status', 'unknown'))}", ""])
        stats = report.get("statistics", {})
        lines.extend([f"模型請求：{stats.get('model_requests', 0)}；快取命中：{stats.get('cache_hits', 0)}；追加分析：{stats.get('local_requests', 0)}/10", "", "## 原始區段審查", ""])
        for review in report.get("reviews", []):
            lines.extend([f"- 區段 {review['segment_index'] + 1}: {review['status']}（信心 {review['confidence']:.2f}）— {review['reason']}"])
            if review.get("actions"):
                lines.append(f"  - 處理結果：{', '.join(review['actions'])}")
            for evidence in review.get("evidence", []):
                lines.append(f"  - {evidence['start_sec']:.3f}–{evidence['end_sec']:.3f} 秒：{evidence['detail']}")
            local_review = review.get("local_review")
            if local_review:
                lines.append(f"  - 局部分析（信心 {local_review['confidence']:.2f}）：{local_review['reason']}")
                for evidence in local_review.get("evidence", []):
                    lines.append(f"  - 局部證據 {evidence['start_sec']:.3f}–{evidence['end_sec']:.3f} 秒：{evidence['detail']}")
            if review.get("note_mismatch"):
                lines.append("  - 既有筆記可能與字幕或畫面不符，需人工確認。")
        if report.get("uncertain_ranges"):
            lines.extend(["", "## 待人工確認", "", *[f"- {item['start_sec']:.3f}–{item['end_sec']:.3f} 秒：{item['reason']}" for item in report['uncertain_ranges']]])
        lines.append("")
    for index, scene in enumerate(scenes, 1):
        lines.extend([
            f"## {index}: {scene['start_sec']:.3f}–{scene['end_sec']:.3f} 秒", "",
            f"擷取時間：{scene['capture_sec']:.3f} 秒" if "capture_sec" in scene else "尚未確認可讀且符合字幕的簡報；暫用區段末端畫面。",
            "", f"![簡報 {index}](frames/{scene_frame_filename(scene)})", "",
        ])
    output = preview_dir / ('partial_preview.md' if partial else 'preview.md')
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Slide preview written at {output}", flush=True)
    return output


def rewrite_notes_impl(
    workdir: Path = Path("workdir"),
    course_name: str | None = None,
    source_note: Path | None = None,
    title: str | None = None,
    url: str | None = None,
    llm_backend: str | None = None,
    llm_timeout_seconds: int | None = None,
    note_style: str = "summary",
    with_course_summary: bool = False,
    with_key_terms: bool = False,
    include_extra_notes: bool = False,
    include_transcript: bool = False,
) -> None:
    if course_name:
        workdir = resolve_course_workdir(workdir, course_name)
    output = _course_article_path(workdir)
    source_note = output if source_note is None else source_note
    segments = _load_segments(workdir / "segments.json")
    resolved_title, resolved_url = _resolve_note_metadata(source_note, title=title, url=url)

    llm_service = LLMService()
    llm_service.log_path = workdir / 'text_generation.log'
    summarize_kwargs: dict[str, Any] = {}
    if llm_timeout_seconds is not None:
        summarize_kwargs["timeout_seconds"] = llm_timeout_seconds
    llm_outputs = llm_service.summarize_segments(
        segments,
        backend=llm_backend,
        note_style=note_style,
        include_extra_notes=include_extra_notes,
        **summarize_kwargs,
    )
    write_json(workdir / "llm_outputs.json", llm_outputs)

    course_summary = (
        llm_service.summarize_course(segments, llm_backend, **summarize_kwargs)
        if with_course_summary
        else None
    )
    key_terms = (
        llm_service.extract_key_terms(segments, llm_backend, **summarize_kwargs)
        if with_key_terms
        else None
    )

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


def download_subtitles_impl(
    url: str,
    course_name: str,
    subtitle_lang: str = "zh-TW",
    transcribe_fallback: bool = False,
    whisper_model: str = "small",
    whisper_device: str = "auto",
    workdir: Path = Path("workdir"),
) -> None:
    workdir = resolve_course_workdir(workdir, course_name)
    metadata = load_course_workspace_metadata(workdir)
    _validate_course_workspace(metadata, course_name=course_name, url=url, workdir=workdir)

    subtitle_result = _load_or_download_subtitles(
        url=url,
        workdir=workdir,
        subtitle_lang=subtitle_lang,
        course_name=course_name,
        metadata=metadata,
        transcribe_fallback=transcribe_fallback,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
    )
    transcripts = SubtitleParser().parse_vtt(
        subtitle_result.subtitle_path,
        lang=subtitle_result.language,
        source=subtitle_result.source,
    )
    write_json(subtitle_result.transcript_json_path, [segment.to_dict() for segment in transcripts])
    write_course_workspace_metadata(
        workdir,
        CourseWorkspaceMetadata(
            course_name=course_name,
            source_url=url,
            title=subtitle_result.title,
            subtitle_language=subtitle_result.language,
            subtitle_source=subtitle_result.source,
            subtitle_requested_language=subtitle_lang,
        ),
    )

    message = (
        f"Subtitles downloaded at {subtitle_result.subtitle_path}; "
        f"transcript written at {subtitle_result.transcript_json_path}"
    )
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def publish_hackmd_impl(
    course_name: str,
    title: str | None = None,
    workdir: Path = Path("workdir"),
    image_dir: Path | None = None,
) -> None:
    load_env_file()
    course_workdir = resolve_course_workdir(workdir, course_name)
    note_path = _course_article_path(course_workdir)
    resolved_image_dir = image_dir or (course_workdir / "frames")
    result = HackMDPublisher().publish_markdown(note_path, title=title, image_dir=resolved_image_dir)
    link = result.publish_link or result.permalink or f"note_id={result.note_id}"
    message = f"HackMD note created: {link} (uploaded {result.image_count} image(s))"
    if HAS_TYPER:
        typer.echo(message)
    else:
        print(message)


def _course_article_path(course_workdir: Path) -> Path:
    course_workdir.mkdir(parents=True, exist_ok=True)
    return course_workdir / ARTICLE_FILENAME


def _load_or_download_subtitles(
    *,
    url: str,
    workdir: Path,
    subtitle_lang: str,
    course_name: str,
    metadata: CourseWorkspaceMetadata | None,
    transcribe_fallback: bool = False,
    whisper_model: str = "small",
    whisper_device: str = "auto",
) -> SubtitleDownloadResult:
    subtitle_path = workdir / "subtitles.vtt"
    if subtitle_path.exists() and _can_reuse_subtitles(metadata, subtitle_lang):
        return _build_cached_subtitle_result(workdir, subtitle_lang, course_name, metadata)
    subtitle_downloader = YouTubeSubtitleDownloader(
        transcribe_fallback=transcribe_fallback,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
    )
    return subtitle_downloader.download(url, workdir, subtitle_lang)


def _build_cached_subtitle_result(
    workdir: Path,
    subtitle_lang: str,
    course_name: str,
    metadata: CourseWorkspaceMetadata | None,
) -> SubtitleDownloadResult:
    title = metadata.title if metadata else course_name
    source = metadata.subtitle_source if metadata and metadata.subtitle_source else "cached"
    return SubtitleDownloadResult(
        title=title,
        subtitle_path=workdir / "subtitles.vtt",
        transcript_json_path=workdir / "transcript.json",
        source=source,
        language=(metadata.subtitle_language if metadata else None) or subtitle_lang,
    )


def _load_or_download_video(url: str, workdir: Path) -> Path:
    video_path = workdir / "video.mp4"
    if video_path.exists():
        return video_path
    return VideoDownloader().download(url, workdir)


def _can_reuse_subtitles(metadata: CourseWorkspaceMetadata | None, subtitle_lang: str) -> bool:
    if metadata is not None and metadata.subtitle_source == "transcribed":
        # Legacy transcriptions forced the preferred subtitle language onto the
        # audio. Only new caches record the preference separately from detection.
        return metadata.subtitle_requested_language == subtitle_lang
    if metadata is None or metadata.subtitle_language is None:
        return True
    return metadata.subtitle_language == subtitle_lang


def _validate_course_workspace(
    metadata: CourseWorkspaceMetadata | None,
    *,
    course_name: str,
    url: str,
    workdir: Path,
) -> None:
    if metadata is None:
        return
    if metadata.course_name != course_name:
        raise ValueError(
            f"Course workspace {workdir} already belongs to '{metadata.course_name}'. "
            f"Please choose a different --course-name."
        )
    if metadata.source_url != url:
        raise ValueError(
            f"Course workspace {workdir} is already bound to {metadata.source_url}. "
            f"Please use a different --course-name for {url}."
        )


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
        course_name: str | None = typer.Option(None, help=GENERATE_HELP["course_name"]),
        config: Path | None = typer.Option(None, help=GENERATE_HELP["config"]),
        subtitle_lang: str | None = typer.Option(None, help=GENERATE_HELP["subtitle_lang"]),
        sample_interval: float | None = typer.Option(None, help=GENERATE_HELP["sample_interval"]),
        scene_threshold: float | None = typer.Option(None, help=GENERATE_HELP["scene_threshold"]),
        min_segment_seconds: int | None = typer.Option(None, help=GENERATE_HELP["min_segment_seconds"]),
        scene_edge_weight: float | None = typer.Option(None, help=GENERATE_HELP["scene_edge_weight"]),
        scene_mode: str | None = typer.Option(None, help=GENERATE_HELP["scene_mode"]),
        vision_backend: str | None = typer.Option(None, help=GENERATE_HELP["vision_backend"]),
        vision_timeout_seconds: int | None = typer.Option(None, help=GENERATE_HELP["vision_timeout_seconds"]),
        llm_backend: str | None = typer.Option(None, help=GENERATE_HELP["llm_backend"]),
        llm_timeout_seconds: int | None = typer.Option(None, help=GENERATE_HELP["llm_timeout_seconds"]),
        note_style: str | None = typer.Option(None, help=GENERATE_HELP["note_style"]),
        with_course_summary: bool | None = typer.Option(None, help=GENERATE_HELP["with_course_summary"]),
        with_key_terms: bool | None = typer.Option(None, help=GENERATE_HELP["with_key_terms"]),
        include_extra_notes: bool | None = typer.Option(None, help=GENERATE_HELP["include_extra_notes"]),
        include_transcript: bool | None = typer.Option(None, help=GENERATE_HELP["include_transcript"]),
        transcribe_fallback: bool | None = typer.Option(None, help=GENERATE_HELP["transcribe_fallback"]),
        whisper_model: str | None = typer.Option(None, help=GENERATE_HELP["whisper_model"]),
        whisper_device: str | None = typer.Option(None, help=GENERATE_HELP["whisper_device"]),
        workdir: Path | None = typer.Option(None, help=GENERATE_HELP["workdir"]),
    ) -> None:
        """Run the full pipeline. Unpassed options come from course-note.toml, then built-in defaults."""
        generate_from_cli(
            url=url,
            course_name=course_name,
            config=config,
            subtitle_lang=subtitle_lang,
            sample_interval=sample_interval,
            scene_threshold=scene_threshold,
            min_segment_seconds=min_segment_seconds,
            scene_edge_weight=scene_edge_weight,
            scene_mode=scene_mode,
            vision_backend=vision_backend,
            vision_timeout_seconds=vision_timeout_seconds,
            llm_backend=llm_backend,
            llm_timeout_seconds=llm_timeout_seconds,
            note_style=note_style,
            with_course_summary=with_course_summary,
            with_key_terms=with_key_terms,
            include_extra_notes=include_extra_notes,
            include_transcript=include_transcript,
            transcribe_fallback=transcribe_fallback,
            whisper_model=whisper_model,
            whisper_device=whisper_device,
            workdir=workdir,
        )

    @app.command("queue")
    def queue(
        queue_file: Path = typer.Argument(Path("queue.txt"), help=QUEUE_HELP["queue_file"]),
        config: Path | None = typer.Option(None, help=QUEUE_HELP["config"]),
        delay_seconds: float | None = typer.Option(None, help=QUEUE_HELP["delay_seconds"]),
        stop_on_error: bool | None = typer.Option(None, help=QUEUE_HELP["stop_on_error"]),
    ) -> None:
        """Generate notes for every URL in a queue file, one video at a time."""
        queue_impl(queue_file=queue_file, config=config, delay_seconds=delay_seconds, stop_on_error=stop_on_error)

    @app.command("detect-slides")
    def detect_slides(
        course_name: str = typer.Option(..., help=GENERATE_HELP["course_name"]),
        workdir: Path = typer.Option(Path("workdir"), help=GENERATE_HELP["workdir"]),
        vision_backend: str | None = typer.Option(None, help=GENERATE_HELP["vision_backend"]),
        vision_timeout_seconds: int | None = typer.Option(None, help=GENERATE_HELP["vision_timeout_seconds"]),
        min_segment_seconds: int = typer.Option(20, help=GENERATE_HELP["min_segment_seconds"]),
        scene_threshold: float = typer.Option(15.0, help=GENERATE_HELP["scene_threshold"]),
        scene_edge_weight: float = typer.Option(0.3, help=GENERATE_HELP["scene_edge_weight"]),
    ) -> None:
        """Preview slide pages in a cached video, without rewriting notes."""
        detect_slides_impl(
            course_name=course_name, workdir=workdir, vision_backend=vision_backend,
            vision_timeout_seconds=vision_timeout_seconds, min_segment_seconds=min_segment_seconds,
            scene_threshold=scene_threshold, scene_edge_weight=scene_edge_weight,
        )

    @app.command("rewrite-notes")
    def rewrite_notes(
        workdir: Path = typer.Option(Path("workdir"), help=REWRITE_HELP["workdir"]),
        course_name: str = typer.Option(..., help=REWRITE_HELP["course_name"]),
        source_note: Path | None = typer.Option(None, help=REWRITE_HELP["source_note"]),
        title: str | None = typer.Option(None, help=REWRITE_HELP["title"]),
        url: str | None = typer.Option(None, help=REWRITE_HELP["url"]),
        llm_backend: str | None = typer.Option(None, help=REWRITE_HELP["llm_backend"]),
        llm_timeout_seconds: int | None = typer.Option(None, help=REWRITE_HELP["llm_timeout_seconds"]),
        note_style: str = typer.Option("summary", help=REWRITE_HELP["note_style"]),
        with_course_summary: bool = typer.Option(False, help=REWRITE_HELP["with_course_summary"]),
        with_key_terms: bool = typer.Option(False, help=REWRITE_HELP["with_key_terms"]),
        include_extra_notes: bool = typer.Option(False, help=REWRITE_HELP["include_extra_notes"]),
        include_transcript: bool = typer.Option(False, help=REWRITE_HELP["include_transcript"]),
    ) -> None:
        rewrite_notes_impl(
            workdir=workdir,
            course_name=course_name,
            source_note=source_note,
            title=title,
            url=url,
            llm_backend=llm_backend,
            llm_timeout_seconds=llm_timeout_seconds,
            note_style=note_style,
            with_course_summary=with_course_summary,
            with_key_terms=with_key_terms,
            include_extra_notes=include_extra_notes,
            include_transcript=include_transcript,
        )

    @app.command("download-subtitles")
    def download_subtitles(
        url: str = typer.Argument(..., help=DOWNLOAD_SUBTITLES_HELP["url"]),
        course_name: str = typer.Option(..., help=DOWNLOAD_SUBTITLES_HELP["course_name"]),
        subtitle_lang: str = typer.Option("zh-TW", help=DOWNLOAD_SUBTITLES_HELP["subtitle_lang"]),
        transcribe_fallback: bool = typer.Option(False, help=GENERATE_HELP["transcribe_fallback"]),
        whisper_model: str = typer.Option("small", help=GENERATE_HELP["whisper_model"]),
        whisper_device: str = typer.Option("auto", help=GENERATE_HELP["whisper_device"]),
        workdir: Path = typer.Option(Path("workdir"), help=DOWNLOAD_SUBTITLES_HELP["workdir"]),
    ) -> None:
        download_subtitles_impl(
            url=url,
            course_name=course_name,
            subtitle_lang=subtitle_lang,
            transcribe_fallback=transcribe_fallback,
            whisper_model=whisper_model,
            whisper_device=whisper_device,
            workdir=workdir,
        )

    @app.command("publish-hackmd")
    def publish_hackmd(
        course_name: str = typer.Option(..., help=PUBLISH_HELP["course_name"]),
        title: str | None = typer.Option(None, help=PUBLISH_HELP["title"]),
        workdir: Path = typer.Option(Path("workdir"), help=PUBLISH_HELP["workdir"]),
        image_dir: Path | None = typer.Option(None, help=PUBLISH_HELP["image_dir"]),
    ) -> None:
        publish_hackmd_impl(course_name=course_name, title=title, workdir=workdir, image_dir=image_dir)
else:
    import argparse

    app = None

    def _build_argparse() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="course-note", description="Generate Markdown course notes from online course videos.")
        subparsers = parser.add_subparsers(dest="command", required=True)
        generate_parser = subparsers.add_parser("generate", help="Run the full video-to-notes pipeline.")
        generate_parser.add_argument("url", help=GENERATE_HELP["url"])
        generate_parser.add_argument("--course-name", help=GENERATE_HELP["course_name"])
        generate_parser.add_argument("--config", type=Path, help=GENERATE_HELP["config"])
        generate_parser.add_argument("--subtitle-lang", help=GENERATE_HELP["subtitle_lang"])
        generate_parser.add_argument("--sample-interval", type=float, help=GENERATE_HELP["sample_interval"])
        generate_parser.add_argument("--scene-threshold", type=float, help=GENERATE_HELP["scene_threshold"])
        generate_parser.add_argument("--min-segment-seconds", type=int, help=GENERATE_HELP["min_segment_seconds"])
        generate_parser.add_argument("--scene-edge-weight", type=float, help=GENERATE_HELP["scene_edge_weight"])
        generate_parser.add_argument("--scene-mode", choices=("algorithm", "hybrid", "reviewed"), help=GENERATE_HELP["scene_mode"])
        generate_parser.add_argument("--vision-backend", help=GENERATE_HELP["vision_backend"])
        generate_parser.add_argument("--vision-timeout-seconds", type=int, help=GENERATE_HELP["vision_timeout_seconds"])
        generate_parser.add_argument("--llm-backend", help=GENERATE_HELP["llm_backend"])
        generate_parser.add_argument("--llm-timeout-seconds", type=int, help=GENERATE_HELP["llm_timeout_seconds"])
        generate_parser.add_argument("--note-style", help=GENERATE_HELP["note_style"])
        generate_parser.add_argument("--with-course-summary", action=argparse.BooleanOptionalAction, help=GENERATE_HELP["with_course_summary"])
        generate_parser.add_argument("--with-key-terms", action=argparse.BooleanOptionalAction, help=GENERATE_HELP["with_key_terms"])
        generate_parser.add_argument("--include-extra-notes", action=argparse.BooleanOptionalAction, help=GENERATE_HELP["include_extra_notes"])
        generate_parser.add_argument("--include-transcript", action=argparse.BooleanOptionalAction, help=GENERATE_HELP["include_transcript"])
        generate_parser.add_argument("--transcribe-fallback", action=argparse.BooleanOptionalAction, help=GENERATE_HELP["transcribe_fallback"])
        generate_parser.add_argument("--whisper-model", help=GENERATE_HELP["whisper_model"])
        generate_parser.add_argument("--whisper-device", help=GENERATE_HELP["whisper_device"])
        generate_parser.add_argument("--workdir", type=Path, help=GENERATE_HELP["workdir"])
        queue_parser = subparsers.add_parser("queue", help="Generate notes for every URL in a queue file, one at a time.")
        queue_parser.add_argument("queue_file", nargs="?", type=Path, default=Path("queue.txt"), help=QUEUE_HELP["queue_file"])
        queue_parser.add_argument("--config", type=Path, help=QUEUE_HELP["config"])
        queue_parser.add_argument("--delay-seconds", type=float, help=QUEUE_HELP["delay_seconds"])
        queue_parser.add_argument("--stop-on-error", action=argparse.BooleanOptionalAction, help=QUEUE_HELP["stop_on_error"])
        slides_parser = subparsers.add_parser("detect-slides", help="Preview slide pages from a cached course video.")
        slides_parser.add_argument("--course-name", required=True, help=GENERATE_HELP["course_name"])
        slides_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=GENERATE_HELP["workdir"])
        slides_parser.add_argument("--vision-backend", help=GENERATE_HELP["vision_backend"])
        slides_parser.add_argument("--vision-timeout-seconds", type=int, help=GENERATE_HELP["vision_timeout_seconds"])
        slides_parser.add_argument("--min-segment-seconds", type=int, default=20, help=GENERATE_HELP["min_segment_seconds"])
        slides_parser.add_argument("--scene-threshold", type=float, default=15.0, help=GENERATE_HELP["scene_threshold"])
        slides_parser.add_argument("--scene-edge-weight", type=float, default=0.3, help=GENERATE_HELP["scene_edge_weight"])
        download_subtitles_parser = subparsers.add_parser("download-subtitles", help="Download and parse subtitles only.")
        download_subtitles_parser.add_argument("url", help=DOWNLOAD_SUBTITLES_HELP["url"])
        download_subtitles_parser.add_argument("--course-name", required=True, help=DOWNLOAD_SUBTITLES_HELP["course_name"])
        download_subtitles_parser.add_argument("--subtitle-lang", default="zh-TW", help=DOWNLOAD_SUBTITLES_HELP["subtitle_lang"])
        download_subtitles_parser.add_argument("--transcribe-fallback", action="store_true", help=GENERATE_HELP["transcribe_fallback"])
        download_subtitles_parser.add_argument("--whisper-model", default="small", help=GENERATE_HELP["whisper_model"])
        download_subtitles_parser.add_argument("--whisper-device", default="auto", help=GENERATE_HELP["whisper_device"])
        download_subtitles_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=DOWNLOAD_SUBTITLES_HELP["workdir"])
        rewrite_parser = subparsers.add_parser("rewrite-notes", help="Reuse existing segments and only regenerate note content.")
        rewrite_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=REWRITE_HELP["workdir"])
        rewrite_parser.add_argument("--course-name", required=True, help=REWRITE_HELP["course_name"])
        rewrite_parser.add_argument("--source-note", type=Path, help=REWRITE_HELP["source_note"])
        rewrite_parser.add_argument("--title", help=REWRITE_HELP["title"])
        rewrite_parser.add_argument("--url", help=REWRITE_HELP["url"])
        rewrite_parser.add_argument("--llm-backend", help=REWRITE_HELP["llm_backend"])
        rewrite_parser.add_argument("--llm-timeout-seconds", type=int, help=REWRITE_HELP["llm_timeout_seconds"])
        rewrite_parser.add_argument("--note-style", default="summary", help=REWRITE_HELP["note_style"])
        rewrite_parser.add_argument("--with-course-summary", action="store_true", help=REWRITE_HELP["with_course_summary"])
        rewrite_parser.add_argument("--with-key-terms", action="store_true", help=REWRITE_HELP["with_key_terms"])
        rewrite_parser.add_argument("--include-extra-notes", action="store_true", help=REWRITE_HELP["include_extra_notes"])
        rewrite_parser.add_argument("--include-transcript", action="store_true", help=REWRITE_HELP["include_transcript"])
        publish_parser = subparsers.add_parser("publish-hackmd", help="Publish a rendered Markdown note to HackMD.")
        publish_parser.add_argument("--course-name", required=True, help=PUBLISH_HELP["course_name"])
        publish_parser.add_argument("--title", help=PUBLISH_HELP["title"])
        publish_parser.add_argument("--workdir", type=Path, default=Path("workdir"), help=PUBLISH_HELP["workdir"])
        publish_parser.add_argument("--image-dir", type=Path, help=PUBLISH_HELP["image_dir"])
        return parser

    def main() -> None:
        parser = _build_argparse()
        args = parser.parse_args()
        if args.command == "generate":
            generate_from_cli(
                url=args.url,
                course_name=args.course_name,
                config=args.config,
                **{key: getattr(args, key) for key in GENERATE_DEFAULTS},
            )
        if args.command == "queue":
            queue_impl(
                queue_file=args.queue_file,
                config=args.config,
                delay_seconds=args.delay_seconds,
                stop_on_error=args.stop_on_error,
            )
        if args.command == "detect-slides":
            detect_slides_impl(
                course_name=args.course_name, workdir=args.workdir, vision_backend=args.vision_backend,
                vision_timeout_seconds=args.vision_timeout_seconds, min_segment_seconds=args.min_segment_seconds,
                scene_threshold=args.scene_threshold, scene_edge_weight=args.scene_edge_weight,
            )
        if args.command == "download-subtitles":
            download_subtitles_impl(
                url=args.url,
                course_name=args.course_name,
                subtitle_lang=args.subtitle_lang,
                transcribe_fallback=args.transcribe_fallback,
                whisper_model=args.whisper_model,
                whisper_device=args.whisper_device,
                workdir=args.workdir,
            )
        if args.command == "rewrite-notes":
            rewrite_notes_impl(
                workdir=args.workdir,
                course_name=args.course_name,
                source_note=args.source_note,
                title=args.title,
                url=args.url,
                llm_backend=args.llm_backend,
                llm_timeout_seconds=args.llm_timeout_seconds,
                note_style=args.note_style,
                with_course_summary=args.with_course_summary,
                with_key_terms=args.with_key_terms,
                include_extra_notes=args.include_extra_notes,
                include_transcript=args.include_transcript,
            )
        if args.command == "publish-hackmd":
            publish_hackmd_impl(
                course_name=args.course_name,
                title=args.title,
                workdir=args.workdir,
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
