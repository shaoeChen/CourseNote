from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from course_note.utils.progress import progress_stage
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
        with progress_stage("讀取影片資訊", workdir / "pipeline.log"):
            info = self._extract_info(url)
        title = info.get("title") or "Untitled Course"

        native_vtt = self._try_download_subtitles(url, workdir, language, auto=False)
        if native_vtt is not None:
            return SubtitleDownloadResult(title, native_vtt, workdir / "transcript.json", "native", language)

        auto_vtt = self._try_download_subtitles(url, workdir, language, auto=True)
        if auto_vtt is not None:
            return SubtitleDownloadResult(title, auto_vtt, workdir / "transcript.json", "auto", language)

        if self.transcribe_fallback:
            return self._transcribe_and_write_vtt(url, workdir, title)

        raise RuntimeError(f"No subtitles available for language '{language}'.")

    def _try_download_subtitles(
        self,
        url: str,
        workdir: Path,
        language: str,
        *,
        auto: bool,
    ) -> Path | None:
        # Only inspect files from this attempt; an existing workspace VTT may
        # belong to another language or a legacy, incorrectly forced transcript.
        with TemporaryDirectory(prefix="subtitle-download-", dir=workdir) as temporary_dir:
            subtitle_target = Path(temporary_dir) / "subtitles"
            command = [
                "yt-dlp",
                "--write-auto-sub" if auto else "--write-sub",
                "--sub-langs", language,
                "--skip-download",
                "--sub-format", "vtt",
                "-o", str(subtitle_target),
                url,
            ]
            kind = "自動字幕" if auto else "人工字幕"
            with progress_stage(f"下載{kind}，語言={language}", workdir / "pipeline.log"):
                self._run(command)
            vtt_files = sorted(
                Path(temporary_dir).glob("subtitles*.vtt"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
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
        with progress_stage("下載音訊（無符合語言的字幕，改用語音轉錄）", workdir / "pipeline.log"):
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

        with progress_stage(f"載入語音模型 {self.whisper_model}，device={device}（首次使用可能下載模型）", workdir / "pipeline.log"):
            model = WhisperModel(self.whisper_model, device=device, compute_type=compute_type)
        with progress_stage("準備音訊與偵測語言（尚未開始逐段轉錄）", workdir / "pipeline.log"):
            segments, info = model.transcribe(
                str(audio_path), language=None, task="transcribe", vad_filter=True,
                log_progress=True,
            )

        print(f"偵測到音訊語言：{info.language}；開始逐段轉錄。", flush=True)
        with progress_stage("語音轉錄", workdir / "pipeline.log", interval=None):
            vtt_path = workdir / "subtitles.vtt"
            vtt_path.write_text(self._segments_to_vtt(segments), encoding="utf-8")
        audio_path.unlink(missing_ok=True)

        return SubtitleDownloadResult(title, vtt_path, workdir / "transcript.json", "transcribed", info.language)

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
        run_command([*command, "--newline", "--progress"], stream=True)
