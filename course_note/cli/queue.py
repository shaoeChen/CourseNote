from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

QUEUE_LOCK_FILENAME = ".queue.lock"


@dataclass(slots=True)
class QueueItem:
    line_number: int
    url: str
    course_name: str | None = None


@dataclass(slots=True)
class QueueResult:
    succeeded: list[QueueItem] = field(default_factory=list)
    skipped: list[QueueItem] = field(default_factory=list)
    failed: list[tuple[QueueItem, BaseException]] = field(default_factory=list)


class QueueLockedError(RuntimeError):
    """Raised when another queue run already holds the workdir lock."""


def parse_queue_file(path: Path) -> list[QueueItem]:
    """Parse one `URL [course name]` per line; blank lines and `#` comments are ignored.

    URLs never contain whitespace, so everything after the first whitespace is the
    course name. This keeps names such as "Part 4 | Tools" intact.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Queue file not found: {path}")
    items: list[QueueItem] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        url, *rest = line.split(maxsplit=1)
        name = rest[0].strip() if rest else ""
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{path}:{line_number}: expected a URL at the start of the line, got '{url}'.")
        items.append(QueueItem(line_number, url, name or None))
    return items


@contextmanager
def queue_lock(workdir: Path) -> Iterator[None]:
    """Refuse a second concurrent queue run so YouTube never sees parallel downloads."""
    workdir.mkdir(parents=True, exist_ok=True)
    lock_path = workdir / QUEUE_LOCK_FILENAME
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise QueueLockedError(
            f"Another queue run is using {workdir} ({lock_path} exists). "
            f"If no queue is running, the previous run was killed; delete the lock file and retry."
        ) from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def run_queue(
    items: list[QueueItem],
    *,
    process: Callable[[QueueItem], bool],
    delay_seconds: float,
    stop_on_error: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> QueueResult:
    """Process items strictly one at a time.

    ``process`` returns False when the item was skipped (already done), True when it
    ran. A pause is inserted only between items that actually hit the network.
    """
    result = QueueResult()
    total = len(items)
    previous_ran = False
    for index, item in enumerate(items, 1):
        label = item.course_name or item.url
        print(f"\n[QUEUE {index}/{total}] {label}", flush=True)
        if previous_ran and delay_seconds > 0:
            print(f"[QUEUE] 等待 {delay_seconds:g} 秒再處理下一部影片，避免連續請求 YouTube。", flush=True)
            sleep(delay_seconds)
        try:
            ran = process(item)
        except Exception as exc:  # noqa: BLE001 - recorded and reported in the summary
            print(f"[QUEUE] 失敗（第 {item.line_number} 行）：{exc}", flush=True)
            result.failed.append((item, exc))
            previous_ran = True
            if stop_on_error:
                break
            continue
        if ran:
            result.succeeded.append(item)
        else:
            result.skipped.append(item)
        previous_ran = ran
    return result


def format_queue_summary(result: QueueResult, total: int) -> str:
    lines = [
        "",
        f"[QUEUE] 完成：成功 {len(result.succeeded)}、略過 {len(result.skipped)}、"
        f"失敗 {len(result.failed)}，共 {total} 筆。",
    ]
    for item, exc in result.failed:
        lines.append(f"  - 第 {item.line_number} 行 {item.url}：{exc}")
    processed = len(result.succeeded) + len(result.skipped) + len(result.failed)
    if processed < total:
        lines.append(f"  - 因 stop_on_error 停止，尚有 {total - processed} 筆未處理。")
    return "\n".join(lines)
