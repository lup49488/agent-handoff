"""Phase 5 -- is this handoff actually usable?

Two questions, kept separate:

* `verify_package` -- may the next agent be launched? Asked automatically
  before every handoff, because starting an agent on a package that belongs to
  another task, or on one truncated by a crash, is worse than not handing off
  at all: the agent looks busy while doing the wrong work.
* `inspect` -- what is the overall state of this store? Asked by `handoff
  verify`, and it reports everything, including things that are merely worth
  knowing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import List, Optional

from agent_handoff import checkpoint as checkpoint_mod
from agent_handoff import gitinfo
from agent_handoff import lock as lock_mod
from agent_handoff.handoff import RESUME_INSTRUCTION
from agent_handoff.store import Store

ERROR = "error"
WARNING = "warning"

#: A line of the resume instruction that must survive into every package.
RESUME_MARKER = RESUME_INSTRUCTION.splitlines()[0]


@dataclasses.dataclass
class Finding:
    level: str
    message: str
    hint: str = ""

    def render(self) -> str:
        line = self.level + ": " + self.message
        return line + ("\n    " + self.hint if self.hint else "")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def errors(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.level == ERROR]


def verify_package(store: Store) -> List[Finding]:
    """Checks that must pass before another agent is launched."""
    found: List[Finding] = []
    path = store.handoff_file

    if not path.is_file():
        return [Finding(ERROR, "no HANDOFF.md -- nothing to hand over")]

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return [Finding(ERROR, "HANDOFF.md is empty -- the write did not complete")]
    if not text.lstrip().startswith("# HANDOFF"):
        found.append(Finding(ERROR, "HANDOFF.md does not look like a handoff package"))
    if RESUME_MARKER not in text:
        found.append(
            Finding(
                ERROR,
                "HANDOFF.md has no resume instruction -- it was truncated",
                "re-render it with `handoff pack`",
            )
        )

    try:
        task = store.read_task()
    except Exception as exc:  # unreadable task.json is fatal for a handoff
        return found + [Finding(ERROR, "cannot read task.json: " + str(exc))]

    if task.task_id not in text:
        found.append(
            Finding(
                ERROR,
                "HANDOFF.md belongs to a different task than task.json",
                "re-render it with `handoff pack`",
            )
        )
    return found


def inspect(store: Store) -> List[Finding]:
    """The full state of a store: damage, drift, and leftovers."""
    found: List[Finding] = []

    try:
        task = store.read_task()
    except Exception as exc:
        return [Finding(ERROR, "cannot read task.json: " + str(exc))]

    # -- journals ----------------------------------------------------------
    for path in (store.events_file, store.commands_file, store.checkpoints_file):
        if not path.is_file():
            continue
        broken = 0
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                json.loads(line)
            except ValueError:
                broken += 1
        if broken:
            found.append(
                Finding(
                    WARNING,
                    str(broken) + " unreadable line(s) in " + path.name + " -- a killed write",
                    "they are skipped when reading; `handoff recover` drops them",
                )
            )

    # -- leftovers ---------------------------------------------------------
    for tmp in sorted(store.dir.glob("*.tmp")):
        found.append(
            Finding(
                WARNING,
                "leftover " + tmp.name + " from an interrupted write",
                "the real file is intact; `handoff recover` removes it",
            )
        )

    holder = lock_mod.read(store.lock_file) if store.lock_file.exists() else None
    if holder is not None:
        level, hint = (
            (WARNING, "`handoff recover --break-lock` clears it")
            if holder.is_stale()
            else (WARNING, "another handoff process is working here")
        )
        state = "stale lock" if holder.is_stale() else "locked"
        found.append(Finding(level, state + " by " + holder.describe(), hint))

    # -- drift -------------------------------------------------------------
    stored = store.read_git()
    if stored is not None and stored.is_repo:
        current = gitinfo.collect(store.root)
        if current.is_repo and current.head != stored.head:
            found.append(
                Finding(
                    WARNING,
                    "git.json is stale: recorded " + str(stored.head) + ", now " + str(current.head),
                    "`handoff snapshot` refreshes it",
                )
            )

    record = checkpoint_mod.last_record(store)
    state_text = store.read_state_md()
    if record and state_text is not None:
        recorded = record.get("digest")
        if recorded and recorded != digest(state_text):
            found.append(
                Finding(
                    WARNING,
                    "state.md was edited after its checkpoint was recorded",
                    "not a problem -- but its age line is only as accurate as the edit",
                )
            )
    if state_text and checkpoint_mod.Checkpoint.parse(state_text).is_empty():
        found.append(
            Finding(
                WARNING,
                "state.md has no recognisable sections -- it will read as prose only",
                "headings: " + ", ".join(checkpoint_mod.SECTION_ORDER),
            )
        )

    # -- unfinished business -----------------------------------------------
    if task.status == "handoff_prepared":
        found.append(
            Finding(
                WARNING,
                "task is mid-handoff: a package was written but no agent took it",
                "`handoff switch --to <agent>` hands it on, or start one by hand",
            )
        )

    if store.handoff_file.exists():
        found.extend(verify_package(store))

    return found


def recover(store: Store, break_lock: bool = False, dry_run: bool = False) -> List[str]:
    """Clean up what a crash left behind. Never touches real content."""
    actions: List[str] = []

    # A supervised run intentionally does not hold the ordinary lock while
    # its agent works, so that the agent can checkpoint. Recovery must still
    # never rewrite journals beneath that live session.
    run_holder = lock_mod.read(store.run_lock_file) if store.run_lock_file.exists() else None
    if run_holder is not None and not run_holder.is_stale():
        raise lock_mod.LockBusy(
            "supervised run active: " + run_holder.describe() + " -- wait for it to finish"
        )
    if store.run_lock_file.exists() and (run_holder is None or run_holder.is_stale()):
        actions.append(
            "remove " + ("unreadable " if run_holder is None else "stale ") + "run lock"
        )
        if not dry_run:
            store.run_lock_file.unlink(missing_ok=True)

    for tmp in sorted(store.dir.glob("*.tmp")):
        actions.append("remove leftover " + tmp.name)
        if not dry_run:
            tmp.unlink(missing_ok=True)

    for path in (store.events_file, store.commands_file, store.checkpoints_file):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        good = []
        dropped = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                json.loads(line)
            except ValueError:
                dropped += 1
                continue
            good.append(line)
        if dropped:
            actions.append("drop " + str(dropped) + " unreadable line(s) from " + path.name)
            if not dry_run:
                from agent_handoff.store import atomic_write

                atomic_write(path, "\n".join(good) + ("\n" if good else ""))

    holder = lock_mod.read(store.lock_file) if store.lock_file.exists() else None
    if store.lock_file.exists():
        if holder is None:
            actions.append("remove unreadable lock file")
            if not dry_run:
                store.lock_file.unlink(missing_ok=True)
        elif holder.is_stale():
            actions.append("break stale lock held by " + holder.describe())
            if not dry_run:
                store.lock_file.unlink(missing_ok=True)
        elif break_lock:
            actions.append("force-break lock held by " + holder.describe())
            if not dry_run:
                store.lock_file.unlink(missing_ok=True)

    return actions
