from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from course_note.utils.subprocess_utils import run_command


@dataclass(slots=True)
class SubtitleDownloadResult:
    title: str
    subtitle_path: Path
    transcript_json_path: Path
    source: str
    language: str


class YouTubeSubtitleDownloader:
    def __init__(
        self,
        *,
        transcribe_fallback: bool = False,
        whisper_model: str = "small",
        whisper_device: str = "auto",
    ) -> None:
        self.transcribe_fallback = transcribe_fallback
        self.whisper_model = whisper_model
        self.whisper_device = whisper_device

    def download(
        self,
        url: str,
        workdir: Path,
        language: str,
    ) -> SubtitleDownloadResult:
        info = self._extract_info(url)
        title = info.get("title") or "Untitled Course"

        native_vtt = self._try_download_subtitles(url, workdir, language, auto=False)
        if native_vtt is not None:
            return SubtitleDownloadResult(title, native_vtt, workdir / "transcript.json", "native", language)

        auto_vtt = self._try_download_subtitles(url, workdir, language, auto=True)
        if auto_vtt is not None:
            return SubtitleDownloadResult(title, auto_vtt, workdir / "transcript.json", "auto", language)

        if self.transcribe_fallback:
            return self._transcribe_and_write_vtt(url, workdir, title, language)

        raise RuntimeError(f"No subtitles available for language '{language}'.")

    def _try_download_subtitles(
        self,
        url: str,
        workdir: Path,
        language: str,
        *,
        auto: bool,
    ) -> Path | None:
        subtitle_target = workdir / "subtitles"
        command = [
            "yt-dlp",
            "--skip-download",
            "--sub-format",
            "vtt",
            "-o",
            str(subtitle_target),
            url,
        ]
        if auto:
            command[1:1] = ["--write-auto-sub", "--sub-langs", language]
        else:
            command[1:1] = ["--write-sub", "--sub-langs", language]
        self._run(command)
        vtt_candidates = [
            subtitle_target.with_suffix(f".{language}.vtt"),
            subtitle_target.with_suffix(f".{language}-{language}.vtt"),
            subtitle_target.with_suffix(".vtt"),
        ]
        for candidate in vtt_candidates:
            if candidate.exists():
                final_path = workdir / "subtitles.vtt"
                candidate.replace(final_path)
                return final_path
        # 若 yt-dlp 寫出其他檔名（例如多語言 auto 字幕），掃描目錄找最新 vtt
        vtt_files = sorted(workdir.glob("subtitles*.vtt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if vtt_files:
            final_path = workdir / "subtitles.vtt"
            vtt_files[0].replace(final_path)
            return final_path
        return None

    def _transcribe_and_write_vtt(
        self,
        url: str,
        workdir: Path,
        title: str,
        language: str,
    ) -> SubtitleDownloadResult:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "No subtitles found and transcription fallback requires faster-whisper. "
                "Install it with: uv pip install faster-whisper"
            ) from exc
        try:
            import ctranslate2  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "ctranslate2 (faster-whisper backend) failed to load — usually a numpy "
                "binary mismatch from PYTHONPATH pointing at another Python's "
                "site-packages (e.g. the Hermes venv). Unset it first: "
                "bash: `unset PYTHONPATH`; PowerShell: `$env:PYTHONPATH = ''`"
            ) from exc

        audio_path = workdir / "audio.m4a"
        self._run(
            [
                "yt-dlp",
                "-f",
                "bestaudio[ext=m4a]/bestaudio/best",
                "-o",
                str(audio_path),
                url,
            ]
        )
        if not audio_path.exists():
            # 某些來源只有 webm/opus，yt-dlp 自動挑了別種副檔名
            audio_files = sorted(
                workdir.glob("audio.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not audio_files:
                raise RuntimeError("Audio download failed; cannot transcribe without audio.")
            audio_path = audio_files[0]

        whisper_lang = language.split("-")[0]
        device, compute_type = self._resolve_device()

        import os

        # Windows 上 ctranslate2 需要 CUDA 函式庫在 PATH 上
        nvidia_base = os.path.expanduser(
            "~/AppData/Local/hermes/hermes-agent/venv/Lib/site-packages/nvidia"
        )
        for d in ["cublas/bin", "cudnn/bin", "cuda_nvrtc/bin"]:
            p = os.path.join(nvidia_base, d)
            if os.path.isdir(p):
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")

        model = WhisperModel(self.whisper_model, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(str(audio_path), language=whisper_lang, vad_filter=True)

        vtt_path = workdir / "subtitles.vtt"
        vtt_path.write_text(self._segments_to_vtt(segments), encoding="utf-8")
        audio_path.unlink(missing_ok=True)

        return SubtitleDownloadResult(title, vtt_path, workdir / "transcript.json", "transcribed", language)

    def _resolve_device(self) -> tuple[str, str]:
        if self.whisper_device != "auto":
            compute = "float16" if self.whisper_device == "cuda" else "int8"
            return self.whisper_device, compute
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception:
            pass
        return "cpu", "int8"

    @staticmethod
    def _segments_to_vtt(segments) -> str:
        lines = ["WEBVTT", ""]
        for segment in segments:
            start = YouTubeSubtitleDownloader._format_timestamp(segment.start)
            end = YouTubeSubtitleDownloader._format_timestamp(segment.end)
            text = segment.text.strip().replace("\n", " ")
            if not text:
                continue
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, remainder = divmod(millis, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, ms = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"

    def _extract_info(self, url: str) -> dict:
        output = run_command(["yt-dlp", "--dump-single-json", "--skip-download", url])
        return json.loads(output.stdout)

    @staticmethod
    def _run(command: list[str]) -> None:
        run_command(command)
