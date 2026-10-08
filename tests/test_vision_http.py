from __future__ import annotations

import base64
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from course_note.segmentation.slides import HybridSlideDetector
from course_note.segmentation.timeline import SegmentBuilder
from course_note.video.frames import FrameSampler


@pytest.mark.parametrize("fps", [10., 29.97])
def test_real_jpeg_frames_cross_http_and_refine_a_page_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fps: float) -> None:
    """Exercise real extraction and serialization; the server is a deterministic test judge."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    video = tmp_path / "lecture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 120))
    assert writer.isOpened()
    try:
        for index in range(100):
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            frame[:] = (0, 0, 255) if index < 45 else (0, 255, 0)
            writer.write(frame)
    finally:
        writer.release()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            contents = body["messages"][0]["content"]
            pages = []
            for part in contents[1:]:
                raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                assert image is not None
                pages.append(int(image.mean(axis=(0, 1)).argmax()))
            judgment = {"slide_visible": True, "readable": True,
                "relation": "same" if pages[0] == pages[1] else "new",
                "confidence": 1., "quality": .9, "reason": "test frame color"}
            result = json.dumps({"choices": [{"message": {"content": json.dumps(judgment)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("COURSE_NOTE_VISION_MODEL", "test-vision")
        monkeypatch.setenv("COURSE_NOTE_VISION_API_BASE", f"http://127.0.0.1:{server.server_port}/v1")
        analysis = tmp_path / "analysis"
        duration = 100 / fps
        candidates = [{"start_sec": 0., "end_sec": duration}]
        scenes = HybridSlideDetector().detect(video, candidates, analysis, scan_interval=duration / 2)
        assert len(scenes) == 2
        assert 45 / fps <= scenes[0]["end_sec"] <= 45 / fps + .5
        assert scenes[1]["start_sec"] == scenes[0]["end_sec"]
        assert scenes[1]["end_sec"] == duration
        assert all(s["start_sec"] <= s["capture_sec"] < s["end_sec"] for s in scenes)
        assert requests and all(body["model"] == "test-vision" for body in requests)
        if shutil.which("ffmpeg"):
            final_frames = tmp_path / "frames"
            FrameSampler().sample_for_scenes(video, final_frames, scenes, 1.)
            segments = SegmentBuilder().build(scenes, [], final_frames)
            colors = []
            for segment in segments:
                image = cv2.imdecode(np.frombuffer((final_frames / segment.screenshot).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
                assert image is not None
                colors.append(int(image.mean(axis=(0, 1)).argmax()))
            assert colors == [2, 1]
        calls = len(requests)
        assert HybridSlideDetector().detect(video, candidates, analysis, scan_interval=duration / 2) == scenes
        assert len(requests) == calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
