"""Phase 2 -- semantic checkpoints (state.md).

Deterministic state says *what happened*. A checkpoint says *why*: the decision
that was made, the thing currently blocking, the step that should come next.
That is the one part of the picture no amount of git archaeology recovers.

It is the only part of the handoff that costs tokens, so it is deliberately
small and written at milestones only -- not per tool call. The budget below is
a guardrail against a checkpoint quietly turning into a conversation summary.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Dict, List, Optional

from agent_handoff.models import now
from agent_handoff.store import Store, atomic_write

#: Doc target: a checkpoint stays under ~300 tokens.
TOKEN_BUDGET = 300

#: Rough English/code heuristic. Only used to warn, never to reject.
CHARS_PER_TOKEN = 4

OBJECTIVE = "Current Objective"
COMPLETED = "Completed"
DECISIONS = "Decisions"
PROBLEM = "Current Problem"
NEXT = "Next Suggested Step"

SECTION_ORDER = (OBJECTIVE, COMPLETED, DECISIONS, PROBLEM, NEXT)

#: Given to the working agent so it maintains the checkpoint itself.
CHECKPOINT_PROTOCOL = """When you complete a meaningful milestone -- a subtask finished, a test
status change, an architectural decision, or before a long stretch of work --
briefly update the handoff checkpoint:

    handoff checkpoint --objective "..." --done "..." --decision "..." \\
                       --problem "..." --next "..."

Keep it under {budget} tokens. Record only the decision, the progress, the
current problem and the next step -- not a summary of our conversation, and
not anything already visible in the git diff. Do not update it after every
tool call.""".format(budget=TOKEN_BUDGET)


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


@dataclasses.dataclass
class Checkpoint:
    """The semantic half of the handoff state."""

    objective: str = ""
    completed: List[str] = dataclasses.field(default_factory=list)
    decisions: List[str] = dataclasses.field(default_factory=list)
    problem: str = ""
    next_step: str = ""

    def is_empty(self) -> bool:
        return not any([self.objective, self.completed, self.decisions, self.problem, self.next_step])

    def render(self) -> str:
        blocks: List[str] = []
        if self.objective:
            blocks.append("# " + OBJECTIVE + "\n\n" + self.objective)
        if self.completed:
            blocks.append("# " + COMPLETED + "\n\n" + "\n".join("- " + i for i in self.completed))
        if self.decisions:
            blocks.append("# " + DECISIONS + "\n\n" + "\n".join("- " + i for i in self.decisions))
        if self.problem:
            blocks.append("# " + PROBLEM + "\n\n" + self.problem)
        if self.next_step:
            blocks.append("# " + NEXT + "\n\n" + self.next_step)
        return "\n\n".join(blocks) + "\n" if blocks else ""

    def estimated_tokens(self) -> int:
        return estimate_tokens(self.render())

    def over_budget(self) -> bool:
        return self.estimated_tokens() > TOKEN_BUDGET

    @classmethod
    def parse(cls, text: str) -> "Checkpoint":
        """Read back a rendered checkpoint.

        Tolerant on purpose: the agent may have hand-edited state.md, used a
        different heading level, or reworded a heading's case. Anything under
        an unrecognised heading is dropped rather than guessed at.
        """
        aliases = {name.lower(): name for name in SECTION_ORDER}
        aliases.update(
            {
                "objective": OBJECTIVE,
                "goal": OBJECTIVE,
                "done": COMPLETED,
                "progress": COMPLETED,
                "decision": DECISIONS,
                "problem": PROBLEM,
                "current issue": PROBLEM,
                "blocker": PROBLEM,
                "next": NEXT,
                "next step": NEXT,
                "next steps": NEXT,
            }
        )

        buckets: Dict[str, List[str]] = {name: [] for name in SECTION_ORDER}
        current: Optional[str] = None
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip().rstrip(":").lower()
                current = aliases.get(title)
                continue
            if current and stripped:
                buckets[current].append(stripped)

        def _items(name: str) -> List[str]:
            out = []
            for line in buckets[name]:
                out.append(line[1:].strip() if line.startswith(("-", "*")) else line)
            return [i for i in out if i]

        return cls(
            objective=" ".join(buckets[OBJECTIVE]).strip(),
            completed=_items(COMPLETED),
            decisions=_items(DECISIONS),
            problem=" ".join(buckets[PROBLEM]).strip(),
            next_step=" ".join(buckets[NEXT]).strip(),
        )


def load(store: Store) -> Checkpoint:
    return Checkpoint.parse(store.read_state_md() or "")


def _dedup_extend(existing: List[str], new: List[str]) -> List[str]:
    out = list(existing)
    for item in new:
        if item and item not in out:
            out.append(item)
    return out


def update(
    store: Store,
    *,
    objective: Optional[str] = None,
    done: Optional[List[str]] = None,
    decisions: Optional[List[str]] = None,
    problem: Optional[str] = None,
    next_step: Optional[str] = None,
    replace: bool = False,
) -> Checkpoint:
    """Patch the checkpoint: given sections are set, the rest are kept.

    `done` and `decisions` accumulate (they are a history), while objective,
    problem and next step are point-in-time and get overwritten.
    """
    checkpoint = Checkpoint() if replace else load(store)
    if objective is not None:
        checkpoint.objective = objective.strip()
    if done:
        checkpoint.completed = _dedup_extend(checkpoint.completed, [d.strip() for d in done])
    if decisions:
        checkpoint.decisions = _dedup_extend(checkpoint.decisions, [d.strip() for d in decisions])
    if problem is not None:
        checkpoint.problem = problem.strip()
    if next_step is not None:
        checkpoint.next_step = next_step.strip()
    return checkpoint


def write(store: Store, checkpoint: Checkpoint, *, git_head: Optional[str] = None) -> Dict[str, Any]:
    """Persist state.md and record when it was taken, for staleness reporting."""
    from agent_handoff.events import log_event

    atomic_write(store.state_file, checkpoint.render())

    # Log first, then count: the checkpoint's own event belongs before the
    # mark, not among the events that happened after it.
    log_event(
        store,
        "checkpoint_written",
        tokens=checkpoint.estimated_tokens(),
        git_head=git_head,
        over_budget=True if checkpoint.over_budget() else None,
    )
    from agent_handoff.integrity import digest

    record = {
        "ts": now(),
        "git_head": git_head,
        "tokens": checkpoint.estimated_tokens(),
        # Of the stripped text, since that is how it is read back.
        "digest": digest(checkpoint.render().strip()),
        "events_at": sum(1 for _ in store.iter_jsonl(store.events_file)),
    }
    store.append(store.checkpoints_file, record)
    return record


def last_record(store: Store) -> Optional[Dict[str, Any]]:
    records = store.tail_jsonl(store.checkpoints_file, 1)
    return records[0] if records else None


def staleness(store: Store, git_head: Optional[str] = None) -> Optional[str]:
    """One line describing how far reality has moved since the checkpoint.

    A stale checkpoint is still useful -- the next agent just needs to know to
    trust the diff over the prose where they disagree.
    """
    record = last_record(store)
    if record is None:
        return None

    total_events = sum(1 for _ in store.iter_jsonl(store.events_file))
    since = max(0, total_events - int(record.get("events_at") or 0))
    recorded_head = record.get("git_head")

    bits = ["recorded " + str(record.get("ts"))]
    if recorded_head:
        bits.append("at HEAD `" + str(recorded_head) + "`")
    if since:
        bits.append(str(since) + " event(s) recorded since")
    if git_head and recorded_head and git_head != recorded_head:
        bits.append("HEAD has moved to `" + git_head + "` since -- trust the diff over the prose")
    return "_" + ", ".join(bits) + "._"


def summary_line(store: Store) -> str:
    """Short status line, e.g. for `handoff status`."""
    record = last_record(store)
    if record is None:
        return "absent"
    return "recorded " + str(record.get("ts")) + " (~" + str(record.get("tokens")) + " tokens)"
