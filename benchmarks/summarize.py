"""Summarize schema-v3 trials without pooling task, snapshot, or target cohorts."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks.validate_trial import validate  # noqa: E402
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS  # noqa: E402


COHORT_KEYS = ("agent", "version", "model", "effort")


def _cohort_key(record: Dict[str, Any]) -> Tuple[str, ...]:
    target = record["target"]
    return tuple(str(target.get(key, "unrecorded")) for key in COHORT_KEYS)


def _trial_paths(inputs: Sequence[Path]) -> List[Path]:
    found = set()
    for item in inputs:
        if item.is_dir():
            found.update(item.rglob("trial.json"))
        elif item.is_file():
            found.add(item)
        else:
            raise FileNotFoundError(str(item))
    return sorted(found)


def summarize(records: Iterable[Dict[str, Any]], expected_replicates: int = 3) -> List[dict]:
    """Return snapshot-stratified cells for one or more separately keyed cohorts."""
    if expected_replicates < 1:
        raise ValueError("expected_replicates must be positive")
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    cohorts = set()
    seen_ids = set()
    for record in records:
        if record.get("schema_version") != 3:
            raise ValueError("summarize accepts schema_version 3 records only")
        problems = validate(record)
        if problems:
            raise ValueError("invalid trial " + str(record.get("trial_id")) + ": " + "; ".join(problems))
        cohort = _cohort_key(record)
        identity = (*cohort, record["trial_id"])
        if identity in seen_ids:
            raise ValueError("duplicate trial_id in target cohort: " + str(record["trial_id"]))
        seen_ids.add(identity)
        key = (*cohort, record["task"], record["snapshot"], record["arm"])
        cohorts.add(cohort)
        groups.setdefault(key, []).append(record)

    rows = []
    keys = set(groups)
    for cohort in cohorts:
        keys.update((*cohort, task, snapshot, arm)
                    for task in TASKS for snapshot in SNAPSHOTS for arm in ARMS)
    for key in sorted(keys):
        trials = groups.get(key, [])
        accepted = [trial for trial in trials if trial["accepted"]]
        completed = [trial for trial in accepted if trial["completed"]]
        progress = [trial["first_verified_progress_seconds"] for trial in accepted
                    if trial.get("first_verified_progress_seconds") is not None]
        completion = [trial["completion_seconds"] for trial in completed
                      if trial.get("completion_seconds") is not None]
        row = dict(zip((*COHORT_KEYS, "task", "snapshot", "arm"), key))
        row.update({
            "trials_seen": len(trials),
            "accepted": len(accepted),
            "invalid": len(trials) - len(accepted),
            "completed": len(completed),
            "completion_rate": (len(completed) / len(accepted)) if accepted else None,
            "median_first_progress_seconds": statistics.median(progress) if progress else None,
            "median_completion_seconds": statistics.median(completion) if completion else None,
            "expected_replicates": expected_replicates,
            "short_by": max(0, expected_replicates - len(accepted)),
        })
        rows.append(row)
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trials", nargs="+", type=Path, help="trial.json files or directories containing them")
    parser.add_argument("--expected-replicates", type=int, default=3)
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    try:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in _trial_paths(args.trials)]
        rows = summarize(records, args.expected_replicates)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    columns = (
        "agent", "version", "model", "effort", "task", "snapshot", "arm", "accepted", "invalid", "completed",
        "completion_rate", "median_first_progress_seconds", "median_completion_seconds", "short_by",
    )
    widths = {column: max([len(column)] + [len(str(row.get(column, ""))) for row in rows]) for column in columns}
    print("  ".join(column.ljust(widths[column]) for column in columns))
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if column == "completion_rate" and value is not None:
                value = "%.0f%%" % (value * 100)
            values.append(str(value if value is not None else "—").ljust(widths[column]))
        print("  ".join(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
