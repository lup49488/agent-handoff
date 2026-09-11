import shutil
import subprocess
import sys

import pytest

from benchmarks.fixtures.task_c import CLI_NEW, HELPER, REPORT_NEW, materialize


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_c_snapshots_are_incomplete(tmp_path, snapshot):
    project = materialize(tmp_path, snapshot)
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=project).returncode != 0
    assert (tmp_path / "handoff-context.md").is_file()


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_c_acceptance_allows_a_correct_completion(tmp_path, snapshot):
    project = materialize(tmp_path / "source", snapshot)
    completed = tmp_path / "completed"
    shutil.copytree(project, completed)
    (completed / "normalization.py").write_text(HELPER, encoding="utf-8")
    (completed / "report.py").write_text(REPORT_NEW, encoding="utf-8")
    (completed / "cli.py").write_text(CLI_NEW, encoding="utf-8")
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=completed).returncode == 0
