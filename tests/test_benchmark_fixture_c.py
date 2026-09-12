import subprocess
import sys

import pytest

from benchmarks.fixtures.task_c import CLI_NEW, HELPER, REPORT_NEW, context, snapshot_files


def write(project, files):
    project.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (project / name).write_text(text, encoding="utf-8")
    return project


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_c_snapshots_are_incomplete(tmp_path, snapshot):
    project = write(tmp_path / "project", snapshot_files(snapshot))

    assert subprocess.run([sys.executable, "acceptance.py"], cwd=project).returncode != 0
    assert context(snapshot).startswith("## Next step")


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_c_acceptance_allows_a_correct_completion(tmp_path, snapshot):
    completed = write(tmp_path / "completed", snapshot_files(snapshot))
    for name, text in (
        ("normalization.py", HELPER),
        ("report.py", REPORT_NEW),
        ("cli.py", CLI_NEW),
    ):
        (completed / name).write_text(text, encoding="utf-8")

    assert subprocess.run([sys.executable, "acceptance.py"], cwd=completed).returncode == 0
