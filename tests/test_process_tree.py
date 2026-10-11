"""Process-tree termination fallbacks."""

import os
import subprocess
import sys

import pytest

from agent_handoff import runner


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill fallback")
@pytest.mark.parametrize("failure", ["nonzero", "unavailable"])
def test_windows_kill_falls_back_when_taskkill_fails(monkeypatch, failure):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    calls = []

    def fail_taskkill(argv, **kwargs):
        calls.append(argv)
        if failure == "unavailable":
            raise OSError("taskkill is unavailable")
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(runner.subprocess, "run", fail_taskkill)
    try:
        runner._kill_process_tree(process)
        process.wait(timeout=5)

        assert calls and calls[0][0] == "taskkill"
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_a_worker_left_holding_the_pipes_does_not_hold_the_supervisor(tmp_path, monkeypatch):
    """An agent that exits but leaves a background worker on its stdout.

    The pipe stays open as long as the worker lives, and the readers were
    joined without a bound, so `handoff run` waited for the worker instead of
    moving on.
    """
    import time

    from agent_handoff.models import Task
    from agent_handoff.store import Store

    store = Store(tmp_path)
    store.init(Task(original_request="t", source_agent="codex", fallback_agent="claude-code"))
    store.logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "PIPE_DRAIN_SECONDS", 1.0)
    agent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys; print('done', flush=True); "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    started = time.monotonic()
    exit_code, stdout, _, stalled = runner._drain(store, agent)

    assert time.monotonic() - started < 15
    assert exit_code == 0 and not stalled
    assert "done" in stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill fallback")
def test_the_kill_reports_whether_the_whole_tree_is_known_gone(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert runner._kill_process_tree(process) is True
    finally:
        process.wait(timeout=5)

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    try:
        assert runner._kill_process_tree(process) is False  # only the direct child
    finally:
        process.wait(timeout=5)
