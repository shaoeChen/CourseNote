from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


@contextmanager
def progress_stage(
    description: str, log_path: Path, *, interval: float | None = 10.0,
) -> Iterator[None]:
    """Report stages; disable the heartbeat when an operation shows actual progress."""
    started = time.monotonic()
    stop = threading.Event()
    lock = threading.Lock()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(event: str, detail: str = "") -> None:
        line = (f"{datetime.now().astimezone().isoformat(timespec='seconds')} [{event}] "
                f"{description} elapsed={time.monotonic() - started:.1f}s {detail}").rstrip()
        with lock:
            with log_path.open('a', encoding='utf-8') as log:
                log.write(line + '\n')
            print(line, flush=True)

    def waiting() -> None:
        while not stop.wait(interval):
            emit('WAIT', '仍在處理中')

    emit('START')
    heartbeat = threading.Thread(target=waiting, daemon=True) if interval is not None else None
    if heartbeat is not None:
        heartbeat.start()
    try:
        yield
    except BaseException as exc:
        stop.set()
        if heartbeat is not None:
            heartbeat.join()
        emit('INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'ERROR',
             f'{type(exc).__name__}: {exc}')
        raise
    else:
        stop.set()
        if heartbeat is not None:
            heartbeat.join()
        emit('DONE')
