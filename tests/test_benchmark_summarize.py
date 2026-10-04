import copy

import pytest

from benchmarks.summarize import main, summarize
from tests.test_benchmark_trials import VALID


def trial(arm="baseline", task="A", snapshot="30", model="model-one", **overrides):
    record = copy.deepcopy(VALID)
    record.update({
        "schema_version": 3,
        "trial_id": "%s-%s-%s-r01" % (task, snapshot, arm),
        "task": task,
        "snapshot": snapshot,
        "arm": arm,
        "fixture": {
            "snapshot_sha256": "a" * 64,
            "evaluator_sha256": "b" * 64,
            "progress_evaluator_sha256": "c" * 64,
            "initial_head": "abc123",
        },
        "environment": {"python": "3.x", "platform": "test"},
        "started_at": "2026-10-03T00:00:00+00:00",
        "scope_violations": [],
        "initial_checks": {"progress_passed": False, "completion_passed": False},
        "target": {"agent": "codex", "version": "1.0", "model": model, "effort": "medium"},
        "first_verified_progress_seconds": 10,
        "completion_seconds": 20,
        "agent_exit_seconds": 21,
    })
    record.update(overrides)
    return record


def test_summary_keeps_task_snapshot_arm_and_model_cohorts_separate():
    rows = summarize([
        trial(),
        trial(arm="handoff"),
        trial(snapshot="60"),
        trial(model="model-two"),
    ], expected_replicates=2)

    populated = [row for row in rows if row["trials_seen"]]
    assert len(rows) == 36
    assert len(populated) == 4
    assert {row["snapshot"] for row in populated} == {"30", "60"}
    assert {row["arm"] for row in populated} == {"baseline", "handoff"}
    assert {row["model"] for row in populated} == {"model-one", "model-two"}
    assert all(row["short_by"] == 1 for row in populated)
    assert sum(row["short_by"] == 2 for row in rows if not row["trials_seen"]) == 32


def test_summary_reports_invalid_shortfall_and_completion_medians():
    invalid = trial(trial_id="A-30-handoff-r02", accepted=False, completed=False,
                    invalid_reason="target_not_started", first_verified_progress_seconds=None,
                    completion_seconds=None, agent_exit_seconds=None)
    incomplete = trial(trial_id="A-30-handoff-r03", completed=False, completion_seconds=None)

    row = next(row for row in summarize([trial(), incomplete, invalid], expected_replicates=3)
               if row["task"] == "A" and row["snapshot"] == "30" and row["arm"] == "baseline")

    assert row["accepted"] == 2
    assert row["invalid"] == 1
    assert row["completed"] == 1
    assert row["completion_rate"] == 0.5
    assert row["median_completion_seconds"] == 20
    assert row["short_by"] == 1


def test_summary_rejects_v2_records_and_invalid_schema_v3_records():
    legacy = copy.deepcopy(VALID)
    with pytest.raises(ValueError, match="schema_version 3"):
        summarize([legacy])

    malformed = trial()
    del malformed["fixture"]["progress_evaluator_sha256"]
    with pytest.raises(ValueError, match="progress_evaluator_sha256"):
        summarize([malformed])


def test_summary_rejects_duplicate_trial_ids_within_a_target_cohort():
    with pytest.raises(ValueError, match="duplicate trial_id"):
        summarize([trial(), trial()])


def test_empty_summary_directory_prints_headers(tmp_path, capsys):
    assert main([str(tmp_path)]) == 0
    assert "completion_rate" in capsys.readouterr().out
