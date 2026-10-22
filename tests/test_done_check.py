"""A clean exit is not the same as a finished task.

An agent can exit 0 without doing what was asked -- it gave up, ran out of
turns, or stopped to ask a question nobody is there to answer -- and the
supervisor used to mark every clean exit done.
"""

import json
import sys

import pytest

from agent_handoff import config as config_mod
from agent_handoff import testresult
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.health import Health
from agent_handoff.models import Task
from agent_handoff.runner import run
from agent_handoff.store import Store


class _Script(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-u", "-c", self.script]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="write done.txt", source_agent="codex", fallback_agent="claude-code"))
    return s


#: Passes only once the work exists.
DONE_CHECK = sys.executable + ' -c "import pathlib, sys; sys.exit(0 if pathlib.Path(\'done.txt\').exists() else 1)"'
GIVES_UP = "print('I could not finish, stopping here')"
FINISHES = "import pathlib; pathlib.Path('done.txt').write_text('ok'); print('finished')"


def events(store, name):
    return [e for e in store.iter_jsonl(store.events_file) if e["event"] == name]


def test_a_clean_exit_that_passes_the_done_check_is_done(store):
    assert run(store, _Script(store.root, "codex", FINISHES),
               [_Script(store.root, "claude-code", "print('unused')")], done_check=DONE_CHECK) == 0

    assert store.read_task().status == "done"
    assert events(store, "agent_completed")[-1]["verified"] is True
    recorded = json.loads(store.tests_file.read_text(encoding="utf-8"))
    assert recorded["cmd"] == DONE_CHECK and recorded["passed"] is True


def test_a_clean_exit_that_fails_the_done_check_is_handed_on(store):
    health = Health.open()
    health.clear()
    assert run(store, _Script(store.root, "codex", GIVES_UP),
               [_Script(store.root, "claude-code", FINISHES)], done_check=DONE_CHECK, health=health) == 0

    task = store.read_task()
    assert task.status == "done"
    assert task.source_agent == "claude-code"
    assert [e["reason"] for e in events(store, "agent_unavailable")] == ["exited_unfinished"]
    # Giving up says nothing about whether the agent can be reached.
    assert not Health.open().cooling("codex")


def test_the_next_agent_is_told_what_still_fails(store):
    run(store, _Script(store.root, "codex", GIVES_UP),
        [_Script(store.root, "claude-code", FINISHES)], done_check=DONE_CHECK)

    package = store.handoff_file.read_text(encoding="utf-8")
    assert "exited_unfinished" in package
    assert "done check" in package


def git(root, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def test_without_a_done_check_a_current_passing_result_verifies_the_exit(store):
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(store.root, *args)
    (store.root / "done.txt").write_text("ok", encoding="utf-8")
    git(store.root, "add", "done.txt")
    git(store.root, "commit", "-qm", "work")
    testresult.write(store, "python -m pytest -q", 0, "12 passed")

    run(store, _Script(store.root, "codex", "print('nothing left to do')"),
        [_Script(store.root, "claude-code", "print('unused')")])

    assert store.read_task().status == "done"


def test_a_passing_result_outside_git_cannot_be_shown_current(store):
    """Without git nothing says the result was taken on this code."""
    testresult.write(store, "python -m pytest -q", 0, "12 passed")

    run(store, _Script(store.root, "codex", "print('nothing left to do')"),
        [_Script(store.root, "claude-code", "print('unused')")])

    assert store.read_task().status == "exited"


def test_without_a_done_check_or_a_result_a_clean_exit_is_reported_unverified(store):
    run(store, _Script(store.root, "codex", GIVES_UP),
        [_Script(store.root, "claude-code", "print('unused')")])

    task = store.read_task()
    assert task.status == "exited"
    assert task.source_agent == "codex"  # nothing to hand on: no evidence either way
    completed = events(store, "agent_completed")[-1]
    assert completed["verified"] is False
    assert "no done check" in completed["detail"]


def test_a_failing_result_does_not_verify_the_exit(store):
    testresult.write(store, "python -m pytest -q", 1, "3 failed")

    run(store, _Script(store.root, "codex", "print('done, I think')"),
        [_Script(store.root, "claude-code", "print('unused')")])

    assert store.read_task().status == "exited"


def test_when_every_agent_gives_up_the_chain_ends_with_a_package(store):
    run(store, _Script(store.root, "codex", GIVES_UP),
        [_Script(store.root, "claude-code", GIVES_UP)], done_check=DONE_CHECK)

    assert store.read_task().status == "handoff_prepared"
    assert [e["reason"] for e in events(store, "agent_unavailable")] == ["exited_unfinished"] * 2


def test_done_check_is_read_from_the_config(tmp_path):
    (tmp_path / ".agent-handoff").mkdir()
    (tmp_path / ".agent-handoff" / "config.toml").write_text(
        'primary = "codex"\nfallback = ["claude-code"]\ndone_check = "python -m pytest -q"\n', encoding="utf-8")
    assert config_mod.load(tmp_path).done_check == "python -m pytest -q"


def test_a_done_check_that_is_not_a_command_is_refused(tmp_path):
    (tmp_path / ".agent-handoff").mkdir()
    (tmp_path / ".agent-handoff" / "config.toml").write_text(
        'primary = "codex"\nfallback = ["claude-code"]\ndone_check = 1\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="done_check"):
        config_mod.load(tmp_path)
