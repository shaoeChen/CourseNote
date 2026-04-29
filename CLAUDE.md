# CLAUDE.md - AI Assistant Guide for CourseNote

## Project Overview

**CourseNote** is a local Python CLI tool that converts YouTube course videos into structured Markdown notes. It downloads video and subtitles, extracts frames, detects scene changes, and uses an LLM to summarize each segment into readable study notes.

**Language**: The project README and output content are in Traditional Chinese (zh-TW), though code comments and this file are in English.

---

## Repository Structure

```
CourseNote/
├── pyproject.toml              # Project config, dependencies, entry point
├── README.md                   # Project documentation (Traditional Chinese)
├── course_note/                # Main package
│   ├── __init__.py
│   ├── cli/
│   │   └── main.py             # CLI entry point & pipeline orchestration
│   ├── llm/
│   │   └── service.py          # LLM summarization orchestration
│   ├── models/
│   │   └── core.py             # Data models (TranscriptSegment, SlideSegment, CourseNote)
│   ├── prompts/
│   │   └── templates.py        # LLM prompt templates (summary, study_notes, structured, near_transcript)
│   ├── providers/
│   │   └── openai_compatible.py # OpenAI-compatible API client & backend resolver
│   ├── renderers/
│   │   └── markdown.py         # Markdown output renderer
│   ├── segmentation/
│   │   ├── scenes.py           # Scene detection via PySceneDetect
│   │   └── timeline.py         # Combines scenes + transcripts + frames into segments
│   ├── subtitle/
│   │   ├── downloader.py       # yt-dlp subtitle download (native → auto fallback)
│   │   └── parser.py           # WebVTT parser
│   ├── utils/
│   │   ├── io.py               # workdir/frames directory creation, JSON writing
│   │   └── timefmt.py          # Time formatting helpers (HH:MM:SS, HHMMSS.jpg)
│   └── video/
│       ├── downloader.py       # yt-dlp video download
│       └── frames.py           # ffmpeg frame extraction
└── tests/
    ├── test_subtitle_parser.py
    └── test_markdown_renderer.py
```

---

## Pipeline Architecture

The `generate` command in `cli/main.py` executes these steps in order:

1. **Download subtitles** (`subtitle/downloader.py`) — yt-dlp; tries native subtitles first, then auto-generated; fails if none available
2. **Parse subtitles** (`subtitle/parser.py`) — WebVTT → list of `TranscriptSegment`
3. **Download video** (`video/downloader.py`) — yt-dlp, mp4 format
4. **Sample frames** (`video/frames.py`) — ffmpeg at configurable interval; filenames use `HHMMSS.jpg` format
5. **Detect scenes** (`segmentation/scenes.py`) — PySceneDetect `ContentDetector`; falls back to one scene if none detected
6. **Build segments** (`segmentation/timeline.py`) — maps scenes to transcript text and nearest screenshot
7. **Summarize with LLM** (`llm/service.py`) — calls OpenAI-compatible API per segment
8. **Optional: course summary & key terms** (`llm/service.py`) — enabled via CLI flags
9. **Render Markdown** (`renderers/markdown.py`) — outputs structured `.md` file

Generated and rewritten notes are always stored at `workdir/<course-name>/article.md`.
The CLI no longer accepts `--output`, and HackMD publishing uses `--course-name`
instead of a positional Markdown filename.

**Intermediate files** are preserved in `workdir/`:
```
workdir/
  FastAPI 入門/
    course_metadata.json
    article.md
    video.mp4
    subtitles.vtt
    transcript.json
    frames/
    scenes.json
    segments.json
    llm_outputs.json
```

---

## Data Models (`models/core.py`)

All models use `@dataclass(slots=True)` for memory efficiency and provide `to_dict()` for JSON serialization.

| Model | Key Fields |
|---|---|
| `TranscriptSegment` | `start_sec`, `end_sec`, `text`, `lang`, `source` |
| `SlideSegment` | `start_sec`, `end_sec`, `screenshot`, `transcript`, `summary`, `extra_notes` |
| `CourseNote` | `title`, `url`, `segments`, `course_summary`, `key_terms` |

---

## LLM Integration

### Supported Backends (`providers/openai_compatible.py`)

All backends use the OpenAI chat completions API format:

| Backend | Default Endpoint |
|---|---|
| `ollama` | `http://localhost:11434/v1` |
| `vllm` | `http://localhost:8000/v1` |
| `llama.cpp` | `http://localhost:8080/v1` |
| `zai` | `https://open.bigmodel.cn/api/paas/v4` |

Override with environment variables:
- `COURSE_NOTE_API_BASE` — custom API base URL
- `COURSE_NOTE_API_KEY` — API key (defaults to `"ollama"` if unset)

### Prompt Templates (`prompts/templates.py`)

Available `--note-style` values:
- `summary` — concise course points in Chinese
- `study_notes` — bullet-point study notes
- `structured` — topics, key points, examples
- `near_transcript` — readable transcript-style

---

## CLI Usage

```bash
course-note generate "https://youtube.com/..." \
  --course-name "FastAPI 入門" \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 27.0 \
  --min-segment-seconds 20 \
  --llm-backend ollama \
  --model qwen2.5:7b \
  --note-style summary \
  --with-course-summary \
  --with-key-terms \
  --workdir ./workdir
```

PowerShell:

```powershell
course-note generate "https://youtube.com/..." `
  --course-name "FastAPI 入門" `
  --subtitle-lang zh-TW `
  --sample-interval 1 `
  --scene-threshold 27.0 `
  --min-segment-seconds 20 `
  --llm-backend ollama `
  --model qwen2.5:7b `
  --note-style summary `
  --with-course-summary `
  --with-key-terms `
  --workdir ./workdir
```

Content-only rewrite:

```bash
course-note rewrite-notes \
  --workdir ./workdir \
  --course-name "FastAPI 入門" \
  --llm-backend ollama \
  --model qwen2.5:7b \
  --note-style summary
```

PowerShell:

```powershell
course-note rewrite-notes `
  --workdir ./workdir `
  --course-name "FastAPI 入門" `
  --llm-backend ollama `
  --model qwen2.5:7b `
  --note-style summary
```

HackMD publish:

```bash
course-note publish-hackmd --course-name "FastAPI 入門"
```

PowerShell:

```powershell
course-note publish-hackmd --course-name "FastAPI 入門"
```

The CLI supports both `typer` (when installed) and a graceful fallback to `argparse`.

---

## Development

### Setup

```bash
pip install -e .
```

External tools required:
- `yt-dlp` — YouTube downloader (must be in PATH)
- `ffmpeg` — video/frame processing (must be in PATH)

### Running Tests

```bash
pytest
```

Tests live in `tests/` and are discovered automatically (configured in `pyproject.toml`).

### Python Version

Requires Python ≥ 3.10. All modules use `from __future__ import annotations` for PEP 563 compatibility.

---

## Code Conventions

### Style

- **Type hints**: comprehensive on all function signatures and return types
- **Dataclasses**: use `@dataclass(slots=True)` for data-holding classes
- **Custom exceptions**: domain-specific exceptions (e.g., `SubtitleError`) for expected failure modes
- **Imports**: `from __future__ import annotations` first, then stdlib, then local imports

### Naming

| Entity | Convention | Example |
|---|---|---|
| Classes | PascalCase | `YouTubeSubtitleDownloader` |
| Functions/methods | snake_case | `parse_vtt`, `resolve_config` |
| Constants | UPPERCASE | `TEMPLATES`, `EXTRA_NOTES_PROMPT` |
| Private methods | `_` prefix | `_parse_timestamp` |

### Architecture Patterns

- **Service classes**: each functional domain has one (downloader, detector, renderer, sampler)
- **Data models**: separate from logic; pure data containers with serialization
- **Provider pattern**: LLM backends abstracted behind `OpenAICompatibleClient`
- **Template pattern**: prompt templates stored as a dict in `prompts/templates.py` for easy addition

### Error Handling

- Raise specific exceptions at boundaries (file not found, subtitle unavailable, empty parse)
- Use defensive checks on paths (verify existence before processing)
- Don't silently swallow errors — propagate with context

---

## Key Extension Points

- **Add a new LLM backend**: extend `resolve_config()` in `providers/openai_compatible.py`
- **Add a new prompt style**: add an entry to `TEMPLATES` dict in `prompts/templates.py` and wire it in `llm/service.py`
- **Add a new output format**: create a new renderer in `renderers/` following `markdown.py`'s interface
- **Add subtitle source**: extend `subtitle/downloader.py` to support new platforms

---

## Branch & Commit Conventions

- Development branches follow the pattern: `claude/<description>-<id>`
- Commit messages should be descriptive and imperative (e.g., `Add scene detection fallback`)
- No CI/CD is currently configured
