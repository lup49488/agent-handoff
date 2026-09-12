"""Validate a redacted benchmark trial record before it is aggregated."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


REQUIRED = {
    "trial_id": str,
    "task": str,
    "snapshot": str,
    "arm": str,
    "replicate": int,
    "source_commit": str,
    "handoff_version": str,
    "target": dict,
    "budget": dict,
    "accepted": bool,
    "completed": bool,
    "budget_exceeded": bool,
    "invalid_reason": (str, type(None)),
}
OPTIONAL_NUMBERS = {
    "first_verified_progress_seconds",
    "completion_seconds",
    "target_turns",
    "provider_tokens",
    "repeated_commands",
    "repeated_file_reads",
}
SECRET_MARKERS = ("api_key", "authorization", "bearer ", "sk-", "oauth")


def validate(record: Dict[str, Any]) -> List[str]:
    errors = []
    for key, expected in REQUIRED.items():
        if key not in record:
            errors.append("missing " + key)
        elif not isinstance(record[key], expected):
            errors.append("invalid " + key)
    if record.get("arm") not in ("baseline", "handoff"):
        errors.append("arm must be baseline or handoff")
    if isinstance(record.get("replicate"), bool) or record.get("replicate", 0) < 1:
        errors.append("replicate must be positive")
    if record.get("completed") and not record.get("accepted"):
        errors.append("completed trial must be accepted")
    # An accepted trial ran; running past the budget is one of its outcomes,
    # not a reason to discard it. Carrying both states at once hides which
    # denominator the trial belongs in.
    if record.get("accepted") and record.get("invalid_reason") is not None:
        errors.append("accepted trial must not carry an invalid_reason")
    if not record.get("accepted") and record.get("invalid_reason") is None:
        errors.append("a trial that was not accepted needs an invalid_reason")
    if record.get("budget_exceeded") and not record.get("accepted"):
        errors.append("budget_exceeded only applies to an accepted trial")
    if record.get("accepted") and record.get("completion_seconds") is None:
        errors.append("accepted trial must record completion_seconds")
    for key in OPTIONAL_NUMBERS:
        value = record.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            errors.append("invalid " + key)
    serialized = json.dumps(record, ensure_ascii=False).lower()
    if any(marker in serialized for marker in SECRET_MARKERS):
        errors.append("record appears to contain a secret marker")
    return errors


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-h"], ["--help"]):
        print("usage: validate_trial.py TRIAL.json")
        return 0
    if len(argv) != 1:
        print("usage: validate_trial.py TRIAL.json")
        return 2
    try:
        record = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("invalid JSON: " + str(exc))
        return 2
    errors = validate(record) if isinstance(record, dict) else ["record must be an object"]
    if errors:
        print("invalid trial: " + "; ".join(errors))
        return 1
    print("valid trial: " + record["trial_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
