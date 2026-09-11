import json
import pytest
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
