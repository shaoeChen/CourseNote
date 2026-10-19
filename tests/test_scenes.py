from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from course_note.segmentation.scenes import SceneDetectorService


class _FakeTimecode:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def get_seconds(self) -> float:
        return self._seconds


class _FakeDuration:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def get_seconds(self) -> float:
        return self._seconds


class _FakeVideo:
    def __init__(self, frame_rate: float, duration_seconds: float) -> None:
        self.frame_rate = frame_rate
        self.duration = _FakeDuration(duration_seconds)


def test_detect_converts_seconds_to_frames_and_saves_stats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    fake_module = ModuleType("scenedetect")

    class FakeStatsManager:
        def save_to_csv(self, csv_file: Path) -> None:
            captured["stats_csv"] = csv_file

    class FakeContentDetector:
        class Components:
            def __init__(
                self,
                delta_hue: float = 1.0,
                delta_sat: float = 1.0,
                delta_lum: float = 1.0,
                delta_edges: float = 0.0,
            ) -> None:
                self.delta_hue = delta_hue
                self.delta_sat = delta_sat
                self.delta_lum = delta_lum
                self.delta_edges = delta_edges

        def __init__(self, threshold: float, min_scene_len: int, weights: object) -> None:
            captured["threshold"] = threshold
            captured["min_scene_len"] = min_scene_len
            captured["weights"] = weights

    class FakeSceneManager:
        def __init__(self, stats_manager: FakeStatsManager | None = None) -> None:
            self.stats_manager = stats_manager

        def add_detector(self, detector: FakeContentDetector) -> None:
            captured["detector"] = detector

        def detect_scenes(self, video: _FakeVideo, *, show_progress: bool = False) -> None:
            captured["video"] = video
            captured["show_progress"] = show_progress

        def get_scene_list(self) -> list[tuple[_FakeTimecode, _FakeTimecode]]:
            return [(_FakeTimecode(0.0), _FakeTimecode(12.5))]

    def fake_open_video(path: str) -> _FakeVideo:
        captured["video_path"] = path
        return _FakeVideo(frame_rate=2.5, duration_seconds=99.0)

    fake_module.ContentDetector = FakeContentDetector
    fake_module.SceneManager = FakeSceneManager
    fake_module.StatsManager = FakeStatsManager
    fake_module.open_video = fake_open_video
    monkeypatch.setitem(sys.modules, "scenedetect", fake_module)

    scenes = SceneDetectorService().detect(
        tmp_path / "video.mp4",
        threshold=31.0,
        min_segment_seconds=4,
        edge_weight=0.2,
        stats_csv_path=tmp_path / "scene_stats.csv",
    )

    assert scenes == [{"start_sec": 0.0, "end_sec": 12.5}]
    assert captured["video_path"] == str(tmp_path / "video.mp4")
    assert captured["threshold"] == 31.0
    assert captured["min_scene_len"] == 10
    assert captured["weights"].delta_edges == 0.2
    assert captured["stats_csv"] == tmp_path / "scene_stats.csv"
    assert captured["show_progress"] is True


def test_detect_returns_full_video_when_no_scenes_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_module = ModuleType("scenedetect")

    class FakeContentDetector:
        class Components:
            def __init__(self, **_: float) -> None:
                return None

        def __init__(self, **_: object) -> None:
            return None

    class FakeSceneManager:
        def __init__(self, stats_manager: object | None = None) -> None:
            self.stats_manager = stats_manager

        def add_detector(self, detector: object) -> None:
            return None

        def detect_scenes(self, video: _FakeVideo, *, show_progress: bool = False) -> None:
            return None

        def get_scene_list(self) -> list[tuple[_FakeTimecode, _FakeTimecode]]:
            return []

    class FakeStatsManager:
        def save_to_csv(self, csv_file: Path) -> None:
            return None

    def fake_open_video(path: str) -> _FakeVideo:
        return _FakeVideo(frame_rate=30.0, duration_seconds=42.0)

    fake_module.ContentDetector = FakeContentDetector
    fake_module.SceneManager = FakeSceneManager
    fake_module.StatsManager = FakeStatsManager
    fake_module.open_video = fake_open_video
    monkeypatch.setitem(sys.modules, "scenedetect", fake_module)

    scenes = SceneDetectorService().detect(tmp_path / "video.mp4", threshold=27.0, min_segment_seconds=20)

    assert scenes == [{"start_sec": 0.0, "end_sec": 42.0}]


def test_real_video_displays_processed_frames_percentage_and_speed(tmp_path, capsys):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    pytest.importorskip('scenedetect')
    video = tmp_path / 'video.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 6, (64, 64))
    assert writer.isOpened()
    try:
        for index in range(12):
            writer.write(np.full((64, 64, 3), 0 if index < 6 else 255, dtype=np.uint8))
    finally:
        writer.release()

    scenes = SceneDetectorService().detect(video, 15, 1, edge_weight=.3)
    output = capsys.readouterr()
    assert '12/12' in output.err
    assert '100%' in output.err
    assert 'frames/s' in output.err
    assert '[DONE]' in output.out
    assert scenes[-1]['end_sec'] == pytest.approx(2)
