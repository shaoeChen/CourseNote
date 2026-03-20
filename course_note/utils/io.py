from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_workdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
