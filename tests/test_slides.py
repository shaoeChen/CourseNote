from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def detector_type():
    try:
        return importlib.import_module("course_note.segmentation.slides").HybridSlideDetector
    except ModuleNotFoundError:
        pytest.fail("Hybrid slide detector is not implemented")


class Frames:
    def __init__(self, page_at, quality_at=lambda t: 0.8, salt=""):
        self.page_at = page_at
        self.quality_at = quality_at
        self.salt = salt
        self.times = []

    def __call__(self, video, timestamp, directory):
        self.times.append(timestamp)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{timestamp:.6f}.jpg"
        path.write_text(json.dumps({"time": timestamp, "page": self.page_at(timestamp),
                                    "quality": self.quality_at(timestamp), "salt": self.salt}))
        return path


class Vision:
    def __init__(self, model="vision-a", response=None):
        self.model = model
        self.response = response
        self.calls = []

    def resolve_vision_config(self, backend=None, timeout_seconds=None):
        return SimpleNamespace(model=self.model, api_base="http://vision/v1", api_key="secret")

    def chat_images(self, config, prompt, images):
        reference, current = [json.loads(path.read_text()) for path in images]
        self.calls.append((reference, current))
        if self.response is not None:
            return self.response(reference, current)
        visible = current["page"] is not None
        return json.dumps({"slide_visible": visible, "readable": visible,
                           "relation": ("same" if reference["page"] == current["page"] else "new") if visible else "unknown",
                           "confidence": 0.99 if visible else 0.0,
                           "quality": current["quality"] if visible else 0.0, "reason": "synthetic comparison"})


def detect(tmp_path, frames, client=None, candidates=None, interval=5):
    video = tmp_path / "video.mp4"
    if not video.exists():
        video.write_bytes(b"fake video")
    return detector_type()(client=client or Vision(), frame_reader=frames).detect(
        video, candidates or [{"start_sec": 0, "end_sec": 20}], tmp_path,
        scan_interval=interval)


def report(tmp_path):
    return json.loads((tmp_path / "slide_review.json").read_text())


def test_camera_cuts_and_progressive_reveals_merge_and_best_frame_is_used(tmp_path):
    frames = Frames(lambda t: "A" if t < 6 or t >= 9 else None,
                    quality_at=lambda t: 1.0 if 9 <= t <= 10 else 0.5)
    scenes = detect(tmp_path, frames, candidates=[{"start_sec": 0, "end_sec": 6},
                    {"start_sec": 6, "end_sec": 9}, {"start_sec": 9, "end_sec": 20}])
    assert len(scenes) == 1
    assert scenes[0]["start_sec"] == 0
    assert scenes[0]["end_sec"] == 20
    assert scenes[0]["capture_sec"] == 10
    assert report(tmp_path)["uncertain_ranges"]


def test_periodic_scan_recovers_missed_page_and_refines_boundary(tmp_path):
    scenes = detect(tmp_path, Frames(lambda t: "A" if t < 7.2 else "B"))
    assert len(scenes) == 2
    assert 7.2 <= scenes[1]["start_sec"] <= 7.7
    assert scenes[0]["end_sec"] == scenes[1]["start_sec"]
    assert all(s["start_sec"] < s["end_sec"] for s in scenes)
    assert scenes[-1]["end_sec"] == 20
    assert all(s["start_sec"] <= s["capture_sec"] < s["end_sec"] for s in scenes)


def test_unknown_frames_preserve_last_readable_reference(tmp_path):
    vision = Vision()
    detect(tmp_path, Frames(lambda t: None if 4 <= t < 8 else "A"), vision)
    at_ten = [(old, new) for old, new in vision.calls if new["time"] == 10]
    assert at_ten and at_ten[0][0]["page"] == "A"
    assert len(report(tmp_path)["scenes"]) == 1


def test_unknown_refinement_records_uncertain_bracket(tmp_path):
    def page(t):
        return "A" if t <= 5 else None if t < 9 else "B"
    scenes = detect(tmp_path, Frames(page))
    assert len(scenes) == 2
    uncertainty = report(tmp_path)["uncertain_ranges"]
    assert any(item["reason"] == "uncertain_boundary" and item["start_sec"] < item["end_sec"] for item in uncertainty)
    assert report(tmp_path)["boundaries"][0]["exact"] is False


def test_intro_and_entire_no_slide_video_are_covered_without_fabricated_capture(tmp_path):
    scenes = detect(tmp_path, Frames(lambda t: None))
    assert scenes == [{"start_sec": 0.0, "end_sec": 20.0}]
    assert report(tmp_path)["scenes"][0]["status"] == "no_readable_slide"


def test_intro_before_first_readable_slide_is_preserved_and_flagged(tmp_path):
    scenes = detect(tmp_path, Frames(lambda t: None if t < 7 else "A"))
    assert len(scenes) == 2
    assert scenes[0]["start_sec"] == 0
    assert "capture_sec" not in scenes[0]
    assert report(tmp_path)["uncertain_ranges"]


@pytest.mark.parametrize("invalid", ["not json", "{}", '{"slide_visible":true,"readable":true,"relation":"same","confidence":1.2,"quality":0.8,"reason":"bad"}',
    '{"slide_visible":true,"readable":true,"relation":"same","confidence":true,"quality":0.8,"reason":"bad"}'])
def test_invalid_response_stops_with_clear_error(tmp_path, invalid):
    with pytest.raises(RuntimeError, match="vision.*(JSON|schema|response)"):
        detect(tmp_path, Frames(lambda t: "A"), Vision(response=lambda a, b: invalid))


def test_cache_reuses_identical_images_but_changes_for_model_and_image_bytes(tmp_path):
    first = Vision()
    detect(tmp_path, Frames(lambda t: "A"), first)
    assert first.calls
    second = Vision()
    detect(tmp_path, Frames(lambda t: "A"), second)
    assert second.calls == []
    different_model = Vision(model="vision-b")
    detect(tmp_path, Frames(lambda t: "A"), different_model)
    assert different_model.calls
    different_bytes = Vision()
    detect(tmp_path, Frames(lambda t: "A", salt="changed"), different_bytes)
    assert different_bytes.calls
    assert "secret" not in (tmp_path / "vision_cache.json").read_text()


def test_api_failure_keeps_incremental_successful_cache(tmp_path):
    client = Vision()
    original = client.chat_images
    def fail_after_bootstrap(config, prompt, images):
        if client.calls:
            raise RuntimeError("connection lost")
        return original(config, prompt, images)
    client.chat_images = fail_after_bootstrap
    with pytest.raises(RuntimeError, match="vision.*5"):
        detect(tmp_path, Frames(lambda t: "A"), client)
    assert (tmp_path / "vision_cache.json").exists()
    assert len(json.loads((tmp_path / "vision_cache.json").read_text())["entries"]) == 1
    assert report(tmp_path)["status"] == "failed"


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_scan_interval_is_rejected_before_requests(tmp_path, interval):
    vision = Vision()
    with pytest.raises(ValueError, match="scan_interval"):
        detect(tmp_path, Frames(lambda t: "A"), vision, interval=interval)
    assert vision.calls == []


def test_short_new_slide_discovered_during_refinement_is_not_discarded(tmp_path):
    scenes = detect(tmp_path, Frames(lambda t: "A" if t < 7 else "B" if t < 8 else "C"))
    assert len(scenes) == 3
    assert 7 <= scenes[1]["start_sec"] <= 7.5
    assert 8 <= scenes[2]["start_sec"] <= 8.5


def test_low_confidence_same_is_reported_without_becoming_reference(tmp_path):
    vision = Vision()
    def answer(old, new):
        return json.dumps({"slide_visible": True, "readable": True, "relation": "same",
            "confidence": 0.3 if new["time"] == 5 else 0.95,
            "quality": 0.8, "reason": "uncertain"})
    vision.response = answer
    scenes = detect(tmp_path, Frames(lambda t: "A"), vision)
    assert len(scenes) == 1
    at_ten = [(old, new) for old, new in vision.calls if new["time"] == 10]
    assert at_ten[0][0]["time"] == 0
    assert any(item["current_sec"] == 5 and not item["reliable"] for item in report(tmp_path)["decisions"])


def test_cached_responses_are_validated(tmp_path):
    detect(tmp_path, Frames(lambda t: "A"))
    path = tmp_path / "vision_cache.json"
    cache = json.loads(path.read_text())
    cache["entries"] = {key: {} for key in cache["entries"]}
    path.write_text(json.dumps(cache))
    vision = Vision()
    with pytest.raises(RuntimeError, match="vision.*schema"):
        detect(tmp_path, Frames(lambda t: "A"), vision)
    assert vision.calls == []


def test_changed_endpoint_invalidates_cache(tmp_path):
    detect(tmp_path, Frames(lambda t: "A"))
    vision = Vision()
    vision.resolve_vision_config = lambda **kwargs: SimpleNamespace(model=vision.model, api_base="http://other/v1")
    detect(tmp_path, Frames(lambda t: "A"), vision)
    assert vision.calls


def test_candidate_neighbors_find_short_slide_hidden_by_transition_frame(tmp_path):
    def page(t):
        return "A" if t < 4 or t >= 5 else "B" if 4.2 <= t < 4.8 else None
    frames = Frames(page)
    scenes = detect(tmp_path, frames, candidates=[{"start_sec": 0, "end_sec": 4},
        {"start_sec": 4, "end_sec": 5}, {"start_sec": 5, "end_sec": 20}])
    assert 3.5 in frames.times and 4.5 in frames.times and 5.5 in frames.times
    assert len(scenes) == 3
    assert all(s["start_sec"] < s["end_sec"] for s in scenes)
    assert scenes[1]["capture_sec"] == 4.5


def test_refined_boundary_reports_bracket_without_claiming_exact_timestamp(tmp_path):
    detect(tmp_path, Frames(lambda t: "A" if t < 7.2 else "B"))
    boundary = report(tmp_path)["boundaries"][0]
    assert boundary["refined"] is True
    assert boundary["exact"] is False
    assert 0 < boundary["end_sec"] - boundary["start_sec"] <= 0.5


def test_actual_synthetic_video_ignores_camera_and_extracts_bounded_frames(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    video = tmp_path / "synthetic.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (1600, 900))
    if not writer.isOpened():
        pytest.skip("MJPG video writer unavailable")
    try:
        for index in range(100):
            # A -> camera -> progressively revealed A -> B.
            color = (0, 255, 0) if 30 <= index < 40 else (0, 0, 255) if index < 62 else (255, 0, 0)
            image = np.full((900, 1600, 3), color, dtype=np.uint8)
            if 40 <= index < 62:
                cv2.rectangle(image, (100, 100), (600, 200), (0, 0, 0), -1)
            writer.write(image)
    finally:
        writer.release()
    source_directory = tmp_path / "中文影片"
    source_directory.mkdir()
    video = video.rename(source_directory / "video.avi")
    captures = []
    real_capture = cv2.VideoCapture
    class Capture:
        def __init__(self, path):
            self.inner = real_capture(path)
            self.released = False
            captures.append(self)
        def __getattr__(self, key):
            return getattr(self.inner, key)
        def release(self):
            self.released = True
            self.inner.release()
    monkeypatch.setattr(cv2, "VideoCapture", Capture)
    class ImageVision(Vision):
        def chat_images(self, config, prompt, images):
            decoded = [cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR) for path in images]
            assert all(max(image.shape[:2]) <= 1280 for image in decoded)
            pages = [int(np.argmax(image.mean(axis=(0, 1)))) for image in decoded]
            visible = pages[1] != 1
            self.calls.append(images)
            return json.dumps({"slide_visible": visible, "readable": visible,
                "relation": ("same" if pages[0] == pages[1] else "new") if visible else "unknown",
                "confidence": 0.99 if visible else 0, "quality": 0.8 if visible else 0, "reason": "color slide"})
    vision = ImageVision()
    workdir = tmp_path / "課程預覽"
    scenes = detector_type()(client=vision).detect(video,
        [{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 4}, {"start_sec": 4, "end_sec": 10}],
        workdir, scan_interval=2)
    assert len(scenes) == 2
    assert 6.2 <= scenes[1]["start_sec"] <= 6.7
    assert len(captures) == 1 and captures[0].released
    assert list((workdir / "vision_frames").glob("*.jpg"))
    repeated = ImageVision()
    detector_type()(client=repeated).detect(video,
        [{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 4}, {"start_sec": 4, "end_sec": 10}],
        workdir, scan_interval=2)
    assert repeated.calls == []
    assert len(captures) == 1


def test_actual_capture_is_released_after_read_failure(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    capture = SimpleNamespace(isOpened=lambda: True, get=lambda key: 0,
                              set=lambda key, value: None, read=lambda: (False, None))
    closed = []
    capture.release = lambda: closed.append(True)
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: capture)
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"bad video")
    vision = Vision()
    with pytest.raises(RuntimeError, match="Unable to read.*0.000"):
        detector_type()(client=vision).detect(video, [{"start_sec": 0, "end_sec": 10}], tmp_path)
    assert closed == [True]
    assert vision.calls == []
    assert report(tmp_path)["status"] == "failed"


def test_decoded_timestamps_drive_boundaries_and_readable_captures(tmp_path):
    class QuantizedFrames(Frames):
        def __init__(self):
            super().__init__(lambda t: "A" if t < 19.5 else "B")
            self.capture_times = {}
        def __call__(self, video, timestamp, directory):
            actual = int(timestamp * 2) / 2
            self.capture_times[timestamp] = actual
            return super().__call__(video, actual, directory)
    scenes = detect(tmp_path, QuantizedFrames())
    assert len(scenes) == 2
    assert scenes[1]["start_sec"] == 19.5
    assert scenes[1]["capture_sec"] == 19.5
    assert all(s["start_sec"] <= s["capture_sec"] < s["end_sec"] for s in scenes)
    assert any(item["current_sec"] == 19.5 and item["current_requested_sec"] == 19.95
               for item in report(tmp_path)["decisions"])


def test_actual_decoded_timestamp_persists_with_cached_jpeg_without_reopening_video(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    module = importlib.import_module("course_note.segmentation.slides")
    video = tmp_path / "lowfps.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 2, (160, 120))
    assert writer.isOpened()
    try:
        for index in range(8):
            writer.write(np.full((120, 160, 3), 80, dtype=np.uint8))
    finally:
        writer.release()
    directory = tmp_path / "vision_frames"
    directory.mkdir()
    reader = module._VideoFrameReader()
    try:
        image = reader(video, 3.95, directory)
        assert reader.capture_times[3.95] == pytest.approx(3.5)
        assert image.with_suffix(".json").exists()
    finally:
        reader.close()
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: pytest.fail("Cached JPEG should not reopen video"))
    repeated = module._VideoFrameReader()
    assert repeated(video, 3.95, directory) == image
    assert repeated.capture_times[3.95] == pytest.approx(3.5)
    repeated.close()


def test_invalid_presentation_timestamp_uses_decoded_frame_index(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    module = importlib.import_module("course_note.segmentation.slides")
    metadata = {cv2.CAP_PROP_FPS: 10, cv2.CAP_PROP_FRAME_COUNT: 100,
                cv2.CAP_PROP_POS_MSEC: float("nan"), cv2.CAP_PROP_POS_FRAMES: 100}
    capture = SimpleNamespace(isOpened=lambda: True, get=lambda key: metadata[key],
                              set=lambda key, value: None,
                              read=lambda: (True, np.full((20, 30, 3), 80, dtype=np.uint8)),
                              release=lambda: None)
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: capture)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic")
    directory = tmp_path / "vision_frames"
    directory.mkdir()
    reader = module._VideoFrameReader()
    try:
        reader(video, 9.95, directory)
        assert reader.capture_times[9.95] == 9.9
    finally:
        reader.close()


def test_temporal_resolution_stagnation_marks_uncertain_bracket(tmp_path):
    class CoarseFrames(Frames):
        def __init__(self):
            super().__init__(lambda t: "A" if t < 18 else "B")
            self.capture_times = {}
        def __call__(self, video, timestamp, directory):
            actual = int(timestamp / 2) * 2
            self.capture_times[timestamp] = actual
            return super().__call__(video, actual, directory)
    scenes = detect(tmp_path, CoarseFrames())
    assert len(scenes) == 2
    assert scenes[1]["start_sec"] == 18
    assert scenes[1]["capture_sec"] == 18
    assert report(tmp_path)["boundaries"][0]["refined"] is False
    assert any(item["reason"] == "uncertain_boundary" for item in report(tmp_path)["uncertain_ranges"])


def test_reader_does_not_invent_timestamp_when_both_decoded_cursors_are_invalid(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    module = importlib.import_module("course_note.segmentation.slides")
    capture = SimpleNamespace(isOpened=lambda: True, get=lambda key: 0,
        set=lambda key, value: None, read=lambda: (True, np.zeros((20, 30, 3), dtype=np.uint8)), release=lambda: None)
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: capture)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic")
    directory = tmp_path / "vision_frames"
    directory.mkdir()
    reader = module._VideoFrameReader()
    try:
        with pytest.raises(RuntimeError, match="Unable to determine decoded.*timestamp"):
            reader(video, 9.95, directory)
    finally:
        reader.close()
