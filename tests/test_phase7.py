"""Fixes found by using the tool against a real repository."""

import json

import pytest

from agent_handoff import checkpoint as cp
from agent_handoff import health as health_mod
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="task one", source_agent="codex", fallback_agent="claude-code"))
    return s


# -- starting a second task -------------------------------------------------


def test_a_new_task_does_not_inherit_the_previous_checkpoint(store):
    """The worst kind of wrong: confidently wrong about what is going on."""
    cp.write(store, cp.Checkpoint(objective="the first task's goal"))
    assert main(["init", "--force", "task two"]) == 0

    assert store.read_state_md() is None
    assert cp.last_record(store) is None
    text = (store.dir / "task.json").read_text(encoding="utf-8")
    assert "task two" in text


def test_a_new_task_starts_with_an_empty_journal(store):
    from agent_handoff.events import log_event

    log_event(store, "file_modified", path="a.py")
    old_id = store.read_task().task_id

    assert main(["init", "--force", "task two"]) == 0
    events = list(store.iter_jsonl(store.events_file))
    assert [e["event"] for e in events] == ["task_started"]
    assert events[0]["task_id"] != old_id


def test_the_previous_task_is_archived_not_deleted(store, capsys):
    from agent_handoff.events import log_event

    cp.write(store, cp.Checkpoint(objective="the first task's goal"))
    log_event(store, "file_modified", path="a.py")
    old_id = store.read_task().task_id

    assert main(["init", "--force", "task two"]) == 0
    assert "archived the previous task" in capsys.readouterr().out

    kept = store.archive_dir / old_id
    assert (kept / "state.md").read_text(encoding="utf-8").find("first task") > -1
    assert json.loads((kept / "task.json").read_text(encoding="utf-8"))["task_id"] == old_id
    assert any(
        e["event"] == "file_modified" for e in store.iter_jsonl(kept / "events.jsonl")
    )


def test_archiving_twice_does_not_collide(store):
    for label in ("two", "three"):
        assert main(["init", "--force", "task " + label]) == 0
    assert len(list(store.archive_dir.iterdir())) == 2


def test_the_handoff_package_is_archived_too(store):
    from agent_handoff import handoff

    handoff.write_package(store)
    old_id = store.read_task().task_id
    assert main(["init", "--force", "task two"]) == 0
    assert not store.handoff_file.exists()
    assert (store.archive_dir / old_id / "HANDOFF.md").is_file()


# -- the end of the chain ---------------------------------------------------


def _at_the_end_of_the_chain(store, attempted):
    task = store.read_task()
    task.source_agent = "claude-code"
    task.attempted = attempted
    store.write_task(task)


def test_a_recovered_agent_becomes_a_target_again(store, capsys):
    """codex ran out an hour ago; its window has since reset."""
    _at_the_end_of_the_chain(store, ["codex"])
    book = health_mod.Health.open()
    book.record_failure("codex", "quota_exhausted")
    book.record_success("codex")  # the limit reset

    assert main(["switch", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "codex has recovered" in out
    assert "codex" in out


def test_an_agent_still_cooling_down_is_not_offered(store, capsys):
    _at_the_end_of_the_chain(store, ["codex"])
    health_mod.Health.open().record_failure("codex", "quota_exhausted")

    assert main(["switch", "--dry-run"]) == 2
    out = capsys.readouterr().out
    assert "no agent left in the chain" in out
    assert "waiting on: codex" in out
    assert "quota_exhausted" in out


def test_the_end_of_the_chain_still_ends(store, capsys):
    # Nothing attempted, nothing recovered: there is genuinely nowhere to go.
    _at_the_end_of_the_chain(store, [])
    assert main(["switch", "--dry-run"]) == 2
    assert "no agent left in the chain" in capsys.readouterr().out


# -- test results -----------------------------------------------------------


def test_recording_a_test_run_fills_the_handoff_section(store, tmp_path):
    from agent_handoff import handoff

    output = tmp_path / "pytest.txt"
    output.write_text("F" * 3 + "\n3 failed, 12 passed in 0.4s\n", encoding="utf-8")
    assert (
        main(
            [
                "tests",
                "pytest -q",
                "--exit-code",
                "1",
                "--summary",
                "3 failed, 12 passed",
                "--from-file",
                str(output),
            ]
        )
        == 0
    )

    recorded = json.loads(store.tests_file.read_text(encoding="utf-8"))
    assert recorded["passed"] is False
    assert recorded["summary"] == "3 failed, 12 passed"
    assert "3 failed" in recorded["output_tail"]

    package = handoff.render(store)
    assert "Latest test results" in package
    assert "3 failed, 12 passed" in package


def test_a_passing_run_is_recorded_as_passing(store):
    assert main(["tests", "pytest -q", "--exit-code", "0"]) == 0
    assert json.loads(store.tests_file.read_text(encoding="utf-8"))["passed"] is True


def test_a_test_run_also_lands_in_the_command_journal(store):
    main(["tests", "pytest -q", "--exit-code", "1"])
    commands = list(store.iter_jsonl(store.commands_file))
    assert commands and commands[-1]["cmd"] == "pytest -q"


def test_status_leads_with_whether_the_work_passes(store, capsys):
    main(["tests", "pytest -q", "--exit-code", "1", "--summary", "3 failed"])
    assert main(["status"]) == 0
    assert "tests    : FAILED  pytest -q  (3 failed)" in capsys.readouterr().out


def test_a_manual_switch_records_the_agents_health(store):
    health_mod.Health.open().clear()
    assert main(["switch", "--to", "claude", "--reason", "quota_exhausted", "--no-launch"]) == 0
    book = health_mod.Health.open()
    assert book.get("codex").reason == "quota_exhausted"
    assert book.cooling("codex")


def test_a_manual_switch_records_that_the_agent_ran_out(store):
    assert main(["switch", "--to", "claude", "--reason", "quota_exhausted", "--no-launch"]) == 0
    assert store.read_task().attempted == ["codex"]


def test_after_a_manual_handoff_the_chain_can_come_back(store, capsys):
    """The interactive flow, end to end: codex out, claude out, codex is back."""
    assert main(["switch", "--to", "claude", "--reason", "quota_exhausted", "--no-launch"]) == 0
    task = store.read_task()
    task.source_agent = "claude-code"  # claude took over, then ran out too
    store.write_task(task)

    # Still cooling: nowhere to go, and it says what it is waiting for.
    assert main(["switch", "--dry-run"]) == 2
    assert "waiting on: codex" in capsys.readouterr().out

    health_mod.Health.open().record_success("codex")  # the window reset
    assert main(["switch", "--dry-run"]) == 0
    assert "codex has recovered" in capsys.readouterr().out


def test_the_quota_cooldown_matches_an_observed_window(store):
    from agent_handoff.health import DEFAULT_COOLDOWNS

    # A real Codex exhaustion reported window_minutes: 300 and a reset 226
    # minutes out; an hour would have called it ready far too early.
    assert DEFAULT_COOLDOWNS["quota_exhausted"] >= 226 * 60


# -- the supervised child's standard input ----------------------------------


def test_a_supervised_agent_never_waits_on_stdin(store):
    """Found by running the real CLI: `handoff run` hung for want of EOF.

    Codex appends piped stdin to its prompt, so an inherited handle that never
    closes blocks it forever -- silently, until the stall timeout fires a
    quarter of an hour later.
    """
    import subprocess
    import sys

    from agent_handoff.adapters.base import AgentAdapter

    class Reader(AgentAdapter):
        name = "codex"
        executable = sys.executable

        def resume_argv(self, instruction):
            # Exits only if stdin reaches EOF.
            return [self.executable, "-c", "import sys; sys.stdin.read(); print('done')"]

    process = Reader(store.root).start("go", capture_output=True)
    assert process.wait(timeout=20) == 0
    assert "done" in (process.stdout.read() if process.stdout else "")


def test_an_interactive_launch_keeps_its_terminal(store, monkeypatch):
    # A person needs to be able to type into the agent that takes over.
    import subprocess

    from agent_handoff.adapters.base import AgentAdapter

    seen = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        raise OSError("not actually starting anything")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: True)

    class Any(AgentAdapter):
        name = "codex"
        executable = "codex"

        def resume_argv(self, instruction):
            return ["codex", instruction]

    for adapter_call in (True, False):
        try:
            Any(store.root).start("go", capture_output=adapter_call)
        except Exception:
            pass
        expected = subprocess.DEVNULL if adapter_call else None
        assert seen["stdin"] == expected
