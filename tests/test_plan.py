"""The run order has to be both randomised and pre-registerable."""

import subprocess
import sys
from collections import Counter

from benchmarks.plan import arm_order, plan
from benchmarks.prepare_trial import ROOT


def test_the_same_seed_gives_the_same_plan_in_a_new_process():
    """A plan nobody can reproduce cannot be pre-registered.

    Python randomises string hashing per process, so an order derived from
    `hash()` would differ on every run while looking deterministic in one.
    """
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from benchmarks.plan import plan;"
        "print([row['trial_id'] for row in plan(20260911, 2)])"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": ""},
        ).stdout
        for seed in ("0", "1", "99991")
    }

    assert len(runs) == 1


def test_a_different_seed_gives_a_different_order():
    assert [row["trial_id"] for row in plan(1, 3)] != [row["trial_id"] for row in plan(2, 3)]


def test_both_arms_run_first_about_equally_often():
    first = Counter(row["arm"] for row in plan(20260911, 10) if row["position"] == 1)

    assert min(first.values()) / sum(first.values()) > 0.35


def test_every_cell_runs_both_arms_once():
    cells = Counter((row["task"], row["snapshot"], row["replicate"]) for row in plan(7, 3))

    assert set(cells.values()) == {2}
    assert len(cells) == 3 * 3 * 3


def test_arm_order_is_stable_for_one_cell():
    assert arm_order("A", "60", 1, 7) == arm_order("A", "60", 1, 7)
    assert set(arm_order("A", "60", 1, 7)) == {"baseline", "handoff"}
