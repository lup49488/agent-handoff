import copy

from benchmarks.validate_trial import main, validate


VALID = {
    "trial_id": "B-60-handoff-r03",
    "task": "B",
    "snapshot": "60",
    "arm": "handoff",
    "replicate": 3,
    "source_commit": "abc123",
    "handoff_version": "0.9.2",
    "target": {"agent": "claude-code", "version": "x", "model": "x"},
    "budget": {"wall_seconds": 1800, "target_turns": 20},
    "accepted": True,
    "completed": True,
    "budget_exceeded": False,
    "first_verified_progress_seconds": 242,
    "completion_seconds": 611,
    "target_turns": 7,
    "provider_tokens": None,
    "repeated_commands": 2,
    "repeated_file_reads": 4,
    "invalid_reason": None,
}


def test_valid_trial_is_accepted():
    assert validate(VALID) == []


def test_v2_trial_requires_evidence_fields():
    trial = copy.deepcopy(VALID)
    trial["schema_version"] = 2
    assert "missing fixture" in validate(trial)


def v3_trial(**overrides):
    trial = copy.deepcopy(VALID)
    trial.update({
        "schema_version": 3,
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
        "first_verified_progress_seconds": 242,
        "completion_seconds": 611,
        "agent_exit_seconds": 700,
    })
    trial.update(overrides)
    return trial


def test_v3_accepts_distinct_progress_and_completion_times():
    assert validate(v3_trial()) == []


def test_v3_rejects_completion_before_verified_progress():
    trial = v3_trial(completion_seconds=200)
    assert "first_verified_progress_seconds cannot exceed completion_seconds" in validate(trial)


def test_v3_incomplete_accepted_trial_can_have_no_completion_time():
    trial = v3_trial(completed=False, completion_seconds=None)
    assert validate(trial) == []


def test_v3_records_zero_when_progress_check_passes_at_launch():
    trial = v3_trial(initial_checks={"progress_passed": True, "completion_passed": False},
                     first_verified_progress_seconds=0)
    assert validate(trial) == []


def test_v3_rejects_completion_check_already_passing_at_launch():
    trial = v3_trial(initial_checks={"progress_passed": True, "completion_passed": True},
                     first_verified_progress_seconds=0)
    assert "accepted v3 trial cannot start with completion already passing" in validate(trial)


def test_completed_trial_cannot_be_rejected():
    trial = copy.deepcopy(VALID)
    trial["accepted"] = False
    assert "completed trial must be accepted" in validate(trial)


def test_a_trial_cannot_be_accepted_and_invalid_at_once():
    """Running past the budget is an outcome; being invalid is an exclusion.

    Recording both put a timed-out trial in the sample and out of it at the
    same time, which decides a completion rate by accident.
    """
    trial = copy.deepcopy(VALID)
    trial["invalid_reason"] = "wall_timeout"
    assert "accepted trial must not carry an invalid_reason" in validate(trial)


def test_a_trial_over_budget_stays_in_the_sample():
    trial = copy.deepcopy(VALID)
    trial.update(budget_exceeded=True, completed=False)
    assert validate(trial) == []


def test_an_excluded_trial_must_say_why():
    trial = copy.deepcopy(VALID)
    trial.update(accepted=False, completed=False, invalid_reason=None)
    assert "a trial that was not accepted needs an invalid_reason" in validate(trial)


def test_an_accepted_trial_must_be_timed():
    trial = copy.deepcopy(VALID)
    del trial["completion_seconds"]
    assert "accepted trial must record completion_seconds" in validate(trial)


def test_secret_markers_are_rejected():
    trial = copy.deepcopy(VALID)
    trial["target"]["note"] = "Authorization: Bearer secret"
    assert "record appears to contain a secret marker" in validate(trial)


def test_validator_has_a_non_error_help_path():
    assert main(["--help"]) == 0


def test_completed_trial_cannot_carry_scope_violations():
    trial = copy.deepcopy(VALID)
    trial["scope_violations"] = ["acceptance.py"]
    assert "completed trial must have no scope violations" in validate(trial)


def test_a_trial_cannot_complete_over_budget():
    """Completion is defined as passing *within* the stated budget."""
    trial = copy.deepcopy(VALID)
    trial["budget_exceeded"] = True
    assert "a trial over budget cannot be completed" in validate(trial)
