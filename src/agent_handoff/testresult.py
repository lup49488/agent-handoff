"""Whether a recorded test result still describes the code.

A test result is only worth handing over if the next agent can tell whether
it was taken on the code it is looking at. Without that it has to re-run the
check to know where the work stands -- on the recovery pilot's task E v3 that
was ten seconds a run -- and a result recorded before the code changed again
reads exactly like a current one.

So `handoff tests` records the tree the result was taken on: HEAD, and a
digest of every change on top of it, tracked or untracked. The handoff
layer's own files are left out, so writing a checkpoint or rendering
`HANDOFF.md` does not make a result look stale.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional, Tuple

from agent_handoff import HANDOFF_DIR, gitinfo
from agent_handoff.models import now
from agent_handoff.store import Store, atomic_write

#: How much of a test command's output a record keeps.
OUTPUT_LINES = 30

CURRENT = "current"
STALE = "stale"
UNKNOWN = "unknown"


def tree(root: Path) -> Optional[Dict[str, str]]:
    """HEAD and a digest of the uncommitted changes, or None outside a repository."""
    if not gitinfo.is_repo(root):
        return None
    code, head, _ = gitinfo.run_git(root, "rev-parse", "HEAD")
    if code != 0:
        return None  # no commit yet: nothing to anchor the changes to
    digest = hashlib.sha256()
    _, patch, _ = gitinfo.run_git(
        root, "diff", "HEAD", "--binary", "--", ".",
        ":(exclude)" + HANDOFF_DIR, ":(exclude)HANDOFF.md",
    )
    digest.update(patch.encode("utf-8", errors="replace"))
    _, listing, _ = gitinfo.run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for name in sorted(n for n in listing.split("\0") if n and not gitinfo._is_self(n)):
        digest.update(b"\0untracked\0" + name.encode("utf-8", errors="replace") + b"\0")
        try:
            digest.update((root / name).read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return {"head": head.strip(), "changes": digest.hexdigest()}


def write(store: Store, cmd: str, exit_code: int, summary: str = "", output: str = "") -> Dict[str, object]:
    """Record a test result as `tests.json`, with the code it was taken on."""
    import json

    record: Dict[str, object] = {"ts": now(), "cmd": cmd, "exit_code": exit_code, "passed": exit_code == 0}
    if summary:
        record["summary"] = summary
    if output.strip():
        record["output_tail"] = "\n".join(output.strip().splitlines()[-OUTPUT_LINES:])
    taken_on = tree(store.root)
    if taken_on is not None:
        record["tree"] = taken_on
    atomic_write(store.tests_file, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def current_pass(store: Store) -> bool:
    """Whether the latest recorded result passed, on the code exactly as it is now."""
    import json

    try:
        record = json.loads(store.tests_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(record, dict) and record.get("passed")
                and freshness(store.root, record)[0] == CURRENT)


def freshness(root: Path, record: Dict[str, object]) -> Tuple[str, str]:
    """`(state, sentence)` for a recorded result against the code as it is now."""
    recorded = record.get("tree")
    if not isinstance(recorded, dict):
        return UNKNOWN, (
            "Recorded without the code it ran on, so whether it still holds is "
            "unknown: re-run it before relying on it."
        )
    now = tree(root)
    if now is None:
        return UNKNOWN, "The code it ran on cannot be compared here: re-run it before relying on it."
    then_head = str(recorded.get("head") or "")
    if then_head != now["head"]:
        return STALE, (
            "**Stale:** recorded at commit `" + then_head[:7] + "`, and HEAD is now `"
            + now["head"][:7] + "`. Re-run it before relying on it."
        )
    if recorded.get("changes") != now["changes"]:
        return STALE, (
            "**Stale:** the uncommitted changes differ from those it ran on. "
            "Re-run it before relying on it."
        )
    return CURRENT, (
        "**Current:** recorded on the code exactly as it is now (HEAD `" + now["head"][:7]
        + "`, the same uncommitted changes). It still describes the work; it need "
        "not be re-run to know where things stand."
    )
