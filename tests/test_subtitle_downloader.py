from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from course_note.subtitle.downloader import YouTubeSubtitleDownloader


@pytest.mark.parametrize("preferred_language,detected_language", [("zh-TW", "en"), ("en", "zh")])
def test_transcription_detects_original_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    preferred_language: str, detected_language: str,
) -> None:
    calls = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio_path, **kwargs):
            calls.append(kwargs)
            assert '準備音訊與偵測語言' in (tmp_path / 'pipeline.log').read_text(encoding='utf-8')

            def segments():
                log = (tmp_path / 'pipeline.log').read_text(encoding='utf-8')
                assert '[DONE] 準備音訊與偵測語言' in log
                assert '[START] 語音轉錄' in log
                yield SimpleNamespace(start=0.0, end=2.0, text="Original speech")

            return segments(), SimpleNamespace(language=detected_language)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace())
    downloader = YouTubeSubtitleDownloader(transcribe_fallback=True, whisper_device="cpu")
    monkeypatch.setattr(downloader, "_extract_info", lambda url: {"title": "Course"})

    def run(command):
        if "--skip-download" not in command:
            Path(command[command.index("-o") + 1]).write_bytes(b"audio")

    monkeypatch.setattr(downloader, "_run", run)
    result = downloader.download("https://example.com/video", tmp_path, preferred_language)

    assert calls == [{"language": None, "task": "transcribe", "vad_filter": True, "log_progress": True}]
    assert result.language == detected_language
    assert result.source == "transcribed"
    assert "Original speech" in result.subtitle_path.read_text(encoding="utf-8")


def test_missing_subtitle_track_does_not_reuse_stale_vtt(monkeypatch, tmp_path):
    old_vtt = tmp_path / "subtitles.vtt"
    old_vtt.write_text("old incorrect transcription", encoding="utf-8")
    (tmp_path / "subtitles.fr.vtt").write_text("unrelated track", encoding="utf-8")
    downloader = YouTubeSubtitleDownloader()
    monkeypatch.setattr(downloader, "_run", lambda command: None)

    assert downloader._try_download_subtitles("url", tmp_path, "en", auto=False) is None
    assert old_vtt.read_text(encoding="utf-8") == "old incorrect transcription"


@pytest.mark.parametrize("auto", [False, True])
def test_fresh_subtitle_track_replaces_stale_vtt(monkeypatch, tmp_path, auto):
    (tmp_path / "subtitles.vtt").write_text("old", encoding="utf-8")
    downloader = YouTubeSubtitleDownloader()

    def run(command):
        target = Path(command[command.index("-o") + 1])
        target.with_suffix(".en.vtt").write_text("fresh", encoding="utf-8")

    monkeypatch.setattr(downloader, "_run", run)
    result = downloader._try_download_subtitles("url", tmp_path, "en", auto=auto)
    assert result == tmp_path / "subtitles.vtt"
    assert result.read_text(encoding="utf-8") == "fresh"
