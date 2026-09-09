"""Phase 2 -- semantic checkpoints."""

import subprocess
from pathlib import Path

import pytest

from agent_handoff import checkpoint as cp
from agent_handoff import gitinfo, handoff
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.runner import initial_prompt
from agent_handoff.store import Store


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path)
    store.init(
        Task(
            original_request="Fix the authentication refresh bug",
            source_agent="codex",
            fallback_agent="claude-code",
        )
    )
    return store


@pytest.fixture
def repo(project):
    root = project.root
    for args in (
        ("init", "-q"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)
    (root / "auth.py").write_text("def refresh():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "auth.py"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=str(root), check=True, capture_output=True
    )
    return project


# -- the model --------------------------------------------------------------


def test_render_and_parse_round_trip():
    entry = cp.Checkpoint(
        objective="Fix refresh-token race condition.",
        completed=["Reproduced bug", "Added regression test"],
        decisions=["Keep current JWT library", "Do not change public API"],
        problem="Refresh lock does not cover persistence.",
        next_step="Move persistence into the lock and rerun auth tests.",
    )
    assert cp.Checkpoint.parse(entry.render()) == entry


def test_parse_tolerates_hand_edited_headings():
    entry = cp.Checkpoint.parse(
        "## Goal\nShip the fix.\n\n### done\n* Reproduced it\n\n# Next Steps:\nWrite the test.\n"
    )
    assert entry.objective == "Ship the fix."
    assert entry.completed == ["Reproduced it"]
    assert entry.next_step == "Write the test."


def test_parse_ignores_unknown_sections():
    entry = cp.Checkpoint.parse("# Random Notes\nnoise\n\n# Current Problem\nThe lock.\n")
    assert entry.problem == "The lock."
    assert entry.completed == []


def test_empty_checkpoint_renders_nothing():
    assert cp.Checkpoint().render() == ""
    assert cp.Checkpoint().is_empty()


def test_budget_flags_a_conversation_summary():
    assert not cp.Checkpoint(objective="Fix the race.").over_budget()
    bloated = cp.Checkpoint(completed=["step " + str(i) + " " + "x" * 40 for i in range(40)])
    assert bloated.over_budget()


# -- update semantics -------------------------------------------------------


def test_update_patches_and_accumulates(project):
    cp.write(project, cp.update(project, objective="Fix the race.", done=["Reproduced bug"]))
    entry = cp.update(project, done=["Added test"], problem="Lock too narrow.")
    assert entry.objective == "Fix the race."  # untouched section survives
    assert entry.completed == ["Reproduced bug", "Added test"]  # history accumulates
    assert entry.problem == "Lock too narrow."


def test_update_does_not_duplicate_completed_items(project):
    cp.write(project, cp.update(project, done=["Reproduced bug"]))
    assert cp.update(project, done=["Reproduced bug"]).completed == ["Reproduced bug"]


def test_replace_starts_from_empty(project):
    cp.write(project, cp.update(project, objective="Old goal.", done=["Old step"]))
    entry = cp.update(project, objective="New goal.", replace=True)
    assert entry.objective == "New goal."
    assert entry.completed == []


def test_write_records_when_it_was_taken(project):
    cp.write(project, cp.Checkpoint(objective="Fix the race."), git_head="abc1234")
    record = cp.last_record(project)
    assert record["git_head"] == "abc1234"
    assert record["tokens"] > 0
    assert any(
        e["event"] == "checkpoint_written" for e in project.iter_jsonl(project.events_file)
    )


# -- staleness --------------------------------------------------------------


def test_staleness_is_absent_without_a_checkpoint(project):
    assert cp.staleness(project) is None


def test_staleness_counts_events_recorded_since(project):
    from agent_handoff.events import log_event

    cp.write(project, cp.Checkpoint(objective="Fix the race."), git_head="abc1234")
    log_event(project, "file_modified", path="auth.py")
    log_event(project, "file_modified", path="test_auth.py")
    line = cp.staleness(project, git_head="abc1234")
    assert "2 event(s) recorded since" in line
    assert "HEAD has moved" not in line


def test_staleness_flags_a_moved_head(project):
    cp.write(project, cp.Checkpoint(objective="Fix the race."), git_head="abc1234")
    line = cp.staleness(project, git_head="def5678")
    assert "HEAD has moved to `def5678`" in line
    assert "trust the diff over the prose" in line


# -- the handoff package ----------------------------------------------------


def test_package_carries_the_checkpoint_with_its_age(repo):
    store = repo
    store.write_git(gitinfo.collect(store.root))
    cp.write(
        store,
        cp.Checkpoint(
            objective="Fix refresh-token race condition.",
            decisions=["Keep current JWT library"],
            next_step="Move persistence into the lock.",
        ),
        git_head="oldhead",
    )
    text = handoff.render(store)
    assert "Keep current JWT library" in text
    assert "HEAD has moved" in text  # the real head is not "oldhead"
    assert "### Current Objective" in text  # still nested under the h2 section


def test_resume_instruction_keeps_the_chain_alive(repo):
    text = handoff.render(repo)
    assert "the diff is the truth" in text
    assert "handoff checkpoint" in text


def test_initial_prompt_carries_the_protocol():
    prompt = initial_prompt("Fix the auth bug")
    assert prompt.startswith("Fix the auth bug")
    assert "handoff checkpoint" in prompt
    assert initial_prompt("Fix the auth bug", with_protocol=False) == "Fix the auth bug"


# -- CLI --------------------------------------------------------------------


def test_cli_checkpoint_writes_state_md(repo):
    assert (
        main(
            [
                "checkpoint",
                "--objective",
                "Fix refresh-token race condition.",
                "--done",
                "Reproduced bug",
                "--decision",
                "Keep current JWT library",
                "--next",
                "Move persistence into the lock.",
            ]
        )
        == 0
    )
    text = repo.state_file.read_text(encoding="utf-8")
    assert "# Current Objective" in text
    assert "- Reproduced bug" in text
    assert cp.last_record(repo)["git_head"]  # captured the real HEAD


def test_cli_checkpoint_rejects_an_empty_update(repo, capsys):
    assert main(["checkpoint"]) == 2
    assert "nothing to record" in capsys.readouterr().out
    assert not repo.state_file.exists()


def test_cli_checkpoint_show(repo, capsys):
    main(["checkpoint", "--objective", "Fix the race."])
    assert main(["checkpoint", "--show"]) == 0
    assert "Fix the race." in capsys.readouterr().out


def test_cli_checkpoint_from_file(repo, tmp_path, capsys):
    source = tmp_path / "note.md"
    source.write_text("# Current Problem\nThe lock is too narrow.\n", encoding="utf-8")
    assert main(["checkpoint", "--from-file", str(source)]) == 0
    assert "The lock is too narrow." in repo.state_file.read_text(encoding="utf-8")


def test_cli_checkpoint_from_file_rejects_unstructured_text(repo, tmp_path, capsys):
    source = tmp_path / "note.md"
    source.write_text("just some prose with no headings\n", encoding="utf-8")
    assert main(["checkpoint", "--from-file", str(source)]) == 2
    assert "nothing recognisable" in capsys.readouterr().out


def test_cli_warns_when_over_budget(repo, capsys):
    args = ["checkpoint"]
    for i in range(40):
        args += ["--done", "step " + str(i) + " " + "x" * 40]
    assert main(args) == 0
    assert "over the 300-token guideline" in capsys.readouterr().out


def test_cli_protocol_prints_the_instruction(capsys):
    assert main(["protocol"]) == 0
    out = capsys.readouterr().out
    assert "handoff checkpoint" in out
    assert "not a summary of our conversation" in out


def test_cli_status_reports_checkpoint_age(repo, capsys):
    main(["checkpoint", "--objective", "Fix the race."])
    assert main(["status"]) == 0
    assert "checkpt  : recorded" in capsys.readouterr().out


def test_checkpoint_is_not_mistaken_for_an_agent_shorthand(repo):
    # `checkpoint` must reach its own subparser, not the `switch` rewrite.
    assert main(["checkpoint", "--objective", "Fix the race."]) == 0
