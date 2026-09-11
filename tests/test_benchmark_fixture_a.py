import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "fixtures" / "task-a"
COMPLETE = '''import re
import unicodedata


def slugify(title):
    if not isinstance(title, str):
        raise TypeError("title must be a string")
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("&", " and ").replace("#", " sharp ")
    return "-".join(re.findall(r"[a-z0-9]+", normalized.lower()))
'''


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_a_snapshots_are_incomplete_and_have_handoff_context(snapshot):
    project = ROOT / snapshot / "project"
    result = subprocess.run(
        [sys.executable, "acceptance.py"], cwd=project, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert (ROOT / snapshot / "handoff-context.md").is_file()


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_a_acceptance_allows_a_correct_completion(tmp_path, snapshot):
    project = tmp_path / "project"
    shutil.copytree(ROOT / snapshot / "project", project)
    (project / "slug.py").write_text(COMPLETE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "acceptance.py"], cwd=project, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
