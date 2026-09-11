"""Repeated failure rehearsal for the repo-local handoff contract.

Unit tests exercise individual branches. This deliberately repeats different
recoverable failures, because a handoff tool must preserve the same evidence
on the twentieth interruption as on the first one. All agents are short-lived
Python subprocesses; no installed coding-agent executable is invoked.
"""

import sys

import pytest

from agent_handoff import integrity
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.models import Task
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
)
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
    ) == 0

    assert work_file.read_text(encoding="utf-8") == original
    assert store.read_task().status == "done"
    assert integrity.verify_package(store) == []

    events = list(store.iter_jsonl(store.events_file))
    unavailable = [event for event in events if event["event"] == "agent_unavailable"]
    assert [event["reason"] for event in unavailable] == [expected_reason]
    assert any(event["event"] == "handoff_started" for event in events)
