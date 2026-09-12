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
