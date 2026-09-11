"""Prepare one disposable benchmark trial without launching an agent.

The script deliberately stops after creating either the ordinary baseline
workspace or the equivalent workspace plus a verified ``agent-handoff``
package. A separate, explicitly authorised harness is responsible for
starting a target agent and recording a trial result.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_handoff import gitinfo  # noqa: E402
from agent_handoff.handoff import write_package  # noqa: E402
from agent_handoff.integrity import errors, verify_package  # noqa: E402
from agent_handoff.models import Task  # noqa: E402
from agent_handoff.store import Store, atomic_write  # noqa: E402

TASKS = ("A", "B", "C")
SNAPSHOTS = ("30", "60", "80")
ARMS = ("baseline", "handoff")
FIXTURES = ROOT / "benchmarks" / "fixtures"


@dataclass(frozen=True)
class PreparedTrial:
    """Locations created for a trial; no target process has been started."""

    root: Path
    project: Path
    prompt: Path
    arm: str


def _require(value: str, choices: Sequence[str], label: str) -> str:
    normalized = value.upper() if label == "task" else value.lower()
    if normalized not in choices:
        raise ValueError(label + " must be one of: " + ", ".join(choices))
    return normalized


def _static_snapshot(task: str, snapshot: str) -> tuple[Path, Path, Path]:
    snapshot_root = FIXTURES / ("task-" + task.lower()) / snapshot
    project = snapshot_root / "project"
    prompt = snapshot_root.parent / "prompt.md"
    context = snapshot_root / "handoff-context.md"
    if not (project.is_dir() and prompt.is_file() and context.is_file()):
        raise RuntimeError("fixture is incomplete: " + str(snapshot_root))
    return project, prompt, context


def _materialize(task: str, snapshot: str, destination: Path) -> tuple[Path, str, str]:
    """Copy a public snapshot and return project, original request, context."""
    if task == "C":
        from benchmarks.fixtures.task_c import materialize

        project = materialize(destination, snapshot)
        prompt = (destination / "prompt.md").read_text(encoding="utf-8")
        context_path = destination / "handoff-context.md"
        context = context_path.read_text(encoding="utf-8")
        context_path.unlink()
        return project, prompt, context

    source_project, prompt_path, context_path = _static_snapshot(task, snapshot)
    project = destination / "project"
    shutil.copytree(source_project, project, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    prompt = prompt_path.read_text(encoding="utf-8")
    atomic_write(destination / "prompt.md", prompt)
    return project, prompt, context_path.read_text(encoding="utf-8")


def _state(prompt: str, snapshot: str, source_context: str) -> str:
    """Create a checkpoint the package can render, without retaining raw files."""
    next_step = source_context.replace("## Source progress", "").replace("## Next step", "").strip()
    return (
        "# Objective\n\n" + prompt.strip() + "\n\n# Done\n\n"
        + "Materialized the public interruption snapshot " + snapshot + ".\n\n# Decisions\n\n"
        + "This is a local instrumentation setup; no target agent has been launched.\n\n"
        + "# Current issue\n\nThe source agent was interrupted before the acceptance check passed.\n\n"
        + "# Next step\n\n" + next_step + "\n"
    )


def prepare(task: str, snapshot: str, arm: str, destination: Path) -> PreparedTrial:
    """Prepare a non-destructive baseline or handoff workspace.

    ``destination`` must not already exist. This prevents a setup error from
    overwriting a prior trial or its evidence.
    """
    task = _require(task, TASKS, "task")
    snapshot = _require(snapshot, SNAPSHOTS, "snapshot")
    arm = _require(arm, ARMS, "arm")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("destination already exists: " + str(destination))

    destination.mkdir(parents=True)
    project, prompt, source_context = _materialize(task, snapshot, destination)

    if arm == "handoff":
        store = Store(project)
        store.init(Task(original_request=prompt, source_agent="benchmark-source", fallback_agent="benchmark-target"))
        store.write_git(gitinfo.collect(project))
        atomic_write(store.state_file, _state(prompt, snapshot, source_context))
        write_package(store, reason="benchmark_preparation", target_agent="benchmark-target")
        package_errors = errors(verify_package(store))
        if package_errors:
            raise RuntimeError("invalid handoff package: " + "; ".join(f.message for f in package_errors))

    return PreparedTrial(destination, project, destination / "prompt.md", arm)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare one local benchmark workspace; never launches an agent.")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("snapshot", choices=SNAPSHOTS)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        trial = prepare(args.task, args.snapshot, args.arm, args.destination)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print("prepared " + trial.arm + " trial at " + str(trial.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
