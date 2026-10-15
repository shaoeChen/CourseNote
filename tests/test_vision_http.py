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
        monkeypatch.setenv("COURSE_NOTE_VISION_PROVIDER", "vllm")
        monkeypatch.setenv("COURSE_NOTE_VLLM_MODEL", "test-vision")
        monkeypatch.setenv("COURSE_NOTE_VLLM_API_BASE", f"http://127.0.0.1:{server.server_port}/v1")
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


def test_segment_review_real_jpegs_http_context_cache_and_final_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Validate real image transport and decoded capture reuse without a paid model."""
    from course_note.models.core import TranscriptSegment
    from course_note.segmentation.segment_review import SegmentSlideReviewer

    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required for final screenshot verification')
    video = tmp_path / 'segment-lecture.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 10., (160, 120))
    assert writer.isOpened()
    try:
        for index in range(100):
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            frame[:] = (0, 0, 255) if index < 50 else (0, 255, 0)
            writer.write(frame)
    finally:
        writer.release()
    requests = []
    observed_pages = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == '/v1/chat/completions'
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            contents = body['messages'][0]['content']
            pages = []
            for part in contents[1:]:
                data_url = part['image_url']['url']
                assert data_url.startswith('data:image/jpeg;base64,')
                raw = base64.b64decode(data_url.split(',', 1)[1])
                image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                assert image is not None
                pages.append(int(image.mean(axis=(0, 1)).argmax()))
            observed_pages.append(pages)
            judgment = dict(status='ok', confidence=.99, same_slide_as_previous=False,
                            capture_index=1, reason='test readable matching page',
                            evidence=[], note_mismatch=False, needs_local_analysis=False)
            result = json.dumps({'choices': [{'message': {'content': json.dumps(judgment)}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('COURSE_NOTE_VLLM_MODEL', 'test-segment-vision')
        monkeypatch.setenv('COURSE_NOTE_VLLM_API_BASE', f'http://127.0.0.1:{server.server_port}/v1')
        candidates = [dict(start_sec=0., end_sec=5.), dict(start_sec=5., end_sec=10.)]
        transcripts = [TranscriptSegment(0., 5., 'red page narration', 'en', 'synthetic'),
                       TranscriptSegment(5., 10., 'green page narration', 'en', 'synthetic')]
        analysis = tmp_path / 'segment-analysis'
        scenes = SegmentSlideReviewer().review(video, candidates, transcripts, analysis, backend='vllm')
        assert len(requests) == 2
        assert observed_pages == [[2, 2, 2], [1, 1, 1, 2]]
        assert all(body['model'] == 'test-segment-vision' for body in requests)
        contexts = [json.loads(body['messages'][0]['content'][0]['text'].split('CONTEXT_JSON\n')[1])
                    for body in requests]
        assert contexts[0]['transcript'] == [dict(start_sec=0., end_sec=5., text='red page narration')]
        assert contexts[1]['transcript'] == [dict(start_sec=5., end_sec=10., text='green page narration')]
        assert contexts[0]['next_transcript'] == contexts[1]['transcript']
        assert contexts[1]['previous_capture_sec'] == scenes[0]['capture_sec']
        assert [(scene['start_sec'], scene['end_sec']) for scene in scenes] == [(0., 5.), (5., 10.)]
        assert all(scene['start_sec'] <= scene['capture_sec'] < scene['end_sec'] for scene in scenes)
        assert SegmentSlideReviewer().review(video, candidates, transcripts, analysis, backend='vllm') == scenes
        assert len(requests) == 2
        report = json.loads((analysis / 'slide_review.json').read_text(encoding='utf-8'))
        assert report['statistics']['model_requests'] == 0
        assert report['statistics']['cache_hits'] == 2
        final_frames = tmp_path / 'segment-final-frames'
        FrameSampler().sample_for_scenes(video, final_frames, scenes, 1.)
        segments = SegmentBuilder().build(scenes, transcripts, final_frames)
        colors = []
        for segment in segments:
            image = cv2.imdecode(np.frombuffer((final_frames / segment.screenshot).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            assert image is not None
            colors.append(int(image.mean(axis=(0, 1)).argmax()))
        assert colors == [2, 1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
