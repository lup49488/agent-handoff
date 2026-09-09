"""Phase 5 -- reliability: locking, crash recovery, handoff validation."""

import json
import os
import subprocess
import sys

import pytest

from agent_handoff import checkpoint as cp
from agent_handoff import gitinfo, handoff
from agent_handoff import integrity, lock as lock_mod
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.store import Store


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path)
    store.init(
        Task(original_request="Fix the auth bug", source_agent="codex", fallback_agent="claude-code")
    )
    return store


# -- process liveness -------------------------------------------------------


def test_this_process_is_alive():
    assert lock_mod.process_alive(os.getpid())


def test_a_finished_process_is_not_alive():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not lock_mod.process_alive(proc.pid)


def test_liveness_probe_does_not_kill_what_it_probes():
    """On Windows os.kill(pid, 0) terminates the target -- we must not use it."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert lock_mod.process_alive(proc.pid)
        assert proc.poll() is None  # still running after being probed
    finally:
        proc.kill()
        proc.wait()


def test_nonsense_pids_are_not_alive():
    assert not lock_mod.process_alive(0)
    assert not lock_mod.process_alive(-1)


# -- the lock ---------------------------------------------------------------


def test_a_lock_excludes_a_second_holder(project):
    with lock_mod.Lock(project.lock_file, "first"):
        with pytest.raises(lock_mod.LockBusy, match="locked by pid"):
            lock_mod.Lock(project.lock_file, "second").acquire()


def test_a_lock_is_released_even_when_the_command_raises(project):
    with pytest.raises(ValueError):
        with lock_mod.Lock(project.lock_file, "boom"):
            raise ValueError("boom")
    assert not project.lock_file.exists()
    lock_mod.Lock(project.lock_file, "next").acquire().release()


def test_a_lock_whose_holder_is_gone_is_broken(project):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    project.lock_file.write_text(
        json.dumps({"pid": proc.pid, "host": lock_mod.socket.gethostname(), "command": "run",
                    "acquired_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    held = lock_mod.Lock(project.lock_file, "next").acquire()
    assert held.broke is not None  # an abandoned lock must never wedge a repo
    held.release()


def test_a_lock_from_another_machine_is_respected(project):
    project.lock_file.write_text(
        json.dumps({"pid": 1, "host": "some-other-box", "command": "run", "acquired_at": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(lock_mod.LockBusy, match="another machine"):
        lock_mod.Lock(project.lock_file, "next").acquire()


def test_an_unreadable_lock_is_debris_not_a_claim(project):
    project.lock_file.write_text("half-written {", encoding="utf-8")
    lock_mod.Lock(project.lock_file, "next").acquire().release()


def test_release_does_not_remove_someone_elses_lock(project):
    mine = lock_mod.Lock(project.lock_file, "mine")
    mine.acquire()
    project.lock_file.write_text(  # someone broke and retook it
        json.dumps({"pid": 999999, "host": "other", "command": "theirs", "acquired_at": "later"}),
        encoding="utf-8",
    )
    mine.release()
    assert project.lock_file.exists()


# -- the write lock in the CLI ---------------------------------------------


def test_the_cli_locks_a_mutating_command(project, capsys):
    with lock_mod.Lock(project.lock_file, "other-process"):
        assert main(["event", "file_modified", "path=auth.py"]) == 1
    assert "locked by pid" in capsys.readouterr().out


def test_read_only_commands_are_never_blocked(project, capsys):
    with lock_mod.Lock(project.lock_file, "other-process"):
        assert main(["status"]) == 0
        assert main(["verify"]) == 0
        assert main(["agents"]) == 0


def test_the_lock_is_gone_after_a_command(project):
    assert main(["event", "file_modified", "path=auth.py"]) == 0
    assert not project.lock_file.exists()


def test_a_supervised_run_does_not_block_the_agents_own_checkpoints(project):
    """The agent is told to checkpoint while it works -- under the run lock."""
    with lock_mod.Lock(project.run_lock_file, "run"):
        assert main(["checkpoint", "--objective", "Fix the race."]) == 0
    assert project.state_file.exists()


def test_run_does_not_take_the_ordinary_lock_for_its_whole_lifetime(project):
    """The supervisor uses run.lock; checkpoints retain access to lock."""
    from agent_handoff.cli import _wants_lock, build_parser

    assert _wants_lock(build_parser().parse_args(["run"])) is False


def test_recover_respects_the_ordinary_write_lock(project, capsys):
    with lock_mod.Lock(project.lock_file, "other-process"):
        assert main(["recover"]) == 1
    assert "locked by pid" in capsys.readouterr().out


def test_recover_refuses_to_mutate_during_a_supervised_run(project):
    with lock_mod.Lock(project.run_lock_file, "run"):
        with pytest.raises(lock_mod.LockBusy, match="supervised run active"):
            integrity.recover(project)


# -- partial writes ---------------------------------------------------------


def test_an_append_after_a_killed_write_starts_a_new_line(project):
    from agent_handoff.events import log_event

    log_event(project, "first")
    with open(project.events_file, "a", encoding="utf-8") as fh:
        fh.write('{"event": "killed mid-w')  # no newline: the process died here
    log_event(project, "second")

    records = [r["event"] for r in project.iter_jsonl(project.events_file)]
    # The truncated line is lost, but it must not swallow the record after it.
    assert records == ["first", "second"]


def test_recover_drops_unreadable_lines(project):
    from agent_handoff.events import log_event

    log_event(project, "first")
    with open(project.events_file, "a", encoding="utf-8") as fh:
        fh.write('{"event": "killed mid-w\n')
    log_event(project, "second")

    actions = integrity.recover(project)
    assert any("unreadable line" in a for a in actions)
    lines = project.events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(line) for line in lines)


def test_recover_removes_leftover_tmp_files(project):
    (project.dir / "state.md.tmp").write_text("half a checkpoint", encoding="utf-8")
    assert any("state.md.tmp" in a for a in integrity.recover(project))
    assert not list(project.dir.glob("*.tmp"))


def test_recover_dry_run_changes_nothing(project):
    (project.dir / "task.json.tmp").write_text("x", encoding="utf-8")
    assert integrity.recover(project, dry_run=True)
    assert (project.dir / "task.json.tmp").exists()


def test_recover_breaks_a_stale_lock_but_not_a_live_one(project):
    with lock_mod.Lock(project.lock_file, "live"):
        assert not any("lock" in a for a in integrity.recover(project))
        assert any("force-break" in a for a in integrity.recover(project, break_lock=True))


# -- package validation -----------------------------------------------------


def test_a_missing_package_is_an_error(project):
    assert "nothing to hand over" in integrity.verify_package(project)[0].message


def test_a_truncated_package_is_an_error(project):
    project.handoff_file.write_text("# HANDOFF\n\n## Task\n", encoding="utf-8")
    messages = [f.message for f in integrity.verify_package(project)]
    assert any("no resume instruction" in m for m in messages)


def test_a_package_from_another_task_is_an_error(project):
    handoff.write_package(project)
    task = project.read_task()
    task.task_id = "0000deadbeef"  # a stale package left by an earlier task
    project.write_task(task)
    messages = [f.message for f in integrity.verify_package(project)]
    assert any("different task" in m for m in messages)


def test_a_good_package_passes(project):
    handoff.write_package(project)
    assert integrity.verify_package(project) == []


def test_a_handoff_refuses_to_launch_on_a_broken_package(project, capsys, monkeypatch):
    from agent_handoff.adapters.base import AgentAdapter

    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(
        handoff, "render", lambda *a, **k: "# HANDOFF\n\ntruncated by a crash\n"
    )
    assert main(["switch", "--to", "claude"]) == 1
    out = capsys.readouterr().out
    assert "refusing to launch" in out


def test_the_runner_refuses_to_hand_off_a_broken_package(project, monkeypatch):
    from agent_handoff.adapters.base import AgentAdapter
    from agent_handoff.runner import run

    class Script(AgentAdapter):
        executable = sys.executable

        def __init__(self, root, name, script):
            super().__init__(root)
            self.name = name
            self.script = script

        def resume_argv(self, instruction):
            return [self.executable, "-c", self.script]

    monkeypatch.setattr(
        "agent_handoff.runner.write_package",
        lambda store, **k: store.handoff_file.write_text("# HANDOFF\n", encoding="utf-8"),
    )
    with pytest.raises(RuntimeError, match="refusing to hand off"):
        run(
            project,
            Script(project.root, "codex", "raise SystemExit(1)"),
            [Script(project.root, "claude-code", "print('x')")],
        )


# -- the overall report -----------------------------------------------------


def test_a_clean_store_reports_nothing(project):
    handoff.write_package(project)
    assert integrity.inspect(project) == []


def test_inspect_notices_a_stale_snapshot(project):
    subprocess.run(["git", "init", "-q"], cwd=str(project.root), check=True, capture_output=True)
    for args in (("config", "user.email", "t@e.com"), ("config", "user.name", "t")):
        subprocess.run(["git", *args], cwd=str(project.root), check=True, capture_output=True)
    (project.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=str(project.root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "one"], cwd=str(project.root), check=True, capture_output=True
    )
    project.write_git(gitinfo.collect(project.root))
    subprocess.run(
        ["git", "commit", "-qm", "two", "--allow-empty"],
        cwd=str(project.root),
        check=True,
        capture_output=True,
    )
    assert any("git.json is stale" in f.message for f in integrity.inspect(project))


def test_inspect_notices_a_checkpoint_edited_afterwards(project):
    cp.write(project, cp.Checkpoint(objective="Fix the race."))
    project.state_file.write_text("# Current Objective\n\nSomething else.\n", encoding="utf-8")
    assert any("edited after its checkpoint" in f.message for f in integrity.inspect(project))


def test_inspect_notices_an_unstructured_checkpoint(project):
    project.state_file.write_text("just some prose\n", encoding="utf-8")
    assert any("no recognisable sections" in f.message for f in integrity.inspect(project))


def test_inspect_notices_an_unfinished_handoff(project):
    task = project.read_task()
    task.status = "handoff_prepared"
    project.write_task(task)
    assert any("mid-handoff" in f.message for f in integrity.inspect(project))


def test_cli_verify_reports_errors_with_a_nonzero_exit(project, capsys):
    project.handoff_file.write_text("# HANDOFF\n\ntruncated\n", encoding="utf-8")
    assert main(["verify"]) == 1
    assert "error:" in capsys.readouterr().out


def test_cli_verify_is_quiet_when_all_is_well(project, capsys):
    handoff.write_package(project)
    assert main(["verify"]) == 0
    assert "ok:" in capsys.readouterr().out


def test_cli_verify_warnings_do_not_fail(project, capsys):
    (project.dir / "git.json.tmp").write_text("x", encoding="utf-8")
    assert main(["verify"]) == 0
    assert "warning:" in capsys.readouterr().out


def test_cli_recover_cleans_up(project, capsys):
    (project.dir / "state.md.tmp").write_text("x", encoding="utf-8")
    assert main(["recover"]) == 0
    assert "remove leftover" in capsys.readouterr().out
    assert not list(project.dir.glob("*.tmp"))


def test_cli_recover_on_a_clean_store(project, capsys):
    assert main(["recover"]) == 0
    assert "nothing to recover" in capsys.readouterr().out


def test_a_freshly_written_checkpoint_is_not_reported_as_edited(project):
    cp.write(project, cp.Checkpoint(objective="Fix the race.", next_step="Move persistence."))
    assert not [f for f in integrity.inspect(project) if "edited after" in f.message]


def test_the_per_user_directory_is_not_mistaken_for_a_project(tmp_path, monkeypatch):
    """`~/.agent-handoff/` holds health, and sits above every path under home.

    Identifying a store by the directory alone made every directory under the
    home directory resolve to the home directory as its project root.
    """
    from agent_handoff.store import find_root

    fake_home = tmp_path / "home"
    (fake_home / ".agent-handoff").mkdir(parents=True)
    (fake_home / ".agent-handoff" / "health.json").write_text("{}", encoding="utf-8")
    nested = fake_home / "code" / "some-project" / "src"
    nested.mkdir(parents=True)

    assert find_root(nested) is None

    # A real store below it is still found.
    project = fake_home / "code" / "some-project"
    store = Store(project)
    store.init(Task(original_request="x", source_agent="codex", fallback_agent="claude-code"))
    assert find_root(nested) == project.resolve()


def test_switch_does_not_hold_the_lock_while_the_new_agent_works(project, monkeypatch):
    """The agent that just took over must be able to checkpoint.

    `switch` waits for the agent it launched. Holding the write lock across
    that wait blocks the very `handoff checkpoint` calls the new agent is
    asked to make -- which is exactly what happened the first time this was
    used against a real repository.
    """
    from agent_handoff.adapters.base import AgentAdapter

    marker = project.root / "result.txt"
    # Stands in for `handoff checkpoint`: take the write lock, briefly.
    child = "\n".join(
        [
            "import os, sys, time, pathlib",
            "lock, marker = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])",
            "deadline = time.monotonic() + 5",
            "while True:",
            "    try:",
            "        os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))",
            "        os.unlink(str(lock))",
            "        marker.write_text('free')",
            "        break",
            "    except FileExistsError:",
            "        if time.monotonic() > deadline:",
            "            marker.write_text('blocked')",
            "            break",
            "        time.sleep(0.02)",
        ]
    )

    def fake_start(self, prompt, *, capture_output=False):
        return subprocess.Popen(
            [sys.executable, "-c", child, str(project.lock_file), str(marker)]
        )

    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(AgentAdapter, "start", fake_start)

    assert main(["switch", "--to", "claude"]) == 0
    assert marker.read_text(encoding="utf-8") == "free"
    assert not project.lock_file.exists()
    assert not project.run_lock_file.exists()


def test_a_briefly_busy_lock_is_waited_for_not_refused(project):
    """Two short writes that overlap should queue, not fail."""
    import threading

    from agent_handoff.cli import LOCK_WAIT_SECONDS

    assert LOCK_WAIT_SECONDS > 0
    holder = lock_mod.Lock(project.lock_file, "brief-writer")
    holder.acquire()
    threading.Timer(0.3, holder.release).start()
    assert main(["event", "file_modified", "path=auth.py"]) == 0


def test_a_lock_held_for_a_long_time_is_still_reported(project, capsys):
    """Waiting must not turn into hanging on a session-length lock."""
    import agent_handoff.cli as cli_mod

    monkey = lock_mod.Lock(project.lock_file, "a-long-session")
    monkey.acquire()
    try:
        original = cli_mod.LOCK_WAIT_SECONDS
        cli_mod.LOCK_WAIT_SECONDS = 0.2
        try:
            assert main(["event", "file_modified", "path=auth.py"]) == 1
        finally:
            cli_mod.LOCK_WAIT_SECONDS = original
    finally:
        monkey.release()
    assert "locked by pid" in capsys.readouterr().out
