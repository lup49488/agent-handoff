"""Serializable records written into .agent-handoff/."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import uuid
from typing import Any, Dict, Optional


def now() -> str:
    """UTC timestamp, ISO 8601, second precision."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def new_task_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclasses.dataclass
class Task:
    """task.json -- the task itself, independent of any agent."""

    original_request: str
    source_agent: str
    fallback_agent: str
    task_id: str = dataclasses.field(default_factory=new_task_id)
    created_at: str = dataclasses.field(default_factory=now)
    updated_at: str = dataclasses.field(default_factory=now)
    status: str = "running"  # running | handoff_prepared | handed_off | done | abandoned
    handoff_count: int = 0
    #: Agents that already ran out on this task, in order. The chain does not
    #: hand work back to an agent that has already failed on it.
    attempted: list = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class GitState:
    """git.json -- deterministic repository state. No LLM involved."""

    is_repo: bool = False
    branch: Optional[str] = None
    head: Optional[str] = None
    dirty: bool = False
    modified: list = dataclasses.field(default_factory=list)
    created: list = dataclasses.field(default_factory=list)
    deleted: list = dataclasses.field(default_factory=list)
    untracked: list = dataclasses.field(default_factory=list)
    diffstat: str = ""
    recent_commits: list = dataclasses.field(default_factory=list)
    collected_at: str = dataclasses.field(default_factory=now)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def changed_files(self) -> list:
        return sorted(set(self.modified + self.created + self.deleted + self.untracked))
