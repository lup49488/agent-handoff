"""events.jsonl -- the durable journal.

Every entry is a fact about what happened, appended as it happens. The handoff
package is rendered from this plus git state, so nothing has to be
reconstructed from the dead agent's memory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_handoff.models import now
from agent_handoff.store import Store

# Unified failure abstraction: all of these surface as `agent_unavailable`.
FAILURE_REASONS = (
    "quota_exhausted",
    "rate_limited",
    "context_exhausted",
    "process_crashed",
    # Not a crash: the agent is still running but has gone silent, which for a
    # supervised run is indistinguishable from being gone.
    "agent_stalled",
    "provider_error",
    "network_failure",
    "manual_handoff",
)

# Everything the runner can classify is worth handing off. An unrecoverable
# reason would be one where the next agent cannot help either -- there are
# none yet, but the flag is recorded so a consumer need not guess.
RECOVERABLE = {reason: True for reason in FAILURE_REASONS}


def log_event(store: Store, event: str, **fields: Any) -> Dict[str, Any]:
    record: Dict[str, Any] = {"ts": now(), "event": event}
    record.update({k: v for k, v in fields.items() if v is not None})
    store.append(store.events_file, record)
    return record


def log_command(store: Store, cmd: str, exit_code: Optional[int] = None, note: str = "") -> None:
    record: Dict[str, Any] = {"ts": now(), "cmd": cmd}
    if exit_code is not None:
        record["exit_code"] = exit_code
    if note:
        record["note"] = note
    store.append(store.commands_file, record)
    log_event(store, "command", cmd=cmd, exit_code=exit_code)


def log_agent_unavailable(store: Store, agent: str, reason: str) -> Dict[str, Any]:
    return log_event(
        store,
        "agent_unavailable",
        agent=agent,
        reason=reason,
        recoverable=RECOVERABLE.get(reason, True),
    )


def recent(store: Store, limit: int = 30) -> List[Dict[str, Any]]:
    return store.tail_jsonl(store.events_file, limit)


def last_failure(store: Store) -> Optional[Dict[str, Any]]:
    for record in reversed(list(store.iter_jsonl(store.events_file))):
        if record.get("event") == "agent_unavailable":
            return record
    return None
