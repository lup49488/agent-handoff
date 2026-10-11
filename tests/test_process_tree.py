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
