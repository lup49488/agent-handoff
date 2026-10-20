"""A recorded test result says whether it still describes the code.

On the recovery pilot's task E v3 a successor re-ran a ten-second check to
learn what the package already said, because nothing told it whether the
recorded result was taken on the code in front of it.
"""

import json
import subprocess

import pytest

from agent_handoff import checkpoint as cp
from agent_handoff import handoff, integrity, testresult
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
    return store


def record(store, exit_code="1", summary="2 mismatches"):
    assert main(["tests", "python check_report.py", "--exit-code", exit_code, "--summary", summary]) == 0
    return json.loads(store.tests_file.read_text(encoding="utf-8"))


def state(store):
    return testresult.freshness(store.root, json.loads(store.tests_file.read_text(encoding="utf-8")))[0]


def test_a_result_records_the_code_it_ran_on(repo):
    tree = record(repo)["tree"]

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo.root), capture_output=True,
                          text=True, check=True).stdout.strip()
    assert tree["head"] == head
    assert len(tree["changes"]) == 64


def test_a_result_on_unchanged_code_is_current(repo, capsys):
    record(repo)

    assert state(repo) == testresult.CURRENT
    assert "**Current:**" in handoff.render(repo)
    main(["status"])
    assert "tests    : FAILED  python check_report.py  (2 mismatches)  [current]" in capsys.readouterr().out


def test_handoff_bookkeeping_does_not_make_a_result_stale(repo):
    """Checkpointing and packing write files; they are not the task's code."""
    record(repo)
    cp.write(repo, cp.Checkpoint(objective="fix the totals", completed=["two days left"]))
    handoff.write_package(repo)

    assert state(repo) == testresult.CURRENT


def test_an_edit_after_the_result_makes_it_stale(repo, capsys):
    record(repo)
    (repo.root / "report.py").write_text("def total():\n    return 3\n", encoding="utf-8")

    assert state(repo) == testresult.STALE
    package = handoff.render(repo)
    assert "**Stale:** the uncommitted changes differ" in package
    main(["status"])
    assert "[stale: the code has changed since]" in capsys.readouterr().out
    assert any("taken on different code" in f.message for f in integrity.inspect(repo))


def test_a_new_untracked_file_makes_it_stale(repo):
    record(repo)
    (repo.root / "dedupe.py").write_text("SEEN = set()\n", encoding="utf-8")

    assert state(repo) == testresult.STALE


def test_a_commit_after_the_result_makes_it_stale_and_says_so(repo):
    record(repo)
    git(repo.root, "add", "report.py")
    git(repo.root, "commit", "-qm", "fix")

    assert state(repo) == testresult.STALE
    assert "HEAD is now" in handoff.render(repo)


def test_a_current_result_raises_no_warning(repo):
    record(repo)

    assert not any("taken on different code" in f.message for f in integrity.inspect(repo))


def test_a_result_recorded_before_trees_were_kept_is_unknown_not_current(repo):
    """Records from earlier versions have no tree; they must not read as current."""
    old = record(repo)
    old.pop("tree")
    repo.tests_file.write_text(json.dumps(old), encoding="utf-8")

    assert state(repo) == testresult.UNKNOWN
    assert "re-run it before relying on it" in handoff.render(repo)
    assert not any("taken on different code" in f.message for f in integrity.inspect(repo))


def test_outside_a_repository_nothing_is_claimed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path)
    store.init(Task(original_request="t", source_agent="codex", fallback_agent="claude-code"))
    recorded = record(store)

    assert "tree" not in recorded
    assert state(store) == testresult.UNKNOWN
    main(["status"])
    out = capsys.readouterr().out
    assert "[current]" not in out and "[stale" not in out


def test_the_resume_instructions_say_a_current_result_need_not_be_re_run(repo):
    assert "re-run only the ones that\nare not" in handoff.RESUME_INSTRUCTION
