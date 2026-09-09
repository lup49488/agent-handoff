import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_handoff import gitinfo, handoff
from agent_handoff.cli import main
from agent_handoff.events import last_failure, log_command, log_event
from agent_handoff.models import Task
from agent_handoff.runner import classify_failure, run
from agent_handoff.store import Store, StoreNotInitialized, find_root
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.adapters.base import AdapterError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def init(project, request="Fix the authentication refresh bug"):
    store = Store(project)
    store.init(Task(original_request=request, source_agent="codex", fallback_agent="claude-code"))
    return store


def test_init_creates_the_working_dir(project):
    store = init(project)
    assert store.task_file.is_file()
    assert store.events_file.is_file()
    assert store.logs_dir.is_dir()
    assert store.read_task().original_request.startswith("Fix the authentication")


def test_find_root_walks_up(project):
    init(project)
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_root(nested) == project.resolve()


def test_open_without_init_raises(project):
    with pytest.raises(StoreNotInitialized):
        Store.open(project)


def test_journal_survives_a_partial_line(project):
    store = init(project)
    log_event(store, "file_modified", path="src/auth.py")
    with open(store.events_file, "a", encoding="utf-8") as fh:
        fh.write('{"event": "truncated_by_a_ki')  # process died mid-write
    records = list(store.iter_jsonl(store.events_file))
    assert [r["event"] for r in records] == ["file_modified"]


def test_last_failure_returns_the_most_recent(project):
    store = init(project)
    log_event(store, "agent_unavailable", agent="codex", reason="rate_limited")
    log_event(store, "file_modified", path="src/auth.py")
    log_event(store, "agent_unavailable", agent="codex", reason="quota_exhausted")
    assert last_failure(store)["reason"] == "quota_exhausted"


def test_commands_land_in_both_journals(project):
    store = init(project)
    log_command(store, "pytest tests/test_auth.py", exit_code=1)
    commands = list(store.iter_jsonl(store.commands_file))
    assert commands[0]["cmd"] == "pytest tests/test_auth.py"
    assert any(r["event"] == "command" for r in store.iter_jsonl(store.events_file))


def test_git_collect_on_a_non_repo(project):
    state = gitinfo.collect(project)
    assert state.is_repo is False


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def repo(project):
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "test")
    (project / "auth.py").write_text("def refresh():\n    pass\n", encoding="utf-8")
    _git(project, "add", "auth.py")
    _git(project, "commit", "-qm", "initial")
    return project


def test_git_collect_sees_modifications(repo):
    (repo / "auth.py").write_text("def refresh():\n    return 1\n", encoding="utf-8")
    (repo / "new_test.py").write_text("x = 1\n", encoding="utf-8")
    state = gitinfo.collect(repo)
    assert state.is_repo and state.dirty
    assert "auth.py" in state.modified
    assert "new_test.py" in state.untracked
    assert state.head and state.branch


def test_render_contains_everything_the_next_agent_needs(repo):
    store = init(repo)
    (repo / "auth.py").write_text("def refresh():\n    return 1\n", encoding="utf-8")
    store.write_git(gitinfo.collect(repo))
    log_command(store, "pytest tests/test_auth.py", exit_code=1)
    log_event(store, "agent_unavailable", agent="codex", reason="quota_exhausted")
    store.state_file.write_text("# Current Objective\n\nFix the race.\n", encoding="utf-8")

    text = handoff.render(store, reason="quota_exhausted", target_agent="claude-code")
    assert "# HANDOFF" in text
    assert "Fix the authentication refresh bug" in text
    assert "Fix the race." in text
    assert "auth.py" in text
    assert "pytest tests/test_auth.py" in text
    assert "Do not restart the task from scratch." in text


def test_render_without_a_semantic_checkpoint_says_so(repo):
    store = init(repo)
    store.write_git(gitinfo.collect(repo))
    assert "None recorded" in handoff.render(store)


def test_cli_flow_end_to_end(repo, capsys):
    assert main(["init", "Fix the auth refresh bug", "--from", "codex", "--to", "claude"]) == 0
    (repo / "auth.py").write_text("def refresh():\n    return 1\n", encoding="utf-8")
    assert main(["event", "file_modified", "path=auth.py"]) == 0
    assert main(["command", "pytest", "--exit-code", "1"]) == 0
    assert main(["snapshot"]) == 0
    assert main(["status"]) == 0
    assert main(["codex", "--to", "claude", "--reason", "quota_exhausted", "--no-launch"]) == 0

    out = capsys.readouterr().out
    assert "not launching claude-code" in out

    package = (repo / "HANDOFF.md").read_text(encoding="utf-8")
    assert "quota_exhausted" in package
    assert "auth.py" in package

    task = json.loads((repo / ".agent-handoff" / "task.json").read_text(encoding="utf-8"))
    assert task["source_agent"] == "codex"
    assert task["fallback_agent"] == "claude-code"


def test_cli_no_launch_bumps_the_task(repo):
    main(["init", "Fix it"])
    assert main(["switch", "--to", "claude", "--no-launch"]) == 0
    store = Store(repo)
    task = store.read_task()
    assert task.status == "handoff_prepared"
    assert task.handoff_count == 0
    assert task.source_agent == "codex"


def test_cli_rejects_a_handoff_to_the_same_agent(repo):
    main(["init", "Fix it", "--from", "codex"])
    assert main(["switch", "--to", "codex", "--dry-run"]) == 2


def test_cli_reports_missing_init(project, capsys):
    assert main(["status"]) == 1
    assert "handoff init" in capsys.readouterr().out


def test_unknown_agent_is_not_treated_as_shorthand(repo, capsys):
    main(["init", "Fix it"])
    with pytest.raises(SystemExit):
        main(["no-such-agent", "--to", "claude"])


def test_atomic_write_leaves_no_tmp_file(project):
    store = init(project)
    assert not list(store.dir.glob("*.tmp"))


def test_handoff_own_files_are_not_reported_as_task_changes(repo):
    store = init(repo)
    handoff.write_package(store)
    state = gitinfo.collect(repo)
    assert not any(p.startswith(".agent-handoff") for p in state.changed_files)
    assert "HANDOFF.md" not in state.changed_files


def test_semantic_checkpoint_headings_are_nested(repo):
    store = init(repo)
    store.write_git(gitinfo.collect(repo))
    store.state_file.write_text("# Current Objective\n\nFix the race.\n", encoding="utf-8")
    text = handoff.render(store)
    assert "### Current Objective" in text
    assert "\n# Current Objective" not in text


@pytest.mark.parametrize(
    ("output", "exit_code", "expected"),
    [
        ("Error: rate limit exceeded", 1, "rate_limited"),
        ("quota exhausted", 1, "quota_exhausted"),
        ("context window exceeded", 1, "context_exhausted"),
        ("", 1, "process_crashed"),
        ("", 0, None),
    ],
)
def test_failure_classifier(output, exit_code, expected):
    assert classify_failure(output, "", exit_code) == expected


class ScriptAdapter(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-c", self.script]


def test_supervised_run_completes_and_records_output(project):
    store = init(project)
    primary = ScriptAdapter(project, "codex", "print('completed')")
    fallback = ScriptAdapter(project, "claude-code", "print('fallback')")
    assert run(store, primary, fallback) == 0
    assert store.read_task().status == "done"
    assert "completed" in (store.logs_dir / "stdout.log").read_text(encoding="utf-8")


def test_supervised_run_hands_off_after_rate_limit(project):
    store = init(project)
    primary = ScriptAdapter(
        project, "codex", "import sys; print('rate limit exceeded', file=sys.stderr); raise SystemExit(1)"
    )
    fallback = ScriptAdapter(project, "claude-code", "print('resumed')")
    assert run(store, primary, fallback) == 0
    task = store.read_task()
    # The fallback finished the work, so the task is done -- not merely
    # handed off. Its exit is classified like any other agent's.
    assert task.status == "done"
    assert task.source_agent == "claude-code"
    assert task.handoff_count == 1
    assert last_failure(store)["reason"] == "rate_limited"
    assert "rate_limited" in store.handoff_file.read_text(encoding="utf-8")


class FailingStartAdapter(ScriptAdapter):
    def start(self, prompt, *, capture_output=False):
        raise AdapterError("fallback could not start")


def test_supervised_run_keeps_prepared_state_when_fallback_does_not_start(project):
    store = init(project)
    primary = ScriptAdapter(project, "codex", "raise SystemExit(1)")
    fallback = FailingStartAdapter(project, "claude-code", "")
    # A fallback that will not start has taken over nothing: the chain is
    # exhausted rather than the task being marked handed off.
    assert run(store, primary, fallback) == 1
    assert store.read_task().status == "handoff_prepared"
    assert store.read_task().handoff_count == 0
    assert any(
        event["event"] == "handoff_launch_failed" for event in store.iter_jsonl(store.events_file)
    )


@pytest.mark.parametrize(
    "output",
    ["Reviewing the rate limit handler; all tests pass", "1 passed, test_x timed out earlier"],
)
def test_clean_exit_is_never_a_failure(output):
    # The agent's own transcript is full of words the classifier looks for.
    assert classify_failure(output, "", 0) is None
