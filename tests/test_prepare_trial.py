import subprocess
import sys

import pytest

from agent_handoff.integrity import errors, verify_package
from agent_handoff.store import Store
from benchmarks.prepare_trial import prepare


@pytest.mark.parametrize("task", ("A", "B", "C"))
@pytest.mark.parametrize("arm", ("baseline", "handoff"))
def test_prepare_creates_equivalent_incomplete_workspace(tmp_path, task, arm):
    trial = prepare(task, "60", arm, tmp_path / arm)

    assert trial.prompt.is_file()
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=trial.project).returncode != 0
    assert not (trial.root / "handoff-context.md").exists()

    store = Store(trial.project)
    if arm == "baseline":
        assert not store.exists
        assert not store.handoff_file.exists()
    else:
        assert errors(verify_package(store)) == []
        package = store.handoff_file.read_text(encoding="utf-8")
        assert "benchmark_preparation" in package
        assert "no target agent has been launched" in package


def test_prepare_refuses_an_existing_destination(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="destination already exists"):
        prepare("A", "30", "baseline", destination)
@pytest.mark.parametrize("task", ("A", "B", "C"))
def test_prepare_arms_differ_only_by_the_handoff_package(tmp_path, task):
    baseline = prepare(task, "60", "baseline", tmp_path / "baseline")
    handoff = prepare(task, "60", "handoff", tmp_path / "handoff")

    def visible_files(project):
        return sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("*")
            if path.is_file() and ".agent-handoff" not in path.parts and path.name != "HANDOFF.md"
        )

    assert visible_files(baseline.project) == visible_files(handoff.project)
