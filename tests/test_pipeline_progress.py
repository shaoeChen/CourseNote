from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from course_note.utils.subprocess_utils import run_command


def test_download_output_is_visible_before_child_exits(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    observed = threading.Event()

    def output(*args, **kwargs):
        if 'download progress' in str(args):
            observed.set()
            release.touch()

    monkeypatch.setattr('builtins.print', output)
    code = (
        "import pathlib,sys,time; "
        "print('download progress', flush=True); "
        "p=pathlib.Path(sys.argv[1]); deadline=time.monotonic()+5\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "sys.exit(0 if p.exists() else 9)"
    )
    result = run_command([sys.executable, '-c', code, str(release)], stream=True)
    assert observed.is_set()
    assert 'download progress' in result.stdout


def test_streaming_failure_preserves_diagnostics(capsys):
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_command([sys.executable, '-c', "import sys; print('download failed', file=sys.stderr); sys.exit(130)"], stream=True)
    assert error.value.returncode == 130
    assert 'download failed' in str(error.value)
    assert 'download failed' in capsys.readouterr().out


def test_stage_reports_wait_and_failure_to_terminal_and_file(tmp_path, capsys):
    from course_note.utils.progress import progress_stage

    log = tmp_path / 'pipeline.log'
    with pytest.raises(RuntimeError, match='broken'):
        with progress_stage('scene detection', log, interval=.01):
            # Wait for the actual heartbeat instead of depending on a fixed sleep.
            deadline = threading.Event()
            for _ in range(200):
                if 'WAIT' in log.read_text(encoding='utf-8'):
                    break
                deadline.wait(.01)
            raise RuntimeError('broken')
    content = log.read_text(encoding='utf-8')
    assert '[START]' in content
    assert '[WAIT]' in content
    assert '[ERROR]' in content
    assert 'broken' in content
    assert '[DONE]' not in content
    assert content == capsys.readouterr().out


def test_stage_records_completion_and_interruption(tmp_path):
    from course_note.utils.progress import progress_stage

    log = tmp_path / 'pipeline.log'
    with progress_stage('download', log):
        assert '[START]' in log.read_text(encoding='utf-8')
    assert '[DONE]' in log.read_text(encoding='utf-8')
    with pytest.raises(KeyboardInterrupt):
        with progress_stage('transcription', log):
            raise KeyboardInterrupt()
    assert '[INTERRUPTED]' in log.read_text(encoding='utf-8')


def test_streaming_decodes_utf8_even_on_windows(capsys):
    # Emit UTF-8 bytes independent of the child's console encoding.
    result = run_command([sys.executable, '-c',
                          "import sys; sys.stdout.buffer.write(bytes([228,184,173,230,150,135,10]))"],
                         stream=True)
    assert result.stdout == '中文\n'
    assert capsys.readouterr().out == '中文\n'


def test_stage_can_disable_heartbeat_when_operation_has_real_progress(tmp_path, monkeypatch):
    from course_note.utils.progress import progress_stage

    def unexpected_thread(*args, **kwargs):
        pytest.fail('A native progress bar must not have a separate heartbeat thread')

    monkeypatch.setattr('course_note.utils.progress.threading.Thread', unexpected_thread)
    log = tmp_path / 'pipeline.log'
    with progress_stage('scene detection', log, interval=None):
        pass
    content = log.read_text(encoding='utf-8')
    assert '[START]' in content
    assert '[DONE]' in content
    assert '[WAIT]' not in content
