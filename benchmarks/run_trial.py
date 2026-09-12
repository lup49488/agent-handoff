"""Run one benchmark trial. Launching a target agent is always opt-in.

Preparation is free and repeatable; a launch spends real quota on a real
coding agent, so it happens only behind ``--launch`` and only for the one
target named on the command line.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_handoff import __version__, gitinfo  # noqa: E402
from agent_handoff.adapters import registry  # noqa: E402

# Reused rather than reimplemented: on Windows killing the wrapper leaves the
# coding CLI's workers running, and a timed-out trial that keeps spending
# quota is the one failure this harness must not cause.
from agent_handoff.runner import _kill_process_tree  # noqa: E402
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS, prepare  # noqa: E402
from benchmarks.validate_trial import validate  # noqa: E402

TARGETS = ("codex", "claude-code")

#: How often the acceptance check is re-run while the target works, to find
#: the first moment verified progress exists. Frequent enough to be useful,
#: rare enough not to compete with the agent for the machine.
PROBE_SECONDS = 5.0

#: Appended to the Handoff arm's prompt. The Baseline arm is told nothing
#: about the package, and neither arm is told it is in an experiment.
HANDOFF_INSTRUCTION = "\n\nOpen and follow HANDOFF.md before making changes."


def _target_argv(adapter, prompt: str, target: str, model: str, effort: str) -> list:
    """The adapter's own command line, with the pinned model and effort."""
    argv = adapter.exec_argv(prompt)
    if target == "codex":
        pinned = ["--model", model, "-c", 'model_reasoning_effort="' + effort + '"']
    else:
        pinned = ["--model", model, "--effort", effort]
    return argv[:-1] + pinned + argv[-1:]


def _acceptance_passes(project: Path) -> bool:
    """Whether the task's pre-registered acceptance check passes right now."""
    return subprocess.run(
        [sys.executable, "acceptance.py"],
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


class _ProgressProbe:
    """Record when the acceptance check first passes, while the target works.

    Completion time alone cannot separate an agent that reached a working
    state quickly from one that reached it just before exiting, and time to
    verified progress is the comparative metric the design leads with. A probe
    that loses a race against a half-written file simply fails and retries.
    """

    def __init__(self, project: Path, started: float) -> None:
        self.project = project
        self.started = started
        self.first_pass: Optional[float] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(PROBE_SECONDS):
            if _acceptance_passes(self.project):
                self.first_pass = round(time.monotonic() - self.started, 3)
                return

    def __enter__(self) -> "_ProgressProbe":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        self._thread.join(timeout=PROBE_SECONDS * 2)


def _package_stayed_out_of_git(project: Path) -> bool:
    """Guardrail: the recovery package must not enter the task's repository.

    The design requires that preparing a trial leaves the tracked source
    alone. If the package were tracked, the Handoff arm's target would see a
    different Git history from the Baseline arm's and the comparison would be
    measuring the instrumentation.
    """
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
) -> Dict[str, Any]:
    """Prepare a trial, optionally launch the target, and record the result."""
    if target not in TARGETS:
        raise ValueError("target must be one of: " + ", ".join(TARGETS))
    if replicate < 1 or wall_seconds < 1:
        raise ValueError("replicate and wall_seconds must be positive")
    if launch and (model == "unrecorded" or target_version == "unrecorded"):
        raise ValueError("--launch requires --model and --target-version")

    trial = prepare(task, snapshot, arm, destination)
    if not _package_stayed_out_of_git(trial.project):
        raise RuntimeError("the handoff package entered the trial repository")

    record: Dict[str, Any] = {
        "trial_id": "%s-%s-%s-r%02d" % (task, snapshot, arm, replicate),
        "task": task,
        "snapshot": snapshot,
        "arm": arm,
        "replicate": replicate,
        "source_commit": gitinfo.collect(ROOT).head or "unknown",
        "handoff_version": __version__,
        "target": {"agent": target, "version": target_version, "model": model, "effort": effort},
        "budget": {"wall_seconds": wall_seconds},
        "accepted": False,
        "completed": False,
        "budget_exceeded": False,
        # Turn counts and provider tokens need a harness that can read the
        # target's own telemetry. Reporting them as null is the design's
        # instruction; estimating them from output length is forbidden.
        "target_turns": None,
        "provider_tokens": None,
        "invalid_reason": "not_launched",
    }

    if launch:
        record.update(_launch(trial, target, arm, model, effort, wall_seconds))

    (trial.root / "trial.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _launch(trial, target: str, arm: str, model: str, effort: str, wall_seconds: int) -> Dict[str, Any]:
    """Start one target agent and measure what this harness can observe."""
    adapter = registry.get(target, trial.project)
    prompt = trial.prompt.read_text(encoding="utf-8")
    if arm == "handoff":
        prompt += HANDOFF_INSTRUCTION

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _target_argv(adapter, prompt, target, model, effort),
            cwd=str(trial.project),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        (trial.root / "target.stderr.log").write_text(str(exc) + "\n", encoding="utf-8")
        return {"invalid_reason": "target_not_started"}

    exceeded = False
    with _ProgressProbe(trial.project, started) as probe:
        try:
            stdout, stderr = process.communicate(timeout=wall_seconds)
        except subprocess.TimeoutExpired:
            exceeded = True
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)

    (trial.root / "target.stdout.log").write_text(stdout or "", encoding="utf-8")
    (trial.root / "target.stderr.log").write_text(stderr or "", encoding="utf-8")

    # A target that never accepted the task is an invalid trial, not a failed
    # one. Running past the budget is a real outcome and stays in the sample.
    if not exceeded and process.returncode != 0:
        return {"completion_seconds": elapsed, "invalid_reason": "target_failed"}

    return {
        "accepted": True,
        "completed": _acceptance_passes(trial.project),
        "budget_exceeded": exceeded,
        "first_verified_progress_seconds": probe.first_pass,
        "completion_seconds": elapsed,
        "invalid_reason": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a trial; --launch starts one selected CLI and spends real quota."
    )
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("snapshot", choices=SNAPSHOTS)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("replicate", type=int)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--model", default="unrecorded")
    parser.add_argument("--target-version", default="unrecorded")
    parser.add_argument("--wall-seconds", type=int, default=600)
        # Pinned: effort must not vary inside a cohort. Widening this means
    # deciding how the new level is reported as a separate cohort first.
    parser.add_argument("--effort", choices=("medium",), default="medium")
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
