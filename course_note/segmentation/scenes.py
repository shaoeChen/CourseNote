from __future__ import annotations

import json
from pathlib import Path


class SceneDetectorService:
    def detect(self, video_path: Path, threshold: float, min_segment_seconds: int) -> list[dict[str, float]]:
        try:
            from scenedetect import ContentDetector, SceneManager, open_video
        except ImportError as exc:
            raise RuntimeError(
                "PySceneDetect is required for scene detection. Install or sync 'scenedetect' in the active environment to continue."
            ) from exc

        video = open_video(str(video_path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_segment_seconds))
        manager.detect_scenes(video)
        scene_list = manager.get_scene_list()
        if not scene_list:
            duration = float(video.duration.get_seconds())
            return [{"start_sec": 0.0, "end_sec": duration}]
        return [
            {
                "start_sec": float(start.get_seconds()),
                "end_sec": float(end.get_seconds()),
            }
            for start, end in scene_list
        ]

    def save(self, scenes: list[dict[str, float]], output_path: Path) -> None:
        output_path.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
