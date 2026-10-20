"""`handoff resume`: take over in one read-only step.

In the recovery pilot a Handoff recovery spent four to seven tool calls on
the package -- status, verify, HANDOFF.md, the diff -- before any work.
"""

import json
import subprocess

import pytest

from agent_handoff import checkpoint as cp
from agent_handoff import handoff
from agent_handoff import lock as lock_mod
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.store import Store


def git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(tmp_path, *args)
    (tmp_path / "report.py").write_text("def total():\n    return 1\n", encoding="utf-8")
    git(tmp_path, "add", "report.py")
    git(tmp_path, "commit", "-qm", "initial")
    store = Store(tmp_path)
    store.init(Task(original_request="fix the totals", source_agent="codex", fallback_agent="claude-code"))
    (tmp_path / "report.py").write_text("def total():\n    return 2\n", encoding="utf-8")
    cp.write(store, cp.Checkpoint(objective="fix the totals", completed=["offset fixed; two days left"]))
    assert main(["tests", "python check_report.py", "--exit-code", "1", "--summary", "2 mismatches"]) == 0
    assert main(["pack"]) == 0
    return store


def snapshot(store):
    files = [store.handoff_file] + sorted(p for p in store.dir.rglob("*") if p.is_file())
    return {str(p): p.read_bytes() for p in files}


def test_resume_verifies_and_prints_the_package(repo, capsys):
    capsys.readouterr()
    assert main(["resume"]) == 0
    out = capsys.readouterr().out

    assert out.startswith("# Taking over")
    assert "handoff verify: ok" in out
    assert "offset fixed; two days left" in out
    assert "**Current:**" in out
    assert "+    return 2" in out
    assert "## Resume instructions" in out


def test_resume_describes_the_code_as_it_is_now_not_as_packed(repo, capsys):
    (repo.root / "report.py").write_text("def total():\n    return 3\n", encoding="utf-8")
    (repo.root / "dedupe.py").write_text("SEEN = set()\n", encoding="utf-8")
    capsys.readouterr()

    main(["resume"])
    out = capsys.readouterr().out

    assert "+    return 3" in out
    assert "dedupe.py" in out
    assert "**Stale:**" in out
    assert "taken on different code" in out  # from the integrity check


def test_resume_writes_nothing(repo):
    before = snapshot(repo)
    assert main(["resume"]) == 0
    assert snapshot(repo) == before


def test_resume_does_not_wait_for_the_write_lock(repo, capsys):
    holder = lock_mod.Lock(repo.lock_file, "a long checkpoint")
    holder.acquire()
    try:
        assert main(["resume"]) == 0
    finally:
        holder.release()


def test_resume_reports_damage_and_still_prints(repo, capsys):
    repo.handoff_file.write_text("not a package\n", encoding="utf-8")
    capsys.readouterr()

    code = main(["resume"])
    out = capsys.readouterr().out

    assert "error: HANDOFF.md does not look like a handoff package" in out
    assert "offset fixed; two days left" in out  # rendered from the state, not the damaged file
    assert code == 1


def test_the_package_tells_a_successor_to_resume(repo):
    text = repo.handoff_file.read_text(encoding="utf-8")
    assert "First run `handoff resume`" in text
    assert handoff.RESUME_INSTRUCTION.splitlines()[0] == (
        "You are continuing an unfinished coding task from another agent.")
