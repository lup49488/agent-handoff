import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "fixtures" / "task-b"
SERVICE = '''TASKS = [
    {"id": 1, "owner": "ada", "status": "open", "created_at": 30},
    {"id": 2, "owner": "bob", "status": "open", "created_at": 10},
    {"id": 3, "owner": "ada", "status": "closed", "created_at": 20},
    {"id": 4, "owner": "ada", "status": "open", "created_at": 15},
]


def list_tasks(owner=None):
    return sorted((task for task in TASKS if task["status"] == "open" and (owner is None or task["owner"] == owner)), key=lambda task: task["created_at"])
'''
API = '''from service import list_tasks


def handle_get_tasks(query):
    raw_limit = query.get("limit")
    if raw_limit is not None and (not raw_limit.isdigit() or int(raw_limit) < 1):
        return 400, {"error": "invalid limit"}
    tasks = list_tasks(query.get("owner"))
    return 200, {"tasks": tasks if raw_limit is None else tasks[:int(raw_limit)]}
'''


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_b_snapshots_are_incomplete(snapshot):
    project = ROOT / snapshot / "project"
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=project).returncode != 0
    assert (ROOT / snapshot / "handoff-context.md").is_file()


@pytest.mark.parametrize("snapshot", ("30", "60", "80"))
def test_task_b_acceptance_allows_a_correct_completion(tmp_path, snapshot):
    project = tmp_path / "project"
    shutil.copytree(ROOT / snapshot / "project", project)
    (project / "service.py").write_text(SERVICE, encoding="utf-8")
    (project / "api.py").write_text(API, encoding="utf-8")
    assert subprocess.run([sys.executable, "acceptance.py"], cwd=project).returncode == 0
