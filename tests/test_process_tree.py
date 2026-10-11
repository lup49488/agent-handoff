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


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_process_outside_its_own_session_is_still_killed():
    """`killpg` finds no group to kill; reporting success there left it running."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert runner._kill_process_tree(process) is False  # only the direct child was reachable
        assert process.wait(timeout=5) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def start_leader_with_worker(tmp_path, leader_lingers):
    """A session leader that starts a worker which touches a marker until killed.

    Scripts go in files: an earlier version built the worker's code as a
    nested `-c` string, its newline became a real one, and the leader died of
    a SyntaxError before the test began — passing on Linux by accident.
    """
    import time

    marker = tmp_path / "worker-alive"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import pathlib, time\n"
        "for _ in range(300):\n"
        "    pathlib.Path(%r).touch()\n"
        "    time.sleep(0.1)\n" % str(marker),
        encoding="utf-8",
    )
    leader = tmp_path / "leader.py"
    leader.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, %r])\n"
        "time.sleep(%d)\n" % (str(worker), 60 if leader_lingers else 0),
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(leader)], start_new_session=True)
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "the worker never started"
    return process, marker


def worker_stopped(marker):
    import time

    marker.unlink(missing_ok=True)
    time.sleep(0.5)
    return not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_session_leader_is_killed_with_everything_it_started(tmp_path):
    process, marker = start_leader_with_worker(tmp_path, leader_lingers=True)

    assert runner._kill_process_tree(process) is True
    process.wait(timeout=5)
    assert worker_stopped(marker)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_worker_outliving_its_exited_leader_is_still_killed(tmp_path):
    """The leader exits on its own, unreaped; its worker is still in the group."""
    import time

    process, marker = start_leader_with_worker(tmp_path, leader_lingers=False)
    time.sleep(0.5)  # the leader has exited but nothing has reaped it

    assert runner._kill_process_tree(process) is True
    assert worker_stopped(marker)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_macos_refusal_to_signal_an_unreaped_leaders_group_is_retried(tmp_path, monkeypatch):
    """macOS raises PermissionError where Linux signals the group; reap and retry."""
    import time

    process, marker = start_leader_with_worker(tmp_path, leader_lingers=False)
    time.sleep(0.5)
    real_killpg = runner.os.killpg
    calls = []

    def like_macos(pgid, sig):
        calls.append(pgid)
        if process.returncode is None:  # leader not reaped yet
            raise PermissionError(1, "Operation not permitted")
        real_killpg(pgid, sig)

    monkeypatch.setattr(runner.os, "killpg", like_macos)

    assert runner._kill_process_tree(process) is True
    assert len(calls) == 2
    assert worker_stopped(marker)
