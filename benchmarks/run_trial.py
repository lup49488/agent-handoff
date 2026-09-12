"""Prepare one benchmark trial; launch is always opt-in and single-target."""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from pathlib import Path
from typing import Sequence
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path: sys.path.insert(0, str(ROOT / "src"))
from agent_handoff import __version__, gitinfo
from agent_handoff.adapters import registry
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS, prepare
from benchmarks.validate_trial import validate
TARGETS = ("codex", "claude-code")

def _target_argv(adapter, prompt, target, model, effort):
    argv = adapter.exec_argv(prompt)
    if target == "codex":
        return argv[:-1] + ["--model", model, "-c", 'model_reasoning_effort="' + effort + '"', argv[-1]]
    return argv[:-1] + ["--model", model, "--effort", effort, argv[-1]]
def run_trial(task, snapshot, arm, target, replicate, destination, *, model="unrecorded", target_version="unrecorded", wall_seconds=600, effort="medium", launch=False):
    if target not in TARGETS: raise ValueError("target must be one of: " + ", ".join(TARGETS))
    if replicate < 1 or wall_seconds < 1: raise ValueError("replicate and wall_seconds must be positive")
    if launch and (model == "unrecorded" or target_version == "unrecorded"): raise ValueError("--launch requires --model and --target-version")
    trial = prepare(task, snapshot, arm, destination)
    head = gitinfo.collect(ROOT).head or "unknown"
    record = {"trial_id": f"{task}-{snapshot}-{arm}-r{replicate:02d}", "task": task, "snapshot": snapshot, "arm": arm, "replicate": replicate, "source_commit": head, "handoff_version": __version__, "target": {"agent": target, "version": target_version, "model": model, "effort": effort}, "budget": {"wall_seconds": wall_seconds}, "accepted": False, "completed": False, "invalid_reason": "not_launched"}
    if launch:
        adapter = registry.get(target, trial.project)
        prompt = trial.prompt.read_text(encoding="utf-8")
        if arm == "handoff": prompt += "\n\nOpen and follow HANDOFF.md before making changes."
        started = time.monotonic()
        try:
            proc = subprocess.run(_target_argv(adapter, prompt, target, model, effort), cwd=trial.project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=wall_seconds, check=False)
            (trial.root / "target.stdout.log").write_text(proc.stdout, encoding="utf-8")
            (trial.root / "target.stderr.log").write_text(proc.stderr, encoding="utf-8")
            record.update(accepted=proc.returncode == 0, invalid_reason=None if proc.returncode == 0 else "target_failed", completion_seconds=round(time.monotonic()-started, 3))
            if proc.returncode == 0:
                record["completed"] = subprocess.run([sys.executable, "acceptance.py"], cwd=trial.project, check=False).returncode == 0
        except subprocess.TimeoutExpired:
            record.update(accepted=True, invalid_reason="wall_timeout", completion_seconds=round(time.monotonic()-started, 3))
        except OSError as exc:
            (trial.root / "target.stderr.log").write_text(str(exc)+"\n", encoding="utf-8")
            record["invalid_reason"] = "target_not_started"
    (trial.root / "trial.json").write_text(json.dumps(record, indent=2)+"\n", encoding="utf-8")
    return record
def main(argv: Sequence[str] | None = None):
    p=argparse.ArgumentParser(description="Prepare a trial; --launch starts one selected CLI.")
    p.add_argument("task", choices=TASKS); p.add_argument("snapshot", choices=SNAPSHOTS); p.add_argument("arm", choices=ARMS); p.add_argument("target", choices=TARGETS); p.add_argument("replicate", type=int); p.add_argument("destination", type=Path)
    p.add_argument("--model", default="unrecorded"); p.add_argument("--target-version", default="unrecorded"); p.add_argument("--wall-seconds", type=int, default=600); p.add_argument("--effort", choices=("medium",), default="medium"); p.add_argument("--launch", action="store_true")
    a=p.parse_args(argv)
    try: record=run_trial(**vars(a))
    except (OSError, RuntimeError, ValueError) as exc: p.error(str(exc))
    print("recorded " + record["trial_id"] + ": " + (record["invalid_reason"] or "accepted"))
    return 0 if not validate(record) else 1
if __name__ == "__main__": raise SystemExit(main())
