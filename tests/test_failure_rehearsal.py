"""Repeated failure rehearsal for the repo-local handoff contract.

Unit tests exercise individual branches. This deliberately repeats different
recoverable failures, because a handoff tool must preserve the same evidence
on the twentieth interruption as on the first one. All agents are short-lived
Python subprocesses; no installed coding-agent executable is invoked.
"""

import json
import subprocess
import sys

import pytest

from agent_handoff import integrity
from agent_handoff import lock as lock_mod
from agent_handoff.events import log_event
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.models import Task
from agent_handoff.models import now as models_now
from agent_handoff.runner import run
from agent_handoff.store import Store


class ScriptAgent(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-c", self.script]


FAILURES = (
    ("quota_exhausted", "import sys; print('quota exhausted', file=sys.stderr); raise SystemExit(1)"),
    ("rate_limited", "import sys; print('rate_limit_error', file=sys.stderr); raise SystemExit(1)"),
    ("process_crashed", "raise SystemExit(7)"),
    # An agent can also fail by not failing: silent, never exiting. Until it
    # was rehearsed here the drills only covered agents that stop by
    # themselves, which is the easier half of the problem.
    ("agent_stalled", "import time; time.sleep(60)"),
)

#: Long enough that a healthy agent is never called stalled, short enough to
#: rehearse twenty times.
STALL_TIMEOUT = 1.0
FINISHES = "print('fallback completed the task')"


@pytest.mark.parametrize("attempt", range(20))
def test_twenty_injected_failovers_preserve_work_and_a_valid_package(tmp_path, attempt):
    """Every recoverable interruption leaves the work and package usable."""
    expected_reason, failing_script = FAILURES[attempt % len(FAILURES)]
    root = tmp_path / str(attempt)
    root.mkdir()
    work_file = root / "important-work.txt"
    original = "uncommitted work must survive interruption " + str(attempt)
    work_file.write_text(original, encoding="utf-8")

    store = Store(root)
    store.init(Task(original_request="Finish the task", source_agent="codex", fallback_agent="claude-code"))

    assert run(
        store,
        ScriptAgent(root, "codex", failing_script),
        [ScriptAgent(root, "claude-code", FINISHES)],
        stall_timeout=STALL_TIMEOUT,
    ) == 0

    assert work_file.read_text(encoding="utf-8") == original
    assert store.read_task().status == "done"
    assert integrity.verify_package(store) == []

    events = list(store.iter_jsonl(store.events_file))
    unavailable = [event for event in events if event["event"] == "agent_unavailable"]
    assert [event["reason"] for event in unavailable] == [expected_reason]
    assert any(event["event"] == "handoff_started" for event in events)


# -- what an interruption leaves behind -------------------------------------
#
# The drills above interrupt an agent. These repeat the damage an interruption
# leaves in the package itself: a journal line cut in half, a temp file from a
# write that never finished, and a lock whose holder is gone. Each must leave
# the recorded work readable, because a package that cannot be read is the
# same loss as no package at all.


@pytest.fixture
def interrupted(tmp_path):
    """A package with real recorded work, ready to be damaged."""
    store = Store(tmp_path)
    store.init(Task(original_request="Finish the task", source_agent="codex", fallback_agent="claude-code"))
    log_event(store, "file_modified", path="auth.py")
    log_event(store, "file_modified", path="token.py")
    return store


@pytest.mark.parametrize("attempt", range(5))
def test_a_journal_line_cut_in_half_never_costs_the_lines_before_it(interrupted, attempt):
    """A killed append leaves a partial final line; the rest still counts."""
    with interrupted.events_file.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-01-01T00:00:00+00:00", "event": "file_mod')

    events = list(interrupted.iter_jsonl(interrupted.events_file))
    assert [event["path"] for event in events] == ["auth.py", "token.py"]

    # And the next append starts a fresh line rather than splicing onto it.
    log_event(interrupted, "tests_run", exit_code=0)
    events = list(interrupted.iter_jsonl(interrupted.events_file))
    assert events[-1]["event"] == "tests_run"

    findings = integrity.inspect(interrupted)
    assert any("unreadable line" in f.message for f in findings)
    assert integrity.errors(findings) == []


def test_recovery_drops_the_damaged_line_and_keeps_the_work(interrupted):
    with interrupted.events_file.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "half')

    integrity.recover(interrupted)

    events = list(interrupted.iter_jsonl(interrupted.events_file))
    assert [event["path"] for event in events] == ["auth.py", "token.py"]
    assert not any("unreadable line" in f.message for f in integrity.inspect(interrupted))


@pytest.mark.parametrize("attempt", range(5))
def test_a_temp_file_from_an_unfinished_write_is_reported_then_removed(interrupted, attempt):
    leftover = interrupted.dir / ("task.json.tmp" if attempt % 2 else "state.md.tmp")
    leftover.write_text("half a file", encoding="utf-8")
    before = interrupted.read_task().original_request

    assert any("leftover " + leftover.name in f.message for f in integrity.inspect(interrupted))

    integrity.recover(interrupted)

    assert not leftover.exists()
    assert interrupted.read_task().original_request == before


def test_a_lock_whose_holder_is_gone_is_reported_and_can_be_broken(interrupted):
    """A crashed process must not strand the package behind its own lock."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    interrupted.lock_file.write_text(
        json.dumps({"pid": dead.pid, "command": "checkpoint", "acquired": "2020-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    assert any("lock" in f.message for f in integrity.inspect(interrupted))

    integrity.recover(interrupted, break_lock=True)

    assert not interrupted.lock_file.exists()
    assert integrity.errors(integrity.inspect(interrupted)) == []


def test_recovery_refuses_to_clean_up_underneath_a_live_supervised_run(interrupted):
    """The other half of lock contention: never rewrite a working session."""
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        interrupted.run_lock_file.write_text(
            json.dumps({"pid": alive.pid, "command": "run", "acquired": models_now()}),
            encoding="utf-8",
        )
        with pytest.raises(lock_mod.LockBusy):
            integrity.recover(interrupted)
    finally:
        alive.kill()
        alive.wait()
