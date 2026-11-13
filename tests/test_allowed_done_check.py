"""The configured done check is the one command beyond the handoff CLI an agent may run.

In a real failover drill a Claude Code that took over tried three times to
run the tests, was refused each time -- its command line allows only the
handoff CLI -- and could only judge its predecessor's work by reading it. It
also ran `python -m unittest` where the check was `python -m unittest -q`,
so the agent is told the exact command as well.
"""

import sys

import pytest

from agent_handoff.adapters import registry
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.models import Task
from agent_handoff.runner import done_check_note, run
from agent_handoff.store import Store

CHECK = "python -m unittest -q"


def test_claude_code_is_allowed_the_done_check_through_either_shell(tmp_path):
    adapter = registry.get("claude-code", tmp_path)
    adapter.allowed_commands = (CHECK,)
    argv = adapter.exec_argv("do the task")

    rules = argv[argv.index("--allowedTools") + 1:argv.index("--permission-mode")]
    assert "Bash(" + CHECK + ")" in rules and "PowerShell(" + CHECK + ")" in rules
    assert "Bash(handoff *)" in rules  # the built-in rules stay
    assert argv[-1] == "do the task"


def test_without_a_done_check_the_command_line_is_unchanged(tmp_path):
    adapter = registry.get("claude-code", tmp_path)
    assert adapter.exec_argv("x") == list(adapter.spec.argv("x", interactive=False))


def test_an_agent_without_a_permission_list_is_left_alone(tmp_path):
    adapter = registry.get("codex", tmp_path)
    adapter.allowed_commands = (CHECK,)
    assert adapter.exec_argv("x") == list(adapter.spec.argv("x", interactive=False))


class _Recording(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script, prompts):
        super().__init__(root)
        self.name = name
        self.script = script
        self.prompts = prompts

    def resume_argv(self, instruction):
        self.prompts.append((self.name, instruction, tuple(self.allowed_commands)))
        return [self.executable, "-c", self.script]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="fix it", source_agent="codex", fallback_agent="claude-code"))
    return s


DIES = "import sys; print('quota exhausted', file=sys.stderr); raise SystemExit(1)"
FINISHES = "import pathlib; pathlib.Path('done.txt').write_text('ok')"
DONE = sys.executable + ' -c "import pathlib, sys; sys.exit(0 if pathlib.Path(\'done.txt\').exists() else 1)"'


def test_every_agent_in_the_chain_is_told_and_allowed_the_done_check(store):
    prompts = []
    run(store, _Recording(store.root, "codex", DIES, prompts),
        [_Recording(store.root, "claude-code", FINISHES, prompts)], done_check=DONE)

    assert [name for name, _, _ in prompts] == ["codex", "claude-code"]
    for _, prompt, allowed in prompts:
        assert "`" + DONE + "` passes" in prompt
        assert allowed == (DONE,)


def test_without_a_done_check_nothing_is_added(store):
    prompts = []
    run(store, _Recording(store.root, "codex", DIES, prompts),
        [_Recording(store.root, "claude-code", "print('done')", prompts)])

    for _, prompt, allowed in prompts:
        assert "counts as done" not in prompt
        assert allowed == ()
    assert done_check_note(None) == ""
