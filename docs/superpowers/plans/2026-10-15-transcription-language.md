# Transcription language correction

**Goal:** Keep subtitle track preference separate from audio language; preserve original-language transcription.

**Design:** Use faster-whisper automatic language detection (`language=None`, `task="transcribe"`) and save the detected language. Record `subtitle_requested_language` separately in workspace metadata. Legacy transcribed caches lack this field and must be regenerated; corrected caches compare the requested preference and return the actual language. Download subtitle tracks into an isolated temporary directory so stale workspace VTT files cannot masquerade as fresh downloads. Keep native/auto subtitle cache compatibility.

**Validation:** Mock only external downloads and Whisper inference. Exercise the real downloader, VTT writing, parser, metadata round trip and CLI cache paths. Do not transcribe or rewrite existing user artifacts as part of code validation.

- [x] Add failing tests in `tests/test_subtitle_downloader.py` for automatic language detection, actual language reporting and stale/unrelated VTT isolation.
- [x] Add failing tests in `tests/test_generate.py` for invalidating legacy transcription caches and persisting/reusing detected language with a different requested track language in both CLI flows.
- [x] Fix `course_note/subtitle/downloader.py`, `course_note/utils/io.py`, and `course_note/cli/main.py` to satisfy those tests.
- [x] Document fallback parameters, detected language and legacy-cache regeneration in `README.md`.
- [x] Run focused tests, the full pytest suite, CLI help and `git diff --check`; review the final diff.

Results: regression run failed as expected (9 failures, 3 passes); implementation passed all 12 focused tests and all 196 suite tests. CLI help exits successfully. Independent review found no blocking issues. Existing workdir artifacts were not changed.
