from __future__ import annotations

import subprocess
from collections.abc import Sequence


class CommandExecutionError(subprocess.CalledProcessError):
    """Keep subprocess metadata while displaying captured failure diagnostics."""

    def __str__(self) -> str:
        message = super().__str__()
        diagnostics = (self.stderr or "").strip() or (self.stdout or "").strip()
        return f"{message}\n{diagnostics}" if diagnostics else message


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        raise CommandExecutionError(
            exc.returncode, exc.cmd, output=exc.output, stderr=exc.stderr
        ) from exc
