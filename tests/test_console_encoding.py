"""A console that cannot encode the task must not lose it.

Found on Windows: `handoff init` with a Chinese task title succeeded, and the
`handoff status` that should have shown it died with `UnicodeEncodeError`
because the console was on `cp932`. The state package was intact and
unreadable at the same time — the exact failure this tool exists to prevent.

These run the CLI as a real subprocess: the bug lives in the encoding of the
process's own stdout, which an in-process fake cannot reproduce faithfully.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent_handoff
from agent_handoff.models import Task
from agent_handoff.store import Store

#: Simplified-only characters. `cp932` covers much of CJK but not these.
UNENCODABLE = "修复认证刷新缺陷"


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path)
    store.init(
        Task(original_request=UNENCODABLE, source_agent="codex", fallback_agent="claude-code")
    )
    return store


def run_on_a_cp932_console(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="cp932")
    env["PYTHONPATH"] = str(Path(agent_handoff.__file__).resolve().parent.parent)
    return subprocess.run(
        [sys.executable, "-m", "agent_handoff", *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="cp932",
        errors="replace",
    )


def test_status_survives_a_console_that_cannot_encode_the_task(project):
    result = run_on_a_cp932_console(project.root, "status")

    assert "UnicodeEncodeError" not in result.stderr
    assert result.returncode == 0
    assert "task_id" in result.stdout


def test_showing_a_checkpoint_survives_the_same_console(project):
    written = run_on_a_cp932_console(
        project.root, "checkpoint", "--objective", UNENCODABLE, "--next", UNENCODABLE
    )
    assert written.returncode == 0

    result = run_on_a_cp932_console(project.root, "checkpoint", "--show")

    assert "UnicodeEncodeError" not in result.stderr
    assert result.returncode == 0


def test_the_package_on_disk_stays_utf8_whatever_the_console_does(project):
    assert run_on_a_cp932_console(project.root, "status").returncode == 0

    assert UNENCODABLE in (project.dir / "task.json").read_text(encoding="utf-8")
