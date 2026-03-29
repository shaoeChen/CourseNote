from __future__ import annotations

import json
import math
from pathlib import Path


class SceneDetectorService:
    def detect(
        self,
        video_path: Path,
        threshold: float,
        min_segment_seconds: int,
        edge_weight: float = 0.0,
        stats_csv_path: Path | None = None,
    ) -> list[dict[str, float]]:
        try:
            from scenedetect import ContentDetector, SceneManager, StatsManager, open_video
        except ImportError as exc:
            raise RuntimeError(
                "PySceneDetect is required for scene detection. Install or sync 'scenedetect' in the active environment to continue."
            ) from exc

        if edge_weight < 0:
            raise ValueError("edge_weight must be greater than or equal to 0.")

        video = open_video(str(video_path))
        min_scene_len_frames = self._seconds_to_frame_count(min_segment_seconds, getattr(video, "frame_rate", None))
        manager = SceneManager(StatsManager() if stats_csv_path else None)
        weights = ContentDetector.Components(delta_hue=1.0, delta_sat=1.0, delta_lum=1.0, delta_edges=edge_weight)
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len_frames, weights=weights))
        manager.detect_scenes(video)
        if stats_csv_path and manager.stats_manager is not None:
            manager.stats_manager.save_to_csv(csv_file=stats_csv_path)
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

    @staticmethod
    def _seconds_to_frame_count(min_segment_seconds: int, frame_rate: float | None) -> int:
        if frame_rate is None or frame_rate <= 0:
            return max(int(min_segment_seconds), 1)
        return max(math.ceil(min_segment_seconds * frame_rate), 1)
