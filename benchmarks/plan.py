"""Print the run order for a cohort, with arm order randomised per cell.

The design requires randomising arm order within each task/snapshot pair, so
that time of day, a provider's rate-limit state and the evaluator's own
learning do not land preferentially on one arm. It also requires the plan to
be pre-registered, which a shuffle nobody can reproduce cannot be — so the
order is derived from a seed that goes into the record with the results.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS  # noqa: E402
from agent_handoff import lock as lock_mod  # noqa: E402
from agent_handoff.store import atomic_write  # noqa: E402

#: A row's life: `pending` until claimed, `running` while its trial runs, then
#: `recorded` when the trial belongs in the sample or `invalid` when it does
#: not. Keeping the last two apart is what makes a cell's shortfall visible.
FINAL_STATES = ("recorded", "invalid")


def arm_order(task: str, snapshot: str, replicate: int, seed: int) -> Tuple[str, ...]:
    """The order to run the two arms in for one cell, reproducible from `seed`.

    Seeded per cell rather than once per cohort, so a plan can be regenerated
    for a single re-run without disturbing the order of every other cell.
    """
    arms = list(ARMS)
    # A string seed, not a tuple's hash: Python randomises string hashing per
    # process, so `hash()` would give a different plan on every run and the
    # order could never be pre-registered.
    random.Random("%d|%s|%s|%d" % (seed, task, snapshot, replicate)).shuffle(arms)
    return tuple(arms)


def plan(seed: int, replicates: int, tasks: Sequence[str] = TASKS) -> List[dict]:
    """Every trial of a cohort, in the order it should be run."""
    rows = []
    for task in tasks:
        for snapshot in SNAPSHOTS:
            for replicate in range(1, replicates + 1):
                for position, arm in enumerate(arm_order(task, snapshot, replicate, seed), 1):
                    rows.append({
                        "task": task,
                        "snapshot": snapshot,
                        "replicate": replicate,
                        "position": position,
                        "arm": arm,
                        "trial_id": "%s-%s-%s-r%02d" % (task, snapshot, arm, replicate),
                    })
    return rows


def write_cohort_plan(path: Path, seed: int, replicates: int, tasks: Sequence[str] = TASKS) -> dict:
    """Write an immutable-before-launch plan whose rows are claimed in order."""
    if path.exists():
        raise FileExistsError("cohort plan already exists: " + str(path))
    rows = plan(seed, replicates, tasks)
    for row in rows:
        row["state"] = "pending"
    data = {"schema_version": 1, "seed": seed, "replicates": replicates, "rows": rows}
    atomic_write(path, json.dumps(data, indent=2) + "\n")
    return data


def read_cohort_plan(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("seed"), int):
        raise ValueError("unsupported cohort plan")
    if not isinstance(data.get("rows"), list):
        raise ValueError("cohort plan has no rows")
    return data


def _write(path: Path, data: Dict[str, Any]) -> None:
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def _plan_lock(path: Path) -> lock_mod.Lock:
    path = Path(path)
    return lock_mod.Lock(path.with_name(path.name + ".lock"), command="benchmark cohort plan")


def _holder(row: Dict[str, Any]) -> Optional[lock_mod.LockInfo]:
    claimed = row.get("claimed_by")
    return lock_mod.LockInfo.from_dict(claimed) if isinstance(claimed, dict) else None


def claim_trial(path: Path, trial_id: str) -> Dict[str, Any]:
    """Claim exactly the next pending row before a real launch.

    The claim records which process took it, because the process is what can
    die: a runner killed mid-trial leaves its row `running`, and without the
    holder there is no telling a live trial from an abandoned one.
    """
    with _plan_lock(path):
        data = read_cohort_plan(path)
        running = [row for row in data["rows"] if row.get("state") == "running"]
        if running:
            row = running[0]
            holder = _holder(row)
            if holder is not None and holder.is_stale():
                raise ValueError(
                    "cohort plan row " + str(row.get("trial_id")) + " was claimed by "
                    + holder.describe() + ", which is no longer running. Inspect its trial "
                    "directory, then: python benchmarks/plan.py --plan " + str(path)
                    + " --release " + str(row.get("trial_id"))
                )
            raise ValueError("cohort plan has an unresolved running trial: " + str(row.get("trial_id")))
        next_row = next((row for row in data["rows"] if row.get("state") == "pending"), None)
        if next_row is None:
            raise ValueError("cohort plan has no pending trials")
        if next_row.get("trial_id") != trial_id:
            raise ValueError("cohort plan requires next trial " + str(next_row.get("trial_id")))
        next_row["state"] = "running"
        next_row["claimed_by"] = lock_mod._mine("benchmark trial " + trial_id).to_dict()
        _write(path, data)
        return {"seed": data["seed"], **next_row}


def _move(path: Path, trial_id: str, state: str, *, force: bool = False) -> None:
    with _plan_lock(path):
        data = read_cohort_plan(path)
        row = next((r for r in data["rows"] if r.get("trial_id") == trial_id), None)
        if row is None or row.get("state") != "running":
            raise ValueError("cohort plan has no running trial " + trial_id)
        if state == "pending" and not force:
            holder = _holder(row)
            if holder is not None and not holder.is_stale():
                raise ValueError(
                    trial_id + " is still held by " + holder.describe()
                    + "; a process on another machine cannot be checked, see --force"
                )
        row["state"] = state
        row.pop("claimed_by", None)
        _write(path, data)


def finish_trial(path: Path, trial_id: str, state: str = "recorded") -> None:
    if state not in FINAL_STATES:
        raise ValueError("state must be one of: " + ", ".join(FINAL_STATES))
    _move(path, trial_id, state)


def release_trial(path: Path, trial_id: str, *, force: bool = False) -> None:
    """Return an abandoned `running` row to `pending` so the cohort can go on.

    Refused while the claiming process is alive: releasing a trial that is
    still running would let its row be claimed twice.
    """
    _move(path, trial_id, "pending", force=force)


def status(path: Path) -> Dict[str, Any]:
    """Row states, and which cells lost a replicate to an invalid trial.

    An invalid trial consumes its row without adding to the sample, so a cell
    it hits ends short of the replicates the design requires. The shortfall is
    reported rather than hidden; topping a cell up is a new pre-registered row.
    """
    data = read_cohort_plan(path)
    states = Counter(row.get("state") for row in data["rows"])
    short = sorted({
        "%s-%s-%s" % (row["task"], row["snapshot"], row["arm"])
        for row in data["rows"]
        if row.get("state") == "invalid"
    })
    return {"seed": data["seed"], "states": dict(states), "short_cells": short}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, help="pre-register this with the plan")
    parser.add_argument("--replicates", type=int, default=3, help="accepted replicates per cell")
    parser.add_argument("--task", action="append", choices=TASKS, help="default: every task")
    parser.add_argument("--output", type=Path, help="write a stateful cohort plan")
    parser.add_argument("--plan", type=Path, help="an existing cohort plan, for --status or --release")
    parser.add_argument("--status", action="store_true", help="summarise the plan's row states")
    parser.add_argument("--release", metavar="TRIAL_ID", help="return an abandoned running row to pending")
    parser.add_argument("--force", action="store_true", help="with --release: skip the liveness check")
    args = parser.parse_args(argv)

    if args.status or args.release:
        if args.plan is None:
            parser.error("--status and --release need --plan")
        try:
            if args.release:
                release_trial(args.plan, args.release, force=args.force)
                print("released " + args.release)
            summary = status(args.plan)
        except (OSError, ValueError, lock_mod.LockBusy) as exc:
            parser.error(str(exc))
        print("# seed " + str(summary["seed"]) + ": " + ", ".join(
            "%s %d" % item for item in sorted(summary["states"].items())
        ))
        for cell in summary["short_cells"]:
            print("short: " + cell + " lost a replicate to an invalid trial")
        return 0

    if args.seed is None:
        parser.error("--seed is required to write or print a plan")
    if args.replicates < 1:
        parser.error("replicates must be positive")

    rows = plan(args.seed, args.replicates, args.task or TASKS)
    if args.output:
        write_cohort_plan(args.output, args.seed, args.replicates, args.task or TASKS)
    print("# seed " + str(args.seed) + ", " + str(len(rows)) + " trials")
    for row in rows:
        print("%-18s task %s  snapshot %s  replicate %d  run %d of 2" % (
            row["trial_id"], row["task"], row["snapshot"], row["replicate"], row["position"]
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
