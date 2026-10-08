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


# -- progress that is its own observation ------------------------------------


def cell(rows, task="A", snapshot="30", arm="baseline"):
    return next(r for r in rows if (r["task"], r["snapshot"], r["arm"]) == (task, snapshot, arm))


def test_progress_seen_in_the_completion_round_is_not_reported_as_progress():
    """v0.3 B/60: 20.079 s against 20.094 s is one probe round, not two events."""
    same_round = trial(first_verified_progress_seconds=20.079, completion_seconds=20.094, agent_exit_seconds=30)
    row = cell(summarize([same_round]))

    assert row["progress_separable"] == 0
    assert row["median_first_progress_seconds"] is None
    assert row["median_completion_seconds"] == 20.094


def test_progress_passing_at_launch_is_not_progress_the_target_made():
    row = cell(summarize([trial(first_verified_progress_seconds=0, completion_seconds=40, agent_exit_seconds=50,
                                initial_checks={"progress_passed": True, "completion_passed": False})]))

    assert row["progress_separable"] == 0
    assert row["median_first_progress_seconds"] is None


def test_progress_in_an_earlier_round_is_reported():
    row = cell(summarize([trial(first_verified_progress_seconds=10, completion_seconds=20)]))

    assert row["progress_separable"] == 1
    assert row["median_first_progress_seconds"] == 10


def test_a_v4_record_states_separability_directly():
    record = trial(schema_version=4, isolation={"workspace": "neutral-temporary", "agent_args": []},
                   contamination=[], progress_separable=False,
                   first_verified_progress_seconds=10, completion_seconds=20)

    assert cell(summarize([record]))["progress_separable"] == 0


# -- exclusions ----------------------------------------------------------------


def test_an_excluded_trial_is_counted_as_excluded_and_nothing_else():
    """So a summary and a report that set aside the same trial agree on the cell."""
    kept = trial(trial_id="A-30-baseline-r02")
    dropped = trial(trial_id="A-30-baseline-r01", completed=False, completion_seconds=None,
                    first_verified_progress_seconds=None, budget_exceeded=True)
    row = cell(summarize([kept, dropped], exclusions={"A-30-baseline-r01": "provider outage"}))

    assert row["excluded"] == 1
    assert row["accepted"] == 1
    assert row["completion_rate"] == 1.0


def test_exclusions_need_a_reason(tmp_path):
    path = tmp_path / "exclusions.json"
    path.write_text('{"A-30-baseline-r01": ""}', encoding="utf-8")
    records = tmp_path / "trial.json"
    records.write_text(__import__("json").dumps(trial()), encoding="utf-8")

    with pytest.raises(SystemExit):
        main([str(records), "--exclusions", str(path)])


def test_times_are_printed_to_the_millisecond(tmp_path, capsys):
    """A median of 30.141 and 45.093 printed as 37.617000000000004."""
    a = trial(trial_id="A-30-baseline-r01", completion_seconds=30.141, first_verified_progress_seconds=10, agent_exit_seconds=60)
    b = trial(trial_id="A-30-baseline-r02", completion_seconds=45.093, first_verified_progress_seconds=10, agent_exit_seconds=60)
    for record in (a, b):
        (tmp_path / (record["trial_id"] + ".json")).write_text(__import__("json").dumps(record), encoding="utf-8")

    main([str(tmp_path)])
    out = capsys.readouterr().out

    assert "37.617" in out
    assert "37.617000000000004" not in out
