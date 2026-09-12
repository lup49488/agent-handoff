import json
import subprocess
import sys

import pytest

from benchmarks.prepare_trial import ROOT
from benchmarks.run_trial import run_trial
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
