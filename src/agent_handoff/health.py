"""Phase 6 -- remembering which agents are currently out of action.

The chain knows the order to try agents in. It does not know that Codex ran
out of quota twenty minutes ago, so every new task starts by launching it
again, waiting for it to fail, and only then handing off. That is the same
wasted round-trip this tool exists to remove, one level up.

Two things make this file's shape:

* **Quota is an account fact, not a project fact.** An agent that is out of
  quota is out of quota in every repository, so health lives once per user
  (``~/.agent-handoff/health.json``), not inside a project's store.
* **Health is advice, not truth.** A cooldown is a guess about when a limit
  resets. So an agent in cooldown is only ever *deprioritised* -- if every
  candidate is cooling down, the one closest to recovering is tried anyway.
  Refusing to work because of a guess would be worse than a wasted launch.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from agent_handoff.models import now
from agent_handoff.store import atomic_write
from agent_handoff.lock import Lock, LockBusy

#: How long an agent is presumed unavailable after each kind of failure.
#:
#: These are re-probe intervals, not real reset times -- nothing here claims to
#: know when a provider's window rolls over. Two reasons are deliberately zero:
#: a context that filled up belongs to the *session*, not the agent, and a
#: crash may well be about this one task rather than the agent's health.
#:
#: The quota interval is five hours because that is the window length observed
#: in a real Codex exhaustion (`window_minutes: 300`), where the true reset was
#: 226 minutes away and an hour's guess would have called the agent ready
#: almost three hours early. Overshooting only means preferring another agent;
#: undershooting means launching one that is still out, which is the cost this
#: whole file exists to avoid.
DEFAULT_COOLDOWNS: Dict[str, int] = {
    "quota_exhausted": 5 * 3600,
    "rate_limited": 300,
    "agent_stalled": 300,
    "provider_error": 120,
    "network_failure": 60,
    "context_exhausted": 0,
    "process_crashed": 0,
    "manual_handoff": 0,
}

ENV_HOME = "AGENT_HANDOFF_HOME"
HEALTH_LOCK_WAIT_SECONDS = 5.0


def home() -> Path:
    """Where per-user state lives. Overridable, which is what tests use."""
    override = os.environ.get(ENV_HOME)
    return Path(override) if override else Path.home() / ".agent-handoff"


def _parse(stamp: str) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclasses.dataclass
class AgentHealth:
    agent: str
    reason: str = ""
    since: str = ""
    retry_after: str = ""
    failures: int = 0
    last_ok: str = ""
    #: True when retry_after came from the provider rather than a cooldown.
    measured: bool = False

    def to_dict(self) -> Dict[str, object]:
        data = dataclasses.asdict(self)
        data.pop("agent")
        return data

    def seconds_left(self, at: Optional[_dt.datetime] = None) -> float:
        moment = _parse(self.retry_after)
        if moment is None:
            return 0.0
        return max(0.0, (moment - (at or _utcnow())).total_seconds())

    def is_cooling(self, at: Optional[_dt.datetime] = None) -> bool:
        return self.seconds_left(at) > 0

    def describe(self) -> str:
        if self.is_cooling():
            minutes = int(self.seconds_left() // 60)
            when = (str(minutes) + " min") if minutes else "under a minute"
            how = " (its own reset time)" if self.measured else ""
            return "cooling down after " + self.reason + ", retry in " + when + how
        if self.reason and not self.last_ok:
            return "last seen failing (" + self.reason + ")"
        return "ready"


class Health:
    """The per-user health file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: Dict[str, AgentHealth] = {}
        self.load()

    @classmethod
    def open(cls, base: Optional[Path] = None) -> "Health":
        return cls((base or home()) / "health.json")

    # -- storage -----------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        agents = raw.get("agents", {}) if isinstance(raw, dict) else {}
        self.records = {}
        for name, data in agents.items():
            if isinstance(data, dict):
                fields = {f.name for f in dataclasses.fields(AgentHealth)}
                clean = {k: v for k, v in data.items() if k in fields}
                self.records[name] = AgentHealth(agent=name, **clean)

    def save(self) -> None:
        payload = {
            "updated_at": now(),
            "agents": {name: rec.to_dict() for name, rec in sorted(self.records.items())},
        }
        atomic_write(self.path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    @contextmanager
    def _write_lock(self):
        """Serialize read-modify-write updates shared by every project."""
        deadline = time.monotonic() + HEALTH_LOCK_WAIT_SECONDS
        while True:
            held = Lock(self.path.with_name(self.path.name + ".lock"), command="health")
            try:
                held.acquire()
                break
            except LockBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        try:
            # Another project may have updated this file while this instance
            # waited for the lock. Always mutate the newest records.
            self.load()
            yield
        finally:
            held.release()

    # -- reading -----------------------------------------------------------
    def get(self, agent: str) -> AgentHealth:
        return self.records.get(agent) or AgentHealth(agent=agent)

    def cooling(self, agent: str) -> bool:
        return self.get(agent).is_cooling()

    # -- writing -----------------------------------------------------------
    def record_failure(
        self,
        agent: str,
        reason: str,
        cooldowns: Optional[Dict[str, int]] = None,
        retry_at: Optional[str] = None,
    ) -> AgentHealth:
        """Record a failure and when to consider the agent again.

        `retry_at` is the provider's own reset time when one is known, and it
        wins over any cooldown: a measured answer beats an estimate. Observed
        once already -- a real Codex window had 226 minutes left where the
        estimate said 60.
        """
        with self._write_lock():
            table = dict(DEFAULT_COOLDOWNS)
            table.update(cooldowns or {})
            seconds = int(table.get(reason, 0))

            record = self.records.setdefault(agent, AgentHealth(agent=agent))
            record.reason = reason
            record.since = now()
            record.failures += 1
            if retry_at:
                record.retry_after = retry_at
                record.measured = True
            else:
                record.retry_after = (
                    (_utcnow() + _dt.timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
                    if seconds > 0
                    else ""
                )
                record.measured = False
            self.save()
            return record

    def record_success(self, agent: str) -> AgentHealth:
        with self._write_lock():
            record = self.records.setdefault(agent, AgentHealth(agent=agent))
            record.last_ok = now()
            record.reason = ""
            record.retry_after = ""
            record.failures = 0
            record.measured = False
            self.save()
            return record

    def clear(self, agent: Optional[str] = None) -> None:
        with self._write_lock():
            if agent is None:
                self.records = {}
            else:
                self.records.pop(agent, None)
            self.save()

    # -- selection ---------------------------------------------------------
    def order(self, agents: Sequence[str]) -> List[str]:
        """Chain order, but agents believed to be down go last.

        Nothing is dropped: a cooldown is a guess, and a guess must not be
        able to strand a task with no agent to hand it to. Among agents that
        are cooling down, the one closest to recovering comes first.
        """
        ready = [a for a in agents if not self.cooling(a)]
        cooling = [a for a in agents if self.cooling(a)]
        cooling.sort(key=lambda a: self.get(a).seconds_left())
        return ready + cooling

    def skipped(self, agents: Sequence[str]) -> List[str]:
        """Which of these would be tried only as a last resort."""
        return [a for a in agents if self.cooling(a)]


class DisabledHealth:
    """Health-shaped no-op used when a project disables health routing."""

    records: Dict[str, AgentHealth] = {}

    def get(self, agent: str) -> AgentHealth:
        return AgentHealth(agent=agent)

    def cooling(self, agent: str) -> bool:
        return False

    def record_failure(self, agent: str, reason: str, *args, **kwargs) -> AgentHealth:
        return AgentHealth(agent=agent, reason=reason)

    def record_success(self, agent: str) -> AgentHealth:
        return AgentHealth(agent=agent)

    def order(self, agents: Sequence[str]) -> List[str]:
        return list(agents)

    def skipped(self, agents: Sequence[str]) -> List[str]:
        return []
