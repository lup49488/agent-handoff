"""Summarize schema-v3/v4 trials without pooling task, snapshot, or target cohorts."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks.validate_trial import validate  # noqa: E402
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS  # noqa: E402


COHORT_KEYS = ("agent", "version", "model", "effort")
SUPPORTED_VERSIONS = (3, 4)

#: Two passes seen in one probe round are a fraction of a second apart; two
#: rounds are at least most of the five-second interval apart. A v3 record has
#: no round numbers, so a gap under this is read as "same round".
SAME_ROUND_SECONDS = 1.0


def _cohort_key(record: Dict[str, Any]) -> Tuple[str, ...]:
    target = record["target"]
    return tuple(str(target.get(key, "unrecorded")) for key in COHORT_KEYS)


def _trial_paths(inputs: Sequence[Path]) -> List[Path]:
    found = set()
    for item in inputs:
        if item.is_dir():
            found.update(item.rglob("trial.json"))
            found.update(item.glob("*-r[0-9]*.json"))
        elif item.is_file():
            found.add(item)
        else:
            raise FileNotFoundError(str(item))
    return sorted(found)


def progress_separable(record: Dict[str, Any]) -> bool:
    """Whether this trial's progress time is an observation in its own right.

    A pass at launch is not progress the target made, and a pass in the same
    probe round as completion cannot be told apart from it. v4 records say so
    directly; for v3 the same fact is read from the gap between the two times.
    """
    if "progress_separable" in record:
        return bool(record["progress_separable"])
    progress = record.get("first_verified_progress_seconds")
    completion = record.get("completion_seconds")
    if not progress:
        return False
    return completion is None or completion - progress >= SAME_ROUND_SECONDS


def summarize(
    records: Iterable[Dict[str, Any]],
    expected_replicates: int = 3,
    exclusions: Optional[Mapping[str, str]] = None,
) -> List[dict]:
    """Return snapshot-stratified cells for one or more separately keyed cohorts.

    `exclusions` maps a trial id to the reason it is set aside. An excluded
    trial is counted as such and nothing else, so a summary and a written
    report that excludes the same trials cannot disagree about a cell.
    """
    if expected_replicates < 1:
        raise ValueError("expected_replicates must be positive")
    exclusions = dict(exclusions or {})
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    excluded: Dict[Tuple[Any, ...], int] = {}
    cohorts = set()
    seen_ids = set()
    for record in records:
        if record.get("schema_version") not in SUPPORTED_VERSIONS:
            raise ValueError("summarize accepts schema_version 3 and 4 records only")
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
        if record["trial_id"] in exclusions:
            excluded[key] = excluded.get(key, 0) + 1
            groups.setdefault(key, [])
            continue
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
        separable = [trial for trial in accepted if progress_separable(trial)]
        progress = [trial["first_verified_progress_seconds"] for trial in separable]
        completion = [trial["completion_seconds"] for trial in completed
                      if trial.get("completion_seconds") is not None]
        row = dict(zip((*COHORT_KEYS, "task", "snapshot", "arm"), key))
        row.update({
            "trials_seen": len(trials) + excluded.get(key, 0),
            "excluded": excluded.get(key, 0),
            "accepted": len(accepted),
            "invalid": len(trials) - len(accepted),
            "completed": len(completed),
            "completion_rate": (len(completed) / len(accepted)) if accepted else None,
            "progress_separable": len(separable),
            # Only trials whose progress was its own observation: the rest
            # would just repeat the launch (zero) or the completion time.
            "median_first_progress_seconds": statistics.median(progress) if progress else None,
            "median_completion_seconds": statistics.median(completion) if completion else None,
            "expected_replicates": expected_replicates,
            "short_by": max(0, expected_replicates - len(accepted)),
        })
        rows.append(row)
    return rows


def _load_exclusions(path: Optional[Path]) -> Dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("exclusions", data) if isinstance(data, dict) else None
    if not isinstance(entries, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in entries.items()
    ):
        raise ValueError("exclusions must map each trial id to a non-empty reason")
    return entries


def _cell(value: Any, column: str) -> str:
    if value is None:
        return "—"
    if column == "completion_rate":
        return "%.0f%%" % (value * 100)
    if isinstance(value, float):
        return "%.3f" % value
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trials", nargs="+", type=Path, help="trial.json files or directories containing them")
    parser.add_argument("--expected-replicates", type=int, default=3)
    parser.add_argument("--exclusions", type=Path, help="JSON mapping trial id to the reason it is excluded")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)
    try:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in _trial_paths(args.trials)]
        rows = summarize(records, args.expected_replicates, _load_exclusions(args.exclusions))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    columns = (
        "agent", "version", "model", "effort", "task", "snapshot", "arm", "excluded", "accepted", "invalid",
        "completed", "completion_rate", "progress_separable", "median_first_progress_seconds",
        "median_completion_seconds", "short_by",
    )
    widths = {column: max([len(column)] + [len(_cell(row.get(column), column)) for row in rows])
              for column in columns}
    print("  ".join(column.ljust(widths[column]) for column in columns))
    for row in rows:
        print("  ".join(_cell(row.get(column), column).ljust(widths[column]) for column in columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
