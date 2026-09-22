"""Run one benchmark trial. Launching a target agent is always opt-in.

The runner measures a target in a disposable fixture.  Its evaluator source
comes from the immutable fixture definition rather than project/acceptance.py,
so a target cannot make a trial pass by changing its in-worktree checker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_handoff import __version__, gitinfo  # noqa: E402
from agent_handoff.adapters import registry  # noqa: E402
from agent_handoff.runner import _kill_process_tree  # noqa: E402
from agent_handoff.lock import Lock  # noqa: E402
from agent_handoff.store import atomic_write  # noqa: E402
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS, prepare, snapshot_files  # noqa: E402
from benchmarks.validate_trial import validate  # noqa: E402

TARGETS = ("codex", "claude-code")
PROBE_SECONDS = 5.0
HANDOFF_INSTRUCTION = "\n\nOpen and follow HANDOFF.md before making changes."

# The fixtures pre-register the only source paths a target may change.  The
# acceptance script itself is deliberately excluded: it is evaluator input,
# not a solution artifact.
ALLOWED_PATHS = {
    "A": {"slug.py"},
    "B": {"api.py"},
    "C": {"normalization.py", "report.py", "cli.py"},
}


def _target_argv(adapter, prompt: str, target: str, model: str, effort: str) -> list:
    argv = adapter.exec_argv(prompt)
    if target == "codex":
        pinned = ["--model", model, "-c", 'model_reasoning_effort="' + effort + '"']
    else:
        pinned = ["--model", model, "--effort", effort]
    return argv[:-1] + pinned + argv[-1:]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fixture_metadata(task: str, snapshot: str) -> Dict[str, str]:
    files = snapshot_files(task, snapshot)
    acceptance = files["acceptance.py"]
    packed = "".join(name + "\0" + text + "\0" for name, text in sorted(files.items()))
    return {
        "task": task,
        "snapshot": snapshot,
        "snapshot_sha256": _sha256(packed),
        "evaluator_sha256": _sha256(acceptance),
    }


def _evaluate(project: Path, task: str, snapshot: str, *, persist_to: Optional[Path] = None) -> Tuple[bool, str, str]:
    """Run the fixture-owned evaluator with the project as its import root.

    ``python -c`` preserves the task workspace on sys.path while the source is
    fetched from benchmarks/fixtures rather than the agent-controlled project.
    """
    acceptance = snapshot_files(task, snapshot)["acceptance.py"]
    result = subprocess.run(
        [sys.executable, "-c", acceptance],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if persist_to is not None:
        (persist_to / "acceptance.stdout.log").write_text(result.stdout, encoding="utf-8")
        (persist_to / "acceptance.stderr.log").write_text(result.stderr, encoding="utf-8")
    return result.returncode == 0, result.stdout, result.stderr


def _git_head(project: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project), capture_output=True,
        text=True, check=True,
    ).stdout.strip()


def _scope_violations(project: Path, task: str, baseline_head: str) -> list[str]:
    """Return paths outside scope relative to the immutable trial baseline.

    Comparing to the saved baseline also includes target-created commits, so a
    target cannot hide a modified evaluator helper merely by committing it.
    """
    changed = subprocess.run(
        ["git", "diff", "--name-only", baseline_head],
        cwd=str(project), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(project), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted({path for path in changed + untracked if path and path not in ALLOWED_PATHS[task]})


def _read_cohort_plan(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("seed"), int):
        raise ValueError("unsupported cohort plan")
    if not isinstance(data.get("rows"), list):
        raise ValueError("cohort plan has no rows")
    return data


def _write_cohort_plan(path: Path, data: Dict[str, Any]) -> None:
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def _claim_cohort_trial_unlocked(path: Path, trial_id: str) -> Dict[str, Any]:
    """Claim exactly the next pending plan row before a real launch."""
    data = _read_cohort_plan(path)
    running = [row for row in data["rows"] if row.get("state") == "running"]
    if running:
        raise ValueError("cohort plan has an unresolved running trial: " + str(running[0].get("trial_id")))
    next_row = next((row for row in data["rows"] if row.get("state") == "pending"), None)
    if next_row is None:
        raise ValueError("cohort plan has no pending trials")
    if next_row.get("trial_id") != trial_id:
        raise ValueError("cohort plan requires next trial " + str(next_row.get("trial_id")))
    next_row["state"] = "running"
    _write_cohort_plan(path, data)
    return {"seed": data["seed"], **next_row}


def _cohort_lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _claim_cohort_trial(path: Path, trial_id: str) -> Dict[str, Any]:
    with Lock(_cohort_lock_path(path), command="benchmark cohort claim"):
        return _claim_cohort_trial_unlocked(path, trial_id)


def _set_cohort_trial_state_unlocked(path: Path, trial_id: str, state: str) -> None:
    data = _read_cohort_plan(path)
    row = next((candidate for candidate in data["rows"] if candidate.get("trial_id") == trial_id), None)
    if row is None or row.get("state") != "running":
        raise ValueError("cohort plan has no running trial " + trial_id)
    row["state"] = state
    _write_cohort_plan(path, data)


def _set_cohort_trial_state(path: Path, trial_id: str, state: str) -> None:
    with Lock(_cohort_lock_path(path), command="benchmark cohort state"):
        _set_cohort_trial_state_unlocked(path, trial_id, state)


def _finish_cohort_trial(path: Path, trial_id: str) -> None:
    _set_cohort_trial_state(path, trial_id, "recorded")


def _release_cohort_trial(path: Path, trial_id: str) -> None:
    _set_cohort_trial_state(path, trial_id, "pending")


class _ProgressProbe:
    """Record the first independently verified all-checks-pass state."""

    def __init__(self, project: Path, task: str, snapshot: str, started: float) -> None:
        self.project = project
        self.task = task
        self.snapshot = snapshot
        self.started = started
        self.first_pass: Optional[float] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(PROBE_SECONDS):
            passed, _, _ = _evaluate(self.project, self.task, self.snapshot)
            if passed:
                self.first_pass = round(time.monotonic() - self.started, 3)
                return

    def __enter__(self) -> "_ProgressProbe":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        self._thread.join(timeout=PROBE_SECONDS * 2)


def _package_stayed_out_of_git(project: Path) -> bool:
    package_paths = {".agent-handoff", "HANDOFF.md"}

    def first_segments(output: str) -> set:
        return {line.split("/")[0] for line in output.splitlines() if line}

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout
    changed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout
    seen = first_segments(tracked) | {line[3:].split("/")[0] for line in changed.splitlines() if line}
    return not (seen & package_paths)


def run_trial(
    task: str,
    snapshot: str,
    arm: str,
    target: str,
    replicate: int,
    destination: Path,
    *,
    model: str = "unrecorded",
    target_version: str = "unrecorded",
    wall_seconds: int = 600,
    effort: str = "medium",
    launch: bool = False,
    plan_seed: Optional[int] = None,
    cohort_plan: Optional[Path] = None,
) -> Dict[str, Any]:
    """Prepare a trial, optionally launch one selected target, and record evidence."""
    if target not in TARGETS:
        raise ValueError("target must be one of: " + ", ".join(TARGETS))
    if replicate < 1 or wall_seconds < 1:
        raise ValueError("replicate and wall_seconds must be positive")
    if launch and (model == "unrecorded" or target_version == "unrecorded"):
        raise ValueError("--launch requires --model and --target-version")
    if cohort_plan is not None and not launch:
        raise ValueError("--cohort-plan requires --launch")

    trial = prepare(task, snapshot, arm, destination)
    if not _package_stayed_out_of_git(trial.project):
        raise RuntimeError("the handoff package entered the trial repository")

    baseline_head = _git_head(trial.project)
    record: Dict[str, Any] = {
        "schema_version": 2,
        "trial_id": "%s-%s-%s-r%02d" % (task, snapshot, arm, replicate),
        "task": task,
        "snapshot": snapshot,
        "arm": arm,
        "replicate": replicate,
        "source_commit": gitinfo.collect(ROOT).head or "unknown",
        "handoff_version": __version__,
        "target": {"agent": target, "version": target_version, "model": model, "effort": effort},
        "budget": {"wall_seconds": wall_seconds},
        "fixture": dict(_fixture_metadata(task, snapshot), initial_head=baseline_head),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "accepted": False,
        "completed": False,
        "budget_exceeded": False,
        "first_verified_progress_seconds": None,
        "completion_seconds": None,
        "agent_exit_seconds": None,
        "target_exit_code": None,
        "target_turns": None,
        "provider_tokens": None,
        "scope_violations": [],
        "invalid_reason": "not_launched",
    }
    if cohort_plan is not None:
        planned_plan = _read_cohort_plan(cohort_plan)
        if plan_seed is not None and plan_seed != planned_plan["seed"]:
            raise ValueError("--plan-seed does not match --cohort-plan")
        plan_seed = planned_plan["seed"]
    if plan_seed is not None:
        order = ("baseline", "handoff")
        # Reproduce plan.py's stable per-cell shuffling without making trial
        # execution implicit. The caller still launches only an explicit arm.
        import random
        arms = list(order)
        random.Random("%d|%s|%s|%d" % (plan_seed, task, snapshot, replicate)).shuffle(arms)
        record["schedule"] = {"seed": plan_seed, "planned_arm_order": arms, "planned_position": arms.index(arm) + 1}

    claimed = False
    try:
        if cohort_plan is not None:
            claimed_row = _claim_cohort_trial(cohort_plan, record["trial_id"])
            claimed = True
            record["schedule"]["cohort_plan"] = cohort_plan.name
            record["schedule"]["cohort_position"] = claimed_row["position"]
        if launch:
            record.update(_launch(trial, task, snapshot, target, arm, model, effort, wall_seconds, baseline_head))
        (trial.root / "trial.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except Exception:
        if claimed:
            _release_cohort_trial(cohort_plan, record["trial_id"])
        raise
    if claimed:
        _finish_cohort_trial(cohort_plan, record["trial_id"])
    return record


def _launch(trial, task: str, snapshot: str, target: str, arm: str, model: str, effort: str, wall_seconds: int, baseline_head: str) -> Dict[str, Any]:
    adapter = registry.get(target, trial.project)
    prompt = trial.prompt.read_text(encoding="utf-8")
    if arm == "handoff":
        prompt += HANDOFF_INSTRUCTION

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _target_argv(adapter, prompt, target, model, effort),
            cwd=str(trial.project), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        (trial.root / "target.stderr.log").write_text(str(exc) + "\n", encoding="utf-8")
        return {"invalid_reason": "target_not_started"}

    exceeded = False
    with _ProgressProbe(trial.project, task, snapshot, started) as probe:
        try:
            stdout, stderr = process.communicate(timeout=wall_seconds)
        except subprocess.TimeoutExpired:
            exceeded = True
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)
    (trial.root / "target.stdout.log").write_text(stdout or "", encoding="utf-8")
    (trial.root / "target.stderr.log").write_text(stderr or "", encoding="utf-8")

    passed, _, _ = _evaluate(trial.project, task, snapshot, persist_to=trial.root)
    violations = _scope_violations(trial.project, task, baseline_head)
    if not exceeded and process.returncode != 0:
        return {"agent_exit_seconds": elapsed, "target_exit_code": process.returncode, "invalid_reason": "target_failed"}

    completed = passed and not violations and not exceeded
    first_pass = probe.first_pass
    if completed and first_pass is None:
        first_pass = elapsed
    return {
        "accepted": True,
        "completed": completed,
        "budget_exceeded": exceeded,
        "first_verified_progress_seconds": first_pass,
        "completion_seconds": first_pass if completed else elapsed,
        "agent_exit_seconds": elapsed,
        "target_exit_code": process.returncode,
        "scope_violations": violations,
        "invalid_reason": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a trial; --launch starts one selected CLI and spends real quota.")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("snapshot", choices=SNAPSHOTS)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("replicate", type=int)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--model", default="unrecorded")
    parser.add_argument("--target-version", default="unrecorded")
    parser.add_argument("--wall-seconds", type=int, default=600)
    parser.add_argument("--effort", choices=("medium",), default="medium")
    parser.add_argument("--plan-seed", type=int, help="record the pre-registered arm-order seed")
    parser.add_argument("--cohort-plan", type=Path, help="claim only the next trial from this pre-registered plan")
    parser.add_argument("--launch", action="store_true", help="start the target agent")
    args = parser.parse_args(argv)
    try:
        record = run_trial(**vars(args))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print("recorded " + record["trial_id"] + ": " + (record["invalid_reason"] or "accepted"))
    problems = validate(record)
    for problem in problems:
        print("  invalid record: " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
