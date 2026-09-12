"""Prepare one disposable benchmark trial without launching an agent.

The script deliberately stops after creating either the ordinary baseline
workspace or the equivalent workspace plus a verified ``agent-handoff``
package. A separate, explicitly authorised harness is responsible for
starting a target agent and recording a trial result.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
# Both are needed: `src` for the package under test, and the project root so
# `benchmarks.*` resolves when this file runs as a script. Only pytest puts
# the root on the path by itself, which is why the documented command lines
# failed while the tests covering them passed.
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_handoff import gitinfo  # noqa: E402
from agent_handoff.checkpoint import Checkpoint  # noqa: E402
from agent_handoff.handoff import write_package  # noqa: E402
from agent_handoff.integrity import errors, verify_package  # noqa: E402
from agent_handoff.models import Task  # noqa: E402
from agent_handoff.store import Store, atomic_write  # noqa: E402

TASKS = ("A", "B", "C")
SNAPSHOTS = ("30", "60", "80")
ARMS = ("baseline", "handoff")
FIXTURES = ROOT / "benchmarks" / "fixtures"

#: The earliest snapshot is the state the task starts from: at 30% the source
#: agent has read the code and reproduced the failure without editing it yet.
#: Committing this and leaving a later snapshot uncommitted is what gives the
#: Baseline arm the Git history the design says it gets.
BASE_SNAPSHOT = "30"

#: Passed to every Git call so a trial neither reads nor writes the
#: developer's global identity, and does not stall on a signing prompt.
TRIAL_IDENTITY = (
    "-c", "user.name=benchmark",
    "-c", "user.email=benchmark@example.invalid",
    "-c", "commit.gpgsign=false",
)

#: Kept out of Git the way the real tool keeps them out of a user's
#: repository. Written to `.git/info/exclude` rather than a tracked
#: `.gitignore`, so the Baseline worktree carries no hint that a handoff
#: package could exist.
EXCLUDED = (".agent-handoff/", "HANDOFF.md", "__pycache__/", "*.pyc")

#: Fixture heading -> the checkpoint field it belongs in. Progress, reasoning
#: and the next step are three different things, and the package being
#: measured is worth less when they are folded into one blob.
SECTIONS = {
    "source progress": "completed",
    "decision": "decisions",
    "next step": "next_step",
}


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


def _fixture_dir(task: str) -> Path:
    return FIXTURES / ("task-" + task.lower())


def snapshot_files(task: str, snapshot: str) -> Dict[str, str]:
    """Every file of one snapshot, keyed by its path inside the project."""
    if task == "C":
        from benchmarks.fixtures.task_c import snapshot_files as task_c_files

        return task_c_files(snapshot)
    project = _fixture_dir(task) / snapshot / "project"
    if not project.is_dir():
        raise RuntimeError("fixture is incomplete: " + str(project))
    return {
        path.relative_to(project).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(project.rglob("*"))
        if path.is_file() and path.suffix != ".pyc"
    }


def fixture_prompt(task: str) -> str:
    if task == "C":
        from benchmarks.fixtures.task_c import PROMPT

        return PROMPT
    return (_fixture_dir(task) / "prompt.md").read_text(encoding="utf-8")


def fixture_context(task: str, snapshot: str) -> str:
    """What the source agent knew, which only the Handoff arm receives."""
    if task == "C":
        from benchmarks.fixtures.task_c import context

        return context(snapshot)
    return (_fixture_dir(task) / snapshot / "handoff-context.md").read_text(encoding="utf-8")


def _git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", *TRIAL_IDENTITY, *args], cwd=str(project), check=True, capture_output=True
    )


def _write_tree(project: Path, files: Dict[str, str]) -> None:
    """Make the worktree hold exactly `files`, leaving `.git` alone."""
    for existing in sorted(project.rglob("*"), reverse=True):
        if ".git" in existing.relative_to(project).parts:
            continue
        if existing.is_file():
            existing.unlink()
        elif existing.is_dir():
            existing.rmdir()
    for name, text in sorted(files.items()):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _init_repo(project: Path) -> None:
    """Commit the pre-task state so both arms can read real Git history.

    Without this the trial directory is not a repository at all. Placed inside
    a checkout, the tool reports the *enclosing* project's branch, HEAD and
    commits; placed outside one, the Baseline arm has no history to recover
    from. Either decides the comparison before the target agent starts.
    """
    _git(project, "init", "-q")
    (project / ".git" / "info" / "exclude").write_text(
        "\n".join(EXCLUDED) + "\n", encoding="utf-8"
    )
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "Project state before the task began")


def _sections(context: str) -> Dict[str, List[str]]:
    """Split a fixture context file by the headings it uses.

    Anything under an unrecognised heading is dropped rather than filed
    somewhere it does not belong.
    """
    found: Dict[str, List[str]] = {}
    current = None
    for line in context.splitlines():
        heading = re.match(r"^##\s+(.*\S)\s*$", line)
        if heading:
            current = SECTIONS.get(heading.group(1).strip().lower())
            if current is not None:
                found.setdefault(current, [])
        elif current is not None:
            found[current].append(line)
    return found


def _items(block: str) -> List[str]:
    """The checkpoint's list fields, from either bullets or a prose block.

    `Source progress` is written as bullets and `Decision` as a paragraph, and
    both belong in a list field, so an unbulleted block becomes a single item.
    """
    items: List[str] = []
    for line in block.splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items and line.strip():
            items[-1] += " " + line.strip()
    if items:
        return items
    return [" ".join(block.split())] if block.strip() else []


def _checkpoint(prompt: str, context: str) -> Checkpoint:
    """Turn one fixture's context file into the checkpoint it describes.

    Rendered through the product's own `Checkpoint`, so the package a target
    agent reads in a trial is shaped exactly like one a real agent writes.
    """
    found = _sections(context)
    blocks = {field: "\n".join(lines).strip() for field, lines in found.items()}
    return Checkpoint(
        objective=prompt.strip(),
        completed=_items(blocks.get("completed", "")),
        decisions=_items(blocks.get("decisions", "")),
        problem="The acceptance check does not pass yet.",
        next_step=blocks.get("next_step", ""),
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
    project = destination / "project"
    project.mkdir()

    _write_tree(project, snapshot_files(task, BASE_SNAPSHOT))
    _init_repo(project)
    if snapshot != BASE_SNAPSHOT:
        _write_tree(project, snapshot_files(task, snapshot))

    prompt = fixture_prompt(task)
    atomic_write(destination / "prompt.md", prompt)

    if arm == "handoff":
        store = Store(project)
        # Neutral names and a real handoff reason. "benchmark-source" and an
        # invented "benchmark_preparation" reason both told the target agent,
        # inside the package it is being measured on, that it is in an
        # experiment — which is not a neutral prompt.
        store.init(
            Task(
                original_request=prompt,
                source_agent="previous-agent",
                fallback_agent="next-agent",
            )
        )
        store.write_git(gitinfo.collect(project))
        checkpoint = _checkpoint(prompt, fixture_context(task, snapshot))
        atomic_write(store.state_file, checkpoint.render())
        write_package(store, reason="manual_handoff", target_agent="next-agent")
        package_errors = errors(verify_package(store))
        if package_errors:
            raise RuntimeError(
                "invalid handoff package: " + "; ".join(f.message for f in package_errors)
            )

    return PreparedTrial(destination, project, destination / "prompt.md", arm)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare one local benchmark workspace; never launches an agent."
    )
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("snapshot", choices=SNAPSHOTS)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        trial = prepare(args.task, args.snapshot, args.arm, args.destination)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print("prepared " + trial.arm + " trial at " + str(trial.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
