from __future__ import annotations

import hashlib
import heapq
import json
import math
from pathlib import Path
from typing import Any, Callable

from course_note.providers.openai_compatible import OpenAICompatibleClient


PROMPT_VERSION = "slide-identity-v1"
SLIDE_PROMPT = """Compare image 1 (the last reliable readable slide) with image 2 (current frame).
Return ONLY a JSON object with exactly these fields:
{"slide_visible": boolean, "readable": boolean, "relation": "same"|"new"|"unknown",
 "confidence": number from 0 to 1, "quality": number from 0 to 1, "reason": string}.
slide_visible and readable describe image 2; quality describes how suitable image 2 is
as a legible study-note screenshot. A camera cut, presenter inset, zoom, or progressive
reveal/build on the same slide is SAME. NEW means a distinct slide/page, including a
return to an earlier page. An obscured, absent or unreadable slide is UNKNOWN; never
infer its identity from a presenter or a transition. If image 1 equals image 2 this
is a bootstrap visibility check: report SAME only if a slide is visible and readable.
Do not add Markdown fences or extra keys."""


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _validate_response(raw: Any) -> dict[str, Any]:
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid vision JSON response.") from exc
    keys = {"slide_visible", "readable", "relation", "confidence", "quality", "reason"}
    if not isinstance(result, dict) or set(result) != keys:
        raise RuntimeError("Invalid vision response schema: expected exactly the slide comparison fields.")
    if (type(result["slide_visible"]) is not bool or type(result["readable"]) is not bool
            or result["relation"] not in ("same", "new", "unknown")
            or not isinstance(result["reason"], str)):
        raise RuntimeError("Invalid vision response schema: boolean, relation or reason field.")
    for field in ("confidence", "quality"):
        value = result[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise RuntimeError(f"Invalid vision response schema: {field} must be between 0 and 1.")
    return result


def _reliable(result: dict[str, Any]) -> bool:
    return (result["slide_visible"] and result["readable"]
            and result["relation"] != "unknown" and result["confidence"] >= 0.8)


class _VideoFrameReader:
    """One bounded OpenCV capture per detection run; JPEG identity includes video stat."""

    def __init__(self) -> None:
        self.capture: Any = None
        self.cv2: Any = None
        self.capture_times: dict[float, float] = {}

    def __call__(self, video_path: Path, timestamp: float, directory: Path) -> Path:
        stat = video_path.stat()
        identity = f"{video_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{timestamp:.9f}:jpeg1280-pts-v2"
        path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".jpg")
        metadata_path = path.with_suffix(".json")
        if path.exists() and metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                actual = metadata["capture_sec"]
                if (type(actual) not in (int, float) or not math.isfinite(actual) or actual < 0
                        or metadata["jpeg_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()):
                    raise ValueError("invalid frame timestamp or image hash")
                self.capture_times[timestamp] = float(actual)
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f"Invalid cached hybrid frame metadata: {metadata_path}") from exc
            return path
        if self.capture is None:
            try:
                import cv2
            except ImportError as exc:
                raise RuntimeError("OpenCV is required for hybrid slide frame extraction.") from exc
            self.cv2 = cv2
            self.capture = cv2.VideoCapture(str(video_path))
            if not self.capture.isOpened():
                raise RuntimeError(f"Unable to open video for hybrid slide detection: {video_path}")
        cv2 = self.cv2
        fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        count = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        seek = timestamp
        if fps > 0 and count > 0:
            seek = min(seek, (count - 1) / fps)
        self.capture.set(cv2.CAP_PROP_POS_MSEC, seek * 1000)
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Unable to read hybrid slide frame at {timestamp:.3f}s.")
        actual = float(self.capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
        frame_index = float(self.capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        if (not math.isfinite(actual) or actual < 0
                or (actual == 0 and frame_index != 0 and timestamp > 0)):
            if math.isfinite(frame_index) and frame_index >= 0 and math.isfinite(fps) and fps > 0:
                actual = frame_index / fps
            else:
                raise RuntimeError(f"Unable to determine decoded hybrid frame timestamp at {timestamp:.3f}s.")
        height, width = frame.shape[:2]
        if max(height, width) > 1280:
            ratio = 1280 / max(height, width)
            frame = cv2.resize(frame, (max(1, round(width * ratio)), max(1, round(height * ratio))),
                               interpolation=cv2.INTER_AREA)
        encoded, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not encoded:
            raise RuntimeError(f"Unable to save hybrid slide frame: {path}")
        # Python filesystem I/O supports Chinese course directory names on Windows.
        jpeg_bytes = jpeg.tobytes()
        path.write_bytes(jpeg_bytes)
        _write_json(metadata_path, {"version": 2, "requested_sec": timestamp, "capture_sec": actual,
                                   "jpeg_sha256": hashlib.sha256(jpeg_bytes).hexdigest()})
        self.capture_times[timestamp] = actual
        return path

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None


class HybridSlideDetector:
    def __init__(
        self,
        client: Any = None,
        frame_reader: Callable[[Path, float, Path], Path] | None = None,
    ) -> None:
        self.client = client if client is not None else OpenAICompatibleClient()
        self.frame_reader = frame_reader

    def detect(
        self,
        video_path: Path,
        candidate_scenes: list[dict[str, float]],
        workdir: Path,
        *,
        backend: str | None = None,
        scan_interval: float = 5.0,
        timeout_seconds: int | None = None,
    ) -> list[dict[str, float]]:
        if not math.isfinite(scan_interval) or scan_interval <= 0:
            raise ValueError("scan_interval must be finite and greater than zero.")
        if not candidate_scenes:
            raise ValueError("Hybrid slide detection requires candidate scenes with a video duration.")
        duration = float(candidate_scenes[-1]["end_sec"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Candidate video duration must be finite and greater than zero.")
        times = {0.0, max(0.0, duration - min(0.05, duration / 2))}
        for scene in candidate_scenes:
            start, end = float(scene["start_sec"]), float(scene["end_sec"])
            if not all(math.isfinite(t) for t in (start, end)) or not 0 <= start < end <= duration:
                raise ValueError("Candidate scenes must have finite, ordered times within the video duration.")
            for offset in ((-0.5, 0.0, 0.5) if start > 0 else (0.0,)):
                times.add(min(max(0.0, start + offset), max(0.0, duration - min(0.05, duration / 2))))
        periodic = scan_interval
        while periodic < duration:
            times.add(periodic)
            periodic += scan_interval
        config = self.client.resolve_vision_config(backend=backend, timeout_seconds=timeout_seconds)
        workdir.mkdir(parents=True, exist_ok=True)
        frames_dir = workdir / "vision_frames"
        frames_dir.mkdir(exist_ok=True)
        cache_path, review_path = workdir / "vision_cache.json", workdir / "slide_review.json"
        cache: dict[str, Any] = {"version": 1, "entries": {}}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(cache, dict) or cache.get("version") != 1 or not isinstance(cache.get("entries"), dict):
                    raise ValueError("invalid cache structure")
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"Invalid vision cache JSON/schema: {cache_path}") from exc
        review: dict[str, Any] = {"version": 1, "prompt_version": PROMPT_VERSION,
            "model": config.model, "api_base": config.api_base, "status": "running",
            "duration_sec": duration, "scan_interval": scan_interval, "decisions": [],
            "uncertain_ranges": [], "boundaries": [], "scenes": []}
        reader = self.frame_reader if self.frame_reader is not None else _VideoFrameReader()
        frames: dict[float, Path] = {}
        frame_times: dict[float, float] = {}
        frame_requests: dict[float, float] = {}
        samples: dict[float, float] = {}
        boundaries: list[float] = [0.0]

        def frame(time: float) -> Path:
            if time not in frames:
                frames[time] = Path(reader(video_path, time, frames_dir))
                actual = float(getattr(reader, "capture_times", {}).get(time, time))
                if not math.isfinite(actual) or not 0 <= actual < duration:
                    raise RuntimeError(f"Decoded hybrid frame timestamp {actual} is outside the video duration.")
                frame_times[time] = actual
                # Alias the verified JPEG by its decoded time so refinement/reference
                # reuse does not seek again and accidentally sample a different frame.
                frames.setdefault(actual, frames[time])
                frame_times[actual] = actual
                frame_requests.setdefault(actual, time)
            return frames[time]

        def actual_time(time: float) -> float:
            frame(time)
            return frame_times[time]

        def judge(reference: float, current: float, purpose: str) -> dict[str, Any]:
            images = [frame(reference), frame(current)]
            reference, current = actual_time(reference), actual_time(current)
            identity = {"prompt_version": PROMPT_VERSION, "prompt": SLIDE_PROMPT,
                        "model": config.model, "api_base": config.api_base,
                        "images": [hashlib.sha256(path.read_bytes()).hexdigest() for path in images]}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            cached = key in cache["entries"]
            try:
                if cached:
                    result = _validate_response(cache["entries"][key])
                else:
                    result = _validate_response(self.client.chat_images(config, SLIDE_PROMPT, images))
                    cache["entries"][key] = result
                    _write_json(cache_path, cache)
            except Exception as exc:
                raise RuntimeError(f"Hybrid vision response failed at {current:.3f}s: {exc}") from exc
            review["decisions"].append({"reference_sec": reference, "current_sec": current,
                "reference_requested_sec": frame_requests[reference], "current_requested_sec": frame_requests[current],
                "purpose": purpose, "cached": cached, "images": [str(path) for path in images],
                "decision": result, "reliable": bool(_reliable(result))})
            if not _reliable(result):
                review["uncertain_ranges"].append({"start_sec": reference, "end_sec": current,
                    "reason": "unknown_or_low_confidence", "detail": result["reason"]})
            _write_json(review_path, review)
            print(f"Hybrid slides: {current:.2f}/{duration:.2f}s, {result['relation']}"
                  f" ({'cache' if cached else 'vision'}, confidence={result['confidence']:.2f})", flush=True)
            return result

        def refine(old: float, new: float, result: dict[str, Any]) -> tuple[float, dict[str, Any], list[float]]:
            low, high = old, new
            high_result = result
            probes: list[float] = []
            uncertain = False
            while high - low > 0.5:
                middle = actual_time((low + high) / 2)
                if not low < middle < high:
                    uncertain = True
                    break
                decision = judge(old, middle, "refinement")
                if not _reliable(decision):
                    uncertain = True
                    break
                if decision["relation"] == "same":
                    low = middle
                    samples[middle] = decision["quality"]
                else:
                    probes.append(high)
                    high, high_result = middle, decision
            review["boundaries"].append({"selected_sec": high, "start_sec": low,
                "end_sec": high, "exact": False, "refined": not uncertain, "tolerance_sec": 0.5,
                "method": "upper_observed_bound"})
            if uncertain:
                review["uncertain_ranges"].append({"start_sec": low, "end_sec": high,
                                                    "reason": "uncertain_boundary"})
            return high, high_result, probes

        queue = list(times)
        heapq.heapify(queue)
        reference: float | None = None
        last_unreadable = 0.0
        processed: set[float] = set()
        try:
            _write_json(review_path, review)
            while queue:
                time = heapq.heappop(queue)
                time = actual_time(time)
                if time in processed or (reference is not None and time <= reference):
                    continue
                decision = judge(reference if reference is not None else time, time,
                                 "bootstrap" if reference is None else "scan")
                if not _reliable(decision):
                    last_unreadable = time
                    processed.add(time)
                    continue
                if reference is None:
                    if time > 0:
                        boundaries.append(time)
                        review["boundaries"].append({"selected_sec": time, "start_sec": last_unreadable,
                            "end_sec": time, "exact": False, "refined": False, "method": "first_readable_slide"})
                        review["uncertain_ranges"].append({"start_sec": 0.0, "end_sec": time,
                                                           "reason": "intro_without_readable_slide"})
                    reference = time
                    samples[time] = decision["quality"]
                elif decision["relation"] == "same":
                    reference = time
                    samples[time] = decision["quality"]
                else:
                    boundary, first_new, probes = refine(reference, time, decision)
                    boundaries.append(boundary)
                    samples[boundary] = first_new["quality"]
                    reference = boundary
                    # Later NEW probes may be another short slide, not the same new page.
                    # Compare them again against the earliest new page rather than dropping them.
                    for probe in probes:
                        if probe > boundary:
                            heapq.heappush(queue, probe)
                    if time > boundary:
                        heapq.heappush(queue, time)
                        continue
                processed.add(time)
            scenes: list[dict[str, float]] = []
            for start, end in zip(boundaries, boundaries[1:] + [duration]):
                scene = {"start_sec": start, "end_sec": end}
                eligible = [(quality, timestamp) for timestamp, quality in samples.items() if start <= timestamp < end]
                if eligible:
                    scene["capture_sec"] = max(eligible)[1]
                scenes.append(scene)
                review["scenes"].append({**scene, "status": "readable_slide" if eligible else "no_readable_slide"})
            review["status"] = "complete"
            _write_json(review_path, review)
            return scenes
        except Exception as exc:
            review["status"] = "failed"
            review["error"] = str(exc)
            _write_json(review_path, review)
            raise
        finally:
            if isinstance(reader, _VideoFrameReader):
                reader.close()
