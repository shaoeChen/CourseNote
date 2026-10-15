from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from course_note.subtitle.downloader import YouTubeSubtitleDownloader
from course_note.utils.subprocess_utils import run_command
from course_note.video.downloader import VideoDownloader
from course_note.video.frames import FrameSampler


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
def test_command_failure_displays_captured_diagnostics(stream: str) -> None:
    diagnostic = "HTTP Error 403: Forbidden"
    character_codes = [ord(char) for char in diagnostic]
    command = [
        sys.executable,
        "-c",
        f"import sys; print(''.join(map(chr, {character_codes})), file=sys.{stream}); sys.exit(1)",
    ]

    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_command(command)

    assert "HTTP Error 403: Forbidden" in str(caught.value)
    assert caught.value.returncode == 1
    assert caught.value.cmd == command
    assert "HTTP Error 403: Forbidden" in getattr(caught.value, stream)


def test_command_failure_without_output_preserves_exit_status() -> None:
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_command([sys.executable, "-c", "import sys; sys.exit(2)"])

    assert "exit status 2" in str(caught.value)


def test_subtitle_downloader_run_uses_utf8_text_mode(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    YouTubeSubtitleDownloader._run(["yt-dlp", "--version"])

    assert recorded["command"] == ["yt-dlp", "--version"]
    assert recorded["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }


def test_video_downloader_uses_utf8_text_mode(monkeypatch, tmp_path: Path) -> None:
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    VideoDownloader().download("https://example.com/video", tmp_path)

    assert recorded["command"] == [
        "yt-dlp",
        "-f",
        "mp4/bestvideo+bestaudio/best",
        "-o",
        str(tmp_path / "video.mp4"),
        "https://example.com/video",
    ]
    assert recorded["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }


def test_frame_sampler_uses_utf8_text_mode(monkeypatch, tmp_path: Path) -> None:
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        Path(command[-1]).write_bytes(b"frame")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    FrameSampler()._capture_frame(tmp_path / "video.mp4", tmp_path / "frame.jpg", 12.345)

    assert recorded["command"] == [
        "ffmpeg",
        "-y",
        "-ss",
        "12.345",
        "-i",
        str(tmp_path / "video.mp4"),
        "-frames:v",
        "1",
        str(tmp_path / "frame.jpg"),
    ]
    assert recorded["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
