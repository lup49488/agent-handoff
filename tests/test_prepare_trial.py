import subprocess
import sys

import pytest

from agent_handoff.checkpoint import Checkpoint
from agent_handoff.integrity import errors, verify_package
from agent_handoff.store import Store
from benchmarks.prepare_trial import BASE_SNAPSHOT, ROOT, prepare, snapshot_files


def git(project, *args):
    return subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("task", ("A", "B", "C"))
@pytest.mark.parametrize("arm", ("baseline", "handoff"))
def test_prepare_creates_equivalent_incomplete_workspace(tmp_path, task, arm):
    trial = prepare(task, "60", arm, tmp_path / arm)

    assert trial.prompt.is_file()
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=trial.project).returncode != 0
    assert not (trial.root / "handoff-context.md").exists()

    store = Store(trial.project)
    if arm == "baseline":
        assert not store.exists
        assert not store.handoff_file.exists()
    else:
        assert errors(verify_package(store)) == []
        assert "manual_handoff" in store.handoff_file.read_text(encoding="utf-8")


def test_prepare_refuses_an_existing_destination(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="destination already exists"):
        prepare("A", "30", "baseline", destination)


@pytest.mark.parametrize("task", ("A", "B", "C"))
def test_prepare_arms_differ_only_by_the_handoff_package(tmp_path, task):
    baseline = prepare(task, "60", "baseline", tmp_path / "baseline")
    handoff = prepare(task, "60", "handoff", tmp_path / "handoff")

    def visible_files(project):
        hidden = {".agent-handoff", ".git"}
        return sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file()
            and not hidden & set(path.relative_to(project).parts)
            and path.name != "HANDOFF.md"
        )

    assert visible_files(baseline.project) == visible_files(handoff.project)


# -- the Baseline arm's Git history -----------------------------------------


@pytest.mark.parametrize("task", ("A", "B", "C"))
@pytest.mark.parametrize("snapshot", ("60", "80"))
def test_baseline_recovers_from_real_history_and_uncommitted_work(tmp_path, task, snapshot):
    """The design gives Baseline the worktree *and* Git history.

    Without its own repository the trial directory has neither: inside a
    checkout it reports the enclosing project's history, outside one it has
    none at all. Either decides the comparison before the target starts.
    """
    trial = prepare(task, snapshot, "baseline", tmp_path / "baseline")

    assert git(trial.project, "log", "--oneline").endswith("Project state before the task began")
    # The commit holds the pre-task state and nothing else, so the source
    # agent's interrupted progress is exactly what shows as uncommitted —
    # a modification for tasks A and B, a new file for C at 60%.
    assert git(trial.project, "ls-tree", "-r", "--name-only", "HEAD").split() == sorted(
        snapshot_files(task, BASE_SNAPSHOT)
    )
    assert git(trial.project, "status", "--porcelain") != ""


def test_the_base_snapshot_is_the_state_before_any_work(tmp_path):
    trial = prepare("A", BASE_SNAPSHOT, "baseline", tmp_path / "baseline")

    assert git(trial.project, "status", "--porcelain") == ""


def test_a_trial_inside_a_checkout_does_not_report_that_checkout(tmp_path):
    """The failure this guards: `git.json` describing the wrong repository."""
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(outer), check=True, capture_output=True)
    (outer / "unrelated.txt").write_text("not the fixture\n", encoding="utf-8")
    for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "outer"]):
        subprocess.run(["git", *args], cwd=str(outer), check=True, capture_output=True)
    outer_head = git(outer, "rev-parse", "HEAD")

    trial = prepare("A", "60", "handoff", outer / "trial")
    state = Store(trial.project).read_git()

    assert state.is_repo
    assert state.head not in outer_head
    assert state.modified == ["slug.py"]
    assert state.untracked == []
    assert "outer" not in "".join(state.recent_commits)


# -- the checkpoint the Handoff arm is measured on --------------------------


def test_the_checkpoint_files_progress_reasoning_and_next_step_separately(tmp_path):
    """Folding them into one blob understates the package being measured."""
    trial = prepare("A", "60", "handoff", tmp_path / "handoff")
    checkpoint = Checkpoint.parse(Store(trial.project).read_state_md())

    assert checkpoint.completed == [
        "Added standard-library Unicode normalization and separator collapse.",
        "`many---spaces` now passes.",
    ]
    assert checkpoint.decisions and "semantic substitutions" in checkpoint.decisions[0]
    assert checkpoint.next_step.startswith("Add the substitutions")
    assert "Source progress" not in checkpoint.next_step


@pytest.mark.parametrize("task", ("A", "B", "C"))
@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_every_snapshot_has_a_next_step_and_stays_in_budget(tmp_path, task, snapshot):
    trial = prepare(task, snapshot, "handoff", tmp_path / "handoff")
    checkpoint = Checkpoint.parse(Store(trial.project).read_state_md())

    assert checkpoint.next_step.strip()
    assert not checkpoint.over_budget()


def test_the_package_never_says_the_trial_is_an_experiment(tmp_path):
    """Telling the subject it is being measured is not a neutral prompt."""
    trial = prepare("A", "60", "handoff", tmp_path / "handoff")
    package = trial.project.joinpath("HANDOFF.md").read_text(encoding="utf-8").lower()

    for leak in ("benchmark", "instrumentation", "no target agent", "experiment", "trial"):
        assert leak not in package


# -- the documented command lines -------------------------------------------


@pytest.mark.parametrize("task", ("A", "C"))
def test_the_documented_command_runs_as_a_script(tmp_path, task):
    """Imported by pytest it always worked; run as documented it did not.

    pytest puts the project root on `sys.path` and the script did not, so
    every `benchmarks.*` import failed only for the user following the README.
    """
    result = subprocess.run(
        [sys.executable, "benchmarks/prepare_trial.py", task, "60", "handoff", str(tmp_path / task)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / task / "project" / "HANDOFF.md").is_file()
