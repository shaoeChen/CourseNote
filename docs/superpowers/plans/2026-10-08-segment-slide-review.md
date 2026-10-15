# Segment-based slide review implementation plan

User approved: use the algorithm timeline as the basis; review transcript, topic and screenshots per segment. No regular vision scan or whole-video frame-by-frame vision calls. Reuse the configured provider settings.

## Architecture
Keep algorithm scene parameters unchanged. For each scene, send its timestamped original subtitle cues, optional existing note summaries with original time ranges, brief neighboring transcripts, three representative frames, and the previous reviewed representative frame when available. Review with one request. Cache on prompt, context, images, model and endpoint. Review results distinguish camera cuts, multiple slides, slide-ahead screenshots, content mismatch, topic continuation and uncertainty.

Only explicit high-confidence same-page camera changes merge. Same topic on different slides stays separate. A selected screenshot must be a readable frame within its resulting scene. High-confidence split proposals require a second, local-only request with five scene frames; validate all proposed bounds/captures. No unknown or low-confidence decision changes the boundaries. Per run at most one base request per scene and at most ten additional local reviews. Exhausted local budget leaves review flags rather than silently pretending a split is verified.

Preview writes reports and proposed scenes/frames only. It reads cached video/subtitles (or transcript.json), never downloads or overwrites article.md/segments.json/original frames. Generate applies the reviewed high-confidence proposed timeline before regenerating notes. Existing summaries are review evidence, not authoritative truth; timestamp overlap maps summaries to a scene.

## Tasks
- [x] Add test-first SegmentSlideReviewer in segmentation/segment_review.py; use the existing decoded-time frame reader and atomic report writing. Test one call per segment, timestamped subtitles, note mismatch, actual-page versus topic merges, screenshot-ahead, local split validation/budget, cache context invalidation, fail-safe API errors, selected frame timestamps, and preserved coverage.
- [x] Replace CLI hybrid dispatch with this reviewer. Forward transcripts/min-segment-seconds, load existing notes read-only, remove periodic scan controls from CLI, and add min-segment-seconds to detect-slides. Prefer cached scenes.json for detect-slides; generate recomputes algorithm timeline using the user's knobs. Show base/local/cache counts and human-readable review reasons in preview.md.
- [x] Add integration tests proving existing algorithm boundaries and minimum duration are retained, preview requires subtitle evidence, prior notes are untouched, shared provider config is used, no scan-interval option remains, and the new HTTP payload supports three or more images.
- [x] Update README/.env example/design spec. Run full pytest, Typer/argparse help/forwarding and independent review; state actual-model validation limits.

## Verification constraints
No paid model calls or model downloads during implementation. Synthetic HTTP/image tests validate transport and timeline behavior separately from real model judgment quality. Existing provider simplification work remains part of the final change.

Validation: 160 tests passed in the full suite. Typer and argparse forwarding verified; real JPEG/local HTTP/ffmpeg tests pass without paid calls. Independent review found budget-exhaustion merge and out-of-context base evidence issues; both have regression tests and conservative fixes. Real provider judgment quality on CS329A has not been evaluated.
