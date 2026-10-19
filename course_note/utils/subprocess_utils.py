from __future__ import annotations

import subprocess
from collections.abc import Sequence


class CommandExecutionError(subprocess.CalledProcessError):
    """Keep subprocess metadata while displaying captured failure diagnostics."""

    def __str__(self) -> str:
        message = super().__str__()
        diagnostics = (self.stderr or "").strip() or (self.stdout or "").strip()
        return f"{message}\n{diagnostics}" if diagnostics else message


def run_command(command: Sequence[str], *, stream: bool = False) -> subprocess.CompletedProcess[str]:
    if stream:
        # Merge the streams to preserve ordering and avoid stderr pipe deadlocks.
        with subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        ) as process:
            output: list[str] = []
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    output.append(line)
                    print(line, end="", flush=True)
                returncode = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
        captured = "".join(output)
        if returncode:
            raise CommandExecutionError(returncode, list(command), output=captured)
        return subprocess.CompletedProcess(list(command), returncode, stdout=captured, stderr="")
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
