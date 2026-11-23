"""Fixes from a review of the supervised-run changes since 0.9.2."""

import subprocess
import sys

import pytest

from agent_handoff import config as config_mod
from agent_handoff import runner, testresult
from agent_handoff.adapters import registry
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.runner import done_check_note, run
from agent_handoff.store import Store


class _Script(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script, prompts=None):
        super().__init__(root)
        self.name = name
        self.script = script
        self.prompts = prompts if prompts is not None else []

    def resume_argv(self, instruction):
        self.prompts.append(instruction)
        return [self.executable, "-c", self.script]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="fix it", source_agent="codex", fallback_agent="claude-code"))
    return s


def git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


# -- 1. a done check that cannot run does not walk the chain --------------------------


def test_a_done_check_that_is_not_a_command_ends_the_run_instead_of_handing_on(store):
    second = []
    run(store, _Script(store.root, "codex", "print('done')"),
        [_Script(store.root, "claude-code", "print('unused')", second)],
        done_check="no-such-command-for-agent-handoff-tests")

    task = store.read_task()
    assert task.status == "exited"
    assert task.source_agent == "codex" and task.handoff_count == 0
    assert second == []  # the fallback was never launched
    completed = [e for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_completed"]
    assert completed[-1]["detail"].startswith("done check could not run")


def test_a_done_check_found_on_path_or_in_the_project_is_run(store):
    assert runner._check_program(sys.executable + " -c pass", store.root) is None
    (store.root / "check.sh").write_text("exit 0\n", encoding="utf-8")
    assert runner._check_program("./check.sh --quick", store.root) is None
    assert runner._check_program("no-such-tool --flag", store.root) == "no-such-tool"
    # Not looked up: the shell judges these itself.
    assert runner._check_program("FOO=1 no-such-tool", store.root) is None
    assert runner._check_program("cd sub && no-such-tool", store.root) is None
    assert runner._check_program("no-such-tool | tee log", store.root) is None


def test_a_done_check_that_times_out_ends_the_run_instead_of_handing_on(store, monkeypatch):
    monkeypatch.setattr(runner, "DONE_CHECK_TIMEOUT", 1)
    run(store, _Script(store.root, "codex", "print('done')"),
        [_Script(store.root, "claude-code", "print('unused')")],
        done_check=sys.executable + ' -c "import time; time.sleep(10)"')

    assert store.read_task().status == "exited"
    assert store.read_task().handoff_count == 0


def test_a_done_check_that_runs_and_fails_still_hands_on(store):
    run(store, _Script(store.root, "codex", "print('gave up')"),
        [_Script(store.root, "claude-code", "print('also gave up')")],
        done_check=sys.executable + ' -c "raise SystemExit(1)"')

    reasons = [e["reason"] for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_unavailable"]
    assert reasons[0] == "exited_unfinished"


# -- 2. a permission rule only for a command it cannot widen or break -----------------


@pytest.mark.parametrize("command", ["pytest tests/*.py", 'python -c "print(1)"'])
def test_a_command_with_rule_characters_is_not_put_in_a_rule(tmp_path, command):
    adapter = registry.get("claude-code", tmp_path)
    adapter.allowed_commands = (command,)
    argv = adapter.exec_argv("x")

    assert not any(command in part for part in argv[:-1])
    assert not registry.can_allow(command)
    assert "may need approval" in done_check_note(command, allowed=registry.can_allow(command))


def test_a_plain_command_is_still_allowed(tmp_path):
    assert registry.can_allow("python -m unittest -q")
    assert "allowed without asking" in done_check_note("python -m unittest -q")


# -- 3. CPU use alone cannot keep a silent agent alive forever ------------------------


def test_cpu_use_alone_keeps_a_silent_agent_alive_only_so_long(store, monkeypatch):
    monkeypatch.setattr(runner, "CPU_ONLY_LIMIT", 3)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _, _, _, stalled = runner._drain(store, process, stall_timeout=1, activity_probe=lambda: "cpu")
    assert stalled


def test_a_changing_work_tree_has_no_such_limit(store, monkeypatch):
    monkeypatch.setattr(runner, "CPU_ONLY_LIMIT", 1)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    code, _, _, stalled = runner._drain(store, process, stall_timeout=1, activity_probe=lambda: "workspace")
    assert not stalled and code == 0


# -- 4. the tree is hashed in pieces, and once per resume ----------------------------


def test_resume_hashes_the_tree_once(store, monkeypatch, capsys):
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(store.root, *args)
    (store.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(store.root, "add", "a.py")
    git(store.root, "commit", "-qm", "i")
    testresult.write(store, "pytest", 0)
    calls = []
    real = testresult.tree
    monkeypatch.setattr(testresult, "tree", lambda root: calls.append(root) or real(root))

    assert main(["resume"]) == 0
    assert len(calls) == 1
    assert "**Current:**" in capsys.readouterr().out


def test_a_large_untracked_file_hashes_the_same_in_pieces(store, monkeypatch):
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(store.root, *args)
    (store.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(store.root, "add", "a.py")
    git(store.root, "commit", "-qm", "i")
    (store.root / "data.bin").write_bytes(b"0123456789" * 50000)

    whole = testresult.tree(store.root)
    monkeypatch.setattr(testresult, "_CHUNK", 7)
    assert testresult.tree(store.root) == whole


# -- 5. the configured primary is adopted only by a running task ----------------------


def test_a_finished_task_is_not_reassigned_to_the_configured_primary(store, monkeypatch):
    import agent_handoff.cli as cli_mod

    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: True)
    task = store.read_task()
    task.status = "done"
    store.write_task(task)
    (store.dir / "config.toml").write_text('primary = "claude-code"\nfallback = ["codex"]\n', encoding="utf-8")
    monkeypatch.setattr(cli_mod, "supervised_run", lambda *a, **kw: 0)

    main(["run"])
    assert store.read_task().source_agent == "codex"
    assert not [e for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_selected"]


# -- 6. the config says what the done check is ----------------------------------------


def test_the_config_description_names_the_done_check():
    assert "done check: python -m unittest -q" in config_mod.from_dict(
        {"done_check": "python -m unittest -q"}).describe()
    assert "done check: none" in config_mod.Config().describe()


# -- 7. a rename is not read as a garbled path ----------------------------------------


def test_a_rename_leaves_no_garbled_path_in_the_workspace_state(store):
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(store.root, *args)
    (store.root / "old_name.py").write_text("x = 1\n", encoding="utf-8")
    git(store.root, "add", "old_name.py")
    git(store.root, "commit", "-qm", "i")
    git(store.root, "mv", "old_name.py", "new_name.py")

    paths = {entry[0] for entry in runner._workspace_state(store)}
    # The rename is a deletion and an addition; nothing else, and nothing cut short.
    assert {p for p in paths if p.endswith(".py")} == {"old_name.py", "new_name.py"}
