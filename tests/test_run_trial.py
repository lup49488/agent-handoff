import json
import subprocess
import sys

import pytest

from benchmarks.prepare_trial import ROOT
from benchmarks.plan import write_cohort_plan
from benchmarks.run_trial import _claim_cohort_trial, _finish_cohort_trial, run_trial
from benchmarks.validate_trial import validate


def test_dry_run_records_valid_unlaunched_trial(tmp_path):
    record = run_trial("A", "60", "handoff", "codex", 1, tmp_path / "trial")

    assert record["invalid_reason"] == "not_launched"
    assert validate(record) == []
    assert json.loads((tmp_path / "trial" / "trial.json").read_text(encoding="utf-8")) == record
    assert (tmp_path / "trial" / "project" / "HANDOFF.md").is_file()


def test_launch_requires_pinned_metadata(tmp_path):
    with pytest.raises(ValueError, match="requires --model"):
        run_trial("A", "30", "baseline", "claude-code", 1, tmp_path / "trial", launch=True)


def test_telemetry_the_harness_cannot_observe_is_left_null(tmp_path):
    """The design forbids estimating tokens or turns; null says so honestly."""
    record = run_trial("B", "80", "baseline", "codex", 2, tmp_path / "trial")

    assert record["target_turns"] is None
    assert record["provider_tokens"] is None


def test_preparing_a_trial_keeps_the_package_out_of_the_task_repository(tmp_path):
    """If the package were tracked, the arms' Git histories would differ."""
    run_trial("C", "60", "handoff", "codex", 1, tmp_path / "trial")
    project = tmp_path / "trial" / "project"

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout

    assert "agent-handoff" not in tracked and "HANDOFF.md" not in tracked
    assert "agent-handoff" not in status and "HANDOFF.md" not in status


def test_the_documented_runner_command_runs_as_a_script(tmp_path):
    result = subprocess.run(
        [sys.executable, "benchmarks/run_trial.py", "A", "60", "baseline", "codex", "1",
         str(tmp_path / "rt")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "not_launched" in result.stdout


def test_v02_records_fixture_environment_and_planned_order(tmp_path):
    record = run_trial("A", "60", "baseline", "codex", 1, tmp_path / "trial", plan_seed=17)

    assert record["fixture"]["task"] == "A"
    assert len(record["fixture"]["snapshot_sha256"]) == 64
    assert record["environment"]["python"]
    assert record["schedule"]["planned_arm_order"] == ["baseline", "handoff"] or record["schedule"]["planned_arm_order"] == ["handoff", "baseline"]
    assert record["schedule"]["planned_position"] in (1, 2)


def test_v02_uses_fixture_evaluator_and_audits_scope(tmp_path):
    from benchmarks.run_trial import _evaluate, _git_head, _scope_violations

    record = run_trial("A", "60", "baseline", "codex", 1, tmp_path / "trial")
    project = tmp_path / "trial" / "project"
    # A target may change this in-worktree helper, but the evaluator source is
    # supplied by the fixture and must still observe the incomplete slug.py.
    baseline_head = _git_head(project)
    (project / "acceptance.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    passed, _, _ = _evaluate(project, "A", "60")
    assert not passed
    assert _scope_violations(project, "A", baseline_head) == ["acceptance.py"]
    subprocess.run(["git", "add", "acceptance.py"], cwd=str(project), check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bad evaluator edit"],
        cwd=str(project), check=True,
    )
    assert _scope_violations(project, "A", baseline_head) == ["acceptance.py"]
    (project / "unrelated.txt").write_text("out of scope\n", encoding="utf-8")
    assert _scope_violations(project, "A", baseline_head) == ["acceptance.py", "unrelated.txt"]


def test_cohort_plan_claims_only_the_next_pending_trial(tmp_path):
    path = tmp_path / "plan.json"
    data = write_cohort_plan(path, 17, 1, ("A",))
    first, second = data["rows"][:2]

    claimed = _claim_cohort_trial(path, first["trial_id"])
    assert claimed["trial_id"] == first["trial_id"]
    with pytest.raises(ValueError, match="unresolved running"):
        _claim_cohort_trial(path, second["trial_id"])
    _finish_cohort_trial(path, first["trial_id"])
    assert _claim_cohort_trial(path, second["trial_id"])["trial_id"] == second["trial_id"]


def test_cohort_plan_requires_a_real_launch(tmp_path):
    path = tmp_path / "plan.json"
    write_cohort_plan(path, 17, 1, ("A",))

    with pytest.raises(ValueError, match="requires --launch"):
        run_trial("A", "30", "baseline", "codex", 1, tmp_path / "trial", cohort_plan=path)


def test_launched_trial_records_its_cohort_claim(tmp_path, monkeypatch):
    import benchmarks.run_trial as runner

    plan_path = tmp_path / "plan.json"
    plan_data = write_cohort_plan(plan_path, 17, 1, ("A",))
    row = plan_data["rows"][0]

    def fake_launch(*args):
        return {
            "accepted": True,
            "completed": False,
            "budget_exceeded": False,
            "first_verified_progress_seconds": None,
            "completion_seconds": 1.0,
            "agent_exit_seconds": 1.0,
            "target_exit_code": 0,
            "scope_violations": [],
            "invalid_reason": None,
        }

    monkeypatch.setattr(runner, "_launch", fake_launch)
    record = runner.run_trial(
        row["task"], row["snapshot"], row["arm"], "codex", row["replicate"], tmp_path / "trial",
        model="test-model", target_version="test-version", launch=True, cohort_plan=plan_path,
    )

    assert record["schema_version"] == 2
    assert record["schedule"]["cohort_position"] == row["position"]
    assert json.loads(plan_path.read_text(encoding="utf-8"))["rows"][0]["state"] == "recorded"


# -- the pre-registered edit surface ----------------------------------------


@pytest.mark.parametrize("task", ("A", "B", "C"))
def test_every_file_a_fixture_changes_is_inside_its_edit_surface(task):
    """The invariant that task B broke, derived rather than hand-listed.

    If a snapshot's own interrupted progress touches a file, a correct
    solution may touch it too. A surface narrower than that disqualifies
    trials before a target runs.
    """
    from benchmarks.prepare_trial import SNAPSHOTS, snapshot_files
    from benchmarks.run_trial import ALLOWED_PATHS

    base = snapshot_files(task, "30")
    touched = set()
    for snapshot in SNAPSHOTS:
        files = snapshot_files(task, snapshot)
        touched |= {name for name in files if files[name] != base.get(name)}
        touched |= set(base) - set(files)

    assert touched <= ALLOWED_PATHS[task]


@pytest.mark.parametrize("task", ("A", "B", "C"))
@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_no_trial_is_born_with_a_scope_violation(tmp_path, task, snapshot):
    from benchmarks.run_trial import _git_head, _scope_violations

    record = run_trial(task, snapshot, "baseline", "codex", 1, tmp_path / "trial")
    project = tmp_path / "trial" / "project"

    assert _scope_violations(project, task, record["fixture"]["initial_head"]) == []


def test_task_b_solved_in_the_service_layer_completes(tmp_path):
    """The package tells the target to sort in the service; doing so must pass."""
    from benchmarks.run_trial import _evaluate, _git_head, _scope_violations

    run_trial("B", "80", "handoff", "codex", 1, tmp_path / "trial")
    project = tmp_path / "trial" / "project"
    head = _git_head(project)
    service = project / "service.py"
    service.write_text(
        service.read_text(encoding="utf-8").replace(
            "    return [\n        task for task in TASKS\n",
            "    return sorted([\n        task for task in TASKS\n",
        ).replace(
            "(owner is None or task[\"owner\"] == owner)\n    ]",
            "(owner is None or task[\"owner\"] == owner)\n    ], key=lambda task: task[\"created_at\"])",
        ),
        encoding="utf-8",
    )

    assert _evaluate(project, "B", "80")[0]
    assert _scope_violations(project, "B", head) == []


# -- a cohort plan survives the runner dying ---------------------------------


def test_a_launch_that_raises_returns_its_row_to_pending(tmp_path, monkeypatch):
    import benchmarks.run_trial as runner

    plan_path = tmp_path / "plan.json"
    row = write_cohort_plan(plan_path, 17, 1, ("A",))["rows"][0]

    def broken_launch(*args):
        raise RuntimeError("target harness failed")

    monkeypatch.setattr(runner, "_launch", broken_launch)
    with pytest.raises(RuntimeError):
        runner.run_trial(
            row["task"], row["snapshot"], row["arm"], "codex", row["replicate"], tmp_path / "trial",
            model="m", target_version="v", launch=True, cohort_plan=plan_path,
        )

    assert json.loads(plan_path.read_text(encoding="utf-8"))["rows"][0]["state"] == "pending"


def test_an_invalid_trial_ends_its_row_as_invalid_and_the_cell_is_reported_short(tmp_path, monkeypatch):
    import benchmarks.run_trial as runner
    from benchmarks.plan import status

    plan_path = tmp_path / "plan.json"
    row = write_cohort_plan(plan_path, 17, 1, ("A",))["rows"][0]
    monkeypatch.setattr(runner, "_launch", lambda *a: {"invalid_reason": "target_not_started"})

    runner.run_trial(
        row["task"], row["snapshot"], row["arm"], "codex", row["replicate"], tmp_path / "trial",
        model="m", target_version="v", launch=True, cohort_plan=plan_path,
    )

    assert json.loads(plan_path.read_text(encoding="utf-8"))["rows"][0]["state"] == "invalid"
    assert status(plan_path)["short_cells"] == ["%s-%s-%s" % (row["task"], row["snapshot"], row["arm"])]


def _abandon(plan_path, pid):
    """Leave the first row `running`, claimed by `pid` — a runner that died."""
    from benchmarks.plan import claim_trial

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    claim_trial(plan_path, data["rows"][0]["trial_id"])
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    data["rows"][0]["claimed_by"]["pid"] = pid
    plan_path.write_text(json.dumps(data), encoding="utf-8")
    return data["rows"]


def test_a_row_abandoned_by_a_dead_runner_can_be_released(tmp_path):
    """The wedge: a killed runner left `running` with no way out but hand-editing."""
    from benchmarks.plan import claim_trial, release_trial

    plan_path = tmp_path / "plan.json"
    write_cohort_plan(plan_path, 17, 1, ("A",))
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    rows = _abandon(plan_path, dead.pid)

    with pytest.raises(ValueError, match="no longer running.*--release"):
        claim_trial(plan_path, rows[1]["trial_id"])

    release_trial(plan_path, rows[0]["trial_id"])
    assert claim_trial(plan_path, rows[0]["trial_id"])["trial_id"] == rows[0]["trial_id"]


def test_a_row_held_by_a_live_runner_is_not_released(tmp_path):
    """Releasing a trial that is still running would let it be claimed twice."""
    from benchmarks.plan import release_trial

    plan_path = tmp_path / "plan.json"
    write_cohort_plan(plan_path, 17, 1, ("A",))
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rows = _abandon(plan_path, alive.pid)
        with pytest.raises(ValueError, match="still held"):
            release_trial(plan_path, rows[0]["trial_id"])
    finally:
        alive.kill()
        alive.wait()


def test_the_release_command_runs_as_documented(tmp_path):
    plan_path = tmp_path / "plan.json"
    write_cohort_plan(plan_path, 17, 1, ("A",))
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    rows = _abandon(plan_path, dead.pid)

    result = subprocess.run(
        [sys.executable, "benchmarks/plan.py", "--plan", str(plan_path), "--release", rows[0]["trial_id"]],
        cwd=str(ROOT), capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "released " + rows[0]["trial_id"] in result.stdout
    assert "pending" in result.stdout
