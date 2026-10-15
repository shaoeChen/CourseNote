# Slide recognition implementation plan

> Historical implementation record. The periodic-scanning workflow below is superseded by [segment-based review](2026-10-08-segment-slide-review.md); the CLI now uses the approved bounded per-segment review.

**Goal:** Add an opt-in hybrid slide detector that preserves existing generation behavior.

**Architecture:** ContentDetector proposes cuts with a short candidate spacing. Periodic samples cover missed cuts. The model of the selected vision provider compares a last readable slide with subsequent frames, ignoring camera changes and progressive reveals. New pages trigger a bounded temporal refinement; uncertain comparisons go to a review report. A selected capture timestamp is carried through the existing frame and timeline pipeline.

**Tech stack:** Python dataclasses, existing OpenAI-compatible HTTP client, OpenCV JPEG extraction, pytest. No paid service or model download is required for tests.

## Task 1: Vision transport and configuration
- [x] Add failing tests for selecting an already configured provider, reusing its model and connection settings, ordered local JPEG image inputs, and unchanged text chat.
- [x] Implement `resolve_vision_config(backend=None, timeout_seconds=None)` and `chat_images(config, prompt, images)` in providers/openai_compatible.py. Select with COURSE_NOTE_VISION_PROVIDER, falling back to COURSE_NOTE_PROVIDER. Reuse resolve_config for model, connection, key and shared timeout; no separate vision model/connection variables.
- [x] Run `python -m pytest tests/test_openai_compatible.py tests/test_vision_client.py -q`.

## Task 2: Slide detector
- [x] Write failing tests using injected frame reader/judge: same slide across camera cuts, missed changes recovered by periodic scans, refinement to within 0.5 seconds, best readable frame, unknown/low confidence report, content-based cache invalidation.
- [x] Add segmentation/slides.py with `HybridSlideDetector.detect(video_path, candidate_scenes, workdir, *, backend=None, scan_interval=5.0, timeout_seconds=None) -> list[dict[str,float]]`. Return contiguous scenes covering the input duration and a verified `capture_sec` where available. Write slide_review.json, vision_cache.json and vision_frames/ incrementally. Only reliable new-page decisions split; unknown must not masquerade as same.
- [x] Validate configuration and JSON; API/invalid response errors stop before final note replacement. Cache includes model, endpoint, prompt version and image bytes. Frame extraction must be bounded and resource handles closed.
- [x] Run detector tests and verify synthetic CS329A camera/slide scenarios.

## Task 3: Pipeline and user controls
- [x] Add failing tests for Typer/argparse forwarding, early invalid-setting errors, selected timestamps, unique hybrid screenshot filenames, and default compatibility.
- [x] Implement `--scene-mode algorithm|hybrid`, `--vision-backend`, `--vision-scan-interval` and `--vision-timeout-seconds`. In hybrid mode detect candidates with at most one-second minimum, then refine before writing final scenes/frames. User min-segment-seconds continues to apply to algorithm mode only. FrameSampler honors validated capture_sec; timeline resolves exactly matching hybrid frame names.
- [x] Add `detect-slides --course-name ...` for cached video-only preview in slide_preview/ without altering article.md, segments.json or frames/. No text LLM call.
- [x] Document the vision provider selector and reused .env settings, command examples, preview/review workflow, scan cost, visibility limitations and artifact meanings.
- [x] Run full pytest, both CLI help/parser paths, diff checks, then independent spec/code review. Integrate the verified branch locally; no remote push.

## Acceptance
Default tests pass. Hybrid merges camera cuts, detects unseen candidate changes from periodic samples, selects readable frames, narrows known boundaries, caches requests, marks uncertainty, and can preview without regenerating notes. Real-model quality is reported separately from mocked/HTTP and synthetic-video verification.

## Verification (2026-10-08)

- Full suite: 123 passed in 23.83 seconds.
- Typer help and argparse generate/detect-slides forwarding verified.
- Synthetic 10 FPS / 29.97 FPS video exercised actual OpenCV JPEG extraction, HTTP image requests, temporal refinement, ffmpeg final screenshots, exact timeline filename resolution and fully cached reruns.
- Real CS329A video samples at requested 244.411 and 304.605 seconds produced verified final images using decoded timestamps; original course files unchanged.
- Independent review found low-FPS tail timestamp mismatch; fixed with persisted actual decoded timestamps, microsecond truncation and staged verified screenshots.
- Real vision model quality was not evaluated in the initial implementation; provider configuration simplification does not change that validation scope.
