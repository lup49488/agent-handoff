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


def test_secret_markers_are_rejected():
    trial = copy.deepcopy(VALID)
    trial["target"]["note"] = "Authorization: Bearer secret"
    assert "record appears to contain a secret marker" in validate(trial)


def test_validator_has_a_non_error_help_path():
    assert main(["--help"]) == 0
