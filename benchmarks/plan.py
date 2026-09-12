"""Print the run order for a cohort, with arm order randomised per cell.

The design requires randomising arm order within each task/snapshot pair, so
that time of day, a provider's rate-limit state and the evaluator's own
learning do not land preferentially on one arm. It also requires the plan to
be pre-registered, which a shuffle nobody can reproduce cannot be — so the
order is derived from a seed that goes into the record with the results.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS  # noqa: E402


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
                    rows.append(
                        {
                            "task": task,
                            "snapshot": snapshot,
                            "replicate": replicate,
                            "position": position,
                            "arm": arm,
                            "trial_id": "%s-%s-%s-r%02d" % (task, snapshot, arm, replicate),
                        }
                    )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True, help="pre-register this with the plan")
    parser.add_argument("--replicates", type=int, default=3, help="accepted replicates per cell")
    parser.add_argument("--task", action="append", choices=TASKS, help="default: every task")
    args = parser.parse_args(argv)
    if args.replicates < 1:
        parser.error("replicates must be positive")

    rows = plan(args.seed, args.replicates, args.task or TASKS)
    print("# seed " + str(args.seed) + ", " + str(len(rows)) + " trials")
    for row in rows:
        print("%-18s task %s  snapshot %s  replicate %d  run %d of 2" % (
            row["trial_id"], row["task"], row["snapshot"], row["replicate"], row["position"]
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
