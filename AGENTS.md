# AGENTS.md - AI Assistant Guide for CourseNote

## Project Overview

**CourseNote** is a local Python CLI tool that converts YouTube course videos into structured Markdown notes. It downloads subtitles and video, detects scenes, samples representative slide frames, and asks an LLM to rewrite each segment into readable study notes.

**Language**: The README and rendered notes are written in Traditional Chinese (`zh-TW`). Code comments and this guide may remain in English.

---

## Repository Structure

```text
CourseNote/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── course_note/
│   ├── __init__.py
│   ├── cli/
│   │   └── main.py
│   ├── hackmd/
│   ├── llm/
│   │   └── service.py
│   ├── models/
│   │   └── core.py
│   ├── prompts/
│   │   └── templates.py
│   ├── providers/
│   │   └── openai_compatible.py
│   ├── renderers/
│   │   └── markdown.py
│   ├── segmentation/
│   │   ├── scenes.py
│   │   └── timeline.py
│   ├── subtitle/
│   │   ├── downloader.py
│   │   └── parser.py
│   ├── utils/
│   │   ├── env.py
│   │   ├── io.py
│   │   └── timefmt.py
│   └── video/
│       ├── downloader.py
│       └── frames.py
└── tests/
```

---

## Pipeline Architecture

### `generate`

The `generate` command in `course_note/cli/main.py` performs the full pipeline:

1. Download subtitles with `subtitle/downloader.py`
2. Parse WebVTT subtitles with `subtitle/parser.py`
3. Download the video with `video/downloader.py`
4. Detect scenes with `segmentation/scenes.py`
5. Capture representative slide frames with `video/frames.py`
6. Build time-aligned segments with `segmentation/timeline.py`
7. Summarize each segment with `llm/service.py`
8. Optionally generate course summary and key terms
9. Render Markdown with `renderers/markdown.py`

Important detail: representative slide frames are aligned to the **end** of each segment, not the start. `sample_interval` means "how many seconds before segment end to capture the frame".

### `rewrite-notes`

The `rewrite-notes` command skips subtitle/video/scene work and only rewrites note content:

1. Load existing `workdir/segments.json`
2. Keep the existing segment boundaries and screenshot names
3. Re-run the LLM only on each segment's `transcript`
4. Rebuild the Markdown output

Use this when timings and screenshots already look correct and only note wording or note style needs to change.

---

## Data Models

All main models live in `course_note/models/core.py` and use `@dataclass(slots=True)`.

| Model | Key Fields |
|---|---|
| `TranscriptSegment` | `start_sec`, `end_sec`, `text`, `lang`, `source` |
| `SlideSegment` | `start_sec`, `end_sec`, `screenshot`, `transcript`, `summary`, `extra_notes` |
| `CourseNote` | `title`, `url`, `segments`, `course_summary`, `key_terms` |

---

## CLI Notes

### Full generation

```bash
course-note generate "https://youtube.com/..." \
  --subtitle-lang zh-TW \
  --sample-interval 1 \
  --scene-threshold 27.0 \
  --min-segment-seconds 20 \
  --llm-backend ollama \
  --model qwen2.5:7b \
  --note-style summary \
  --with-course-summary \
  --with-key-terms \
  --output output.md \
  --workdir ./workdir
```

### Content-only rewrite

```bash
course-note rewrite-notes \
  --workdir ./workdir \
  --source-note output.md \
  --llm-backend ollama \
  --model qwen2.5:7b \
  --note-style summary \
  --output output.md
```

If no prior Markdown file is available, pass `--title` and `--url` explicitly.

### HackMD publish

```bash
course-note publish-hackmd output.md
```

---

## Workdir Artifacts

`generate` preserves intermediate artifacts inside `workdir/`:

```text
workdir/
  video.mp4
  subtitles.vtt
  transcript.json
  frames/
  scenes.json
  segments.json
  llm_outputs.json
```

`rewrite-notes` depends on `segments.json` and rewrites `llm_outputs.json`.

---

## Development

### Setup

```bash
pip install -e .
```

External tools required:

- `yt-dlp`
- `ffmpeg`

### Tests

```bash
pytest
```

### Python Version

Requires Python 3.10 or newer.

---

## Code Conventions

- Use comprehensive type hints on public function signatures
- Keep data in dataclasses, logic in services
- Prefer specific exceptions over silent failure
- Keep imports ordered: `__future__`, stdlib, then local modules
- Use `snake_case` for functions, `PascalCase` for classes, `UPPER_CASE` for constants

---

## Key Extension Points

- Add a new LLM backend in `providers/openai_compatible.py`
- Add a new prompt style in `prompts/templates.py`
- Add a new output renderer under `renderers/`
- Extend content-only editing behavior in `cli/main.py` if more rewrite modes are needed later

---

## Branch & Commit Conventions

- Development branches: `claude/<description>-<id>`
- Commit messages should be imperative and descriptive
- No CI/CD is currently configured
