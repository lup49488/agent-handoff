"""The run order has to be both randomised and pre-registerable."""

import json
import subprocess
import sys
from collections import Counter

import pytest

from benchmarks.plan import arm_order, plan, write_cohort_plan
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


def test_snapshot_filter_makes_six_trial_instrumentation_cohort():
    rows = plan(20261004, 1, ("A", "B", "C"), ("60",))
    cells = Counter((row["task"], row["snapshot"]) for row in rows)

    assert len(rows) == 6
    assert set(cells.values()) == {2}
    assert set(cells) == {(task, "60") for task in ("A", "B", "C")}


def test_snapshot_filter_is_recorded_in_stateful_plan(tmp_path):
    data = write_cohort_plan(tmp_path / "plan.json", 7, 1, ("A", "B", "C"), ("80",))

    assert data["tasks"] == ["A", "B", "C"]
    assert data["snapshots"] == ["80"]
    assert len(data["rows"]) == 6
    assert {row["snapshot"] for row in data["rows"]} == {"80"}


def test_replicate_start_creates_fresh_r02_six_trial_cohort(tmp_path):
    path = tmp_path / "plan.json"
    data = write_cohort_plan(path, 20261007, 1, ("A", "B", "C"), ("60",), 2)

    assert data["replicate_start"] == 2
    assert {row["replicate"] for row in data["rows"]} == {2}
    assert {row["trial_id"] for row in data["rows"]} == {
        "%s-60-%s-r02" % (task, arm)
        for task in ("A", "B", "C")
        for arm in ("baseline", "handoff")
    }
    assert {row["state"] for row in data["rows"]} == {"pending"}


def test_plan_rejects_nonpositive_replicate_start():
    with pytest.raises(ValueError, match="replicate_start must be positive"):
        plan(7, 1, ("A",), ("60",), 0)


def test_balanced_arm_order_splits_first_position_evenly_and_is_reproducible():
    args = (20261009, 4, ("B",), ("60",), 6, True)
    rows = plan(*args)
    first_arms = [row["arm"] for row in rows if row["position"] == 1]

    assert Counter(first_arms) == {"baseline": 2, "handoff": 2}
    assert rows == plan(*args)
    assert {row["replicate"] for row in rows} == {6, 7, 8, 9}


def test_balanced_plan_records_its_ordering_policy(tmp_path):
    data = write_cohort_plan(
        tmp_path / "balanced.json", 7, 4, ("B",), ("60",), 6, True
    )

    assert data["balance_arm_order"] is True
    assert Counter(row["arm"] for row in data["rows"] if row["position"] == 1) == {
        "baseline": 2,
        "handoff": 2,
    }


def test_odd_balanced_plan_has_only_one_extra_first_position():
    first_arms = Counter(
        row["arm"]
        for row in plan(20261009, 3, ("B",), ("60",), 6, True)
        if row["position"] == 1
    )

    assert set(first_arms) == {"baseline", "handoff"}
    assert max(first_arms.values()) - min(first_arms.values()) == 1


@pytest.mark.parametrize("tasks,snapshots", [(("A", "A"), ("60",)), (("A",), ("60", "60"))])
def test_plan_rejects_duplicate_filters(tasks, snapshots):
    with pytest.raises(ValueError, match="unique"):
        plan(7, 1, tasks, snapshots)


def test_arm_order_is_stable_for_one_cell():
    assert arm_order("A", "60", 1, 7) == arm_order("A", "60", 1, 7)
    assert set(arm_order("A", "60", 1, 7)) == {"baseline", "handoff"}


def test_written_cohort_plan_starts_pending_and_cannot_be_replaced(tmp_path):
    path = tmp_path / "plan.json"
    data = write_cohort_plan(path, 7, 1, ("A",))

    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert {row["state"] for row in data["rows"]} == {"pending"}
    with pytest.raises(FileExistsError):
        write_cohort_plan(path, 8, 1, ("A",))
