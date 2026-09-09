"""The .agent-handoff/ working directory.

Writes are atomic (tmp + fsync + rename) so a crash mid-write cannot leave a
truncated file behind, and journal appends never splice onto a partial line
left by a killed process. What a crash does leave behind -- debris, stale
locks -- is handled in integrity.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from agent_handoff import HANDOFF_DIR
from agent_handoff.models import GitState, Task


class StoreNotInitialized(RuntimeError):
    pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def find_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from `start` looking for a project store.

    A store is identified by its task.json, not merely by the directory name.
    Per-user state lives in ``~/.agent-handoff/`` too, and without this every
    directory anywhere under the home directory would walk up and mistake the
    home directory for a project root.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / HANDOFF_DIR / "task.json").is_file():
            return candidate
    return None


class Store:
    """File-backed handoff state rooted at <project>/.agent-handoff/."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # -- locations ---------------------------------------------------------
    @property
    def dir(self) -> Path:
        return self.root / HANDOFF_DIR

    @property
    def task_file(self) -> Path:
        return self.dir / "task.json"

    @property
    def git_file(self) -> Path:
        return self.dir / "git.json"

    @property
    def events_file(self) -> Path:
        return self.dir / "events.jsonl"

    @property
    def commands_file(self) -> Path:
        return self.dir / "commands.jsonl"

    @property
    def tests_file(self) -> Path:
        return self.dir / "tests.json"

    @property
    def state_file(self) -> Path:
        """Semantic checkpoint, written by the agent at milestones."""
        return self.dir / "state.md"

    @property
    def checkpoints_file(self) -> Path:
        """When each checkpoint was taken -- used to report its staleness."""
        return self.dir / "checkpoints.jsonl"

    @property
    def lock_file(self) -> Path:
        """Write lock: held briefly around one mutating command. See lock.py."""
        return self.dir / "lock"

    @property
    def run_lock_file(self) -> Path:
        """Session lock: held for a whole supervised run.

        Separate from the write lock on purpose. A supervised agent is asked
        to record its own checkpoints while it works, and those `handoff
        checkpoint` calls must not be blocked by the supervisor that started
        them.
        """
        return self.dir / "run.lock"

    @property
    def logs_dir(self) -> Path:
        return self.dir / "logs"

    @property
    def archive_dir(self) -> Path:
        """Finished tasks, kept out of the way of the current one."""
        return self.dir / "archive"

    @property
    def handoff_file(self) -> Path:
        return self.root / "HANDOFF.md"

    @property
    def exists(self) -> bool:
        return self.dir.is_dir()

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls, start: Optional[Path] = None) -> "Store":
        root = find_root(start)
        if root is None:
            raise StoreNotInitialized(
                "no .agent-handoff/ found here or in any parent -- run `handoff init` first"
            )
        return cls(root)

    #: Everything that belongs to one task rather than to the project.
    TASK_FILES = (
        "task.json",
        "state.md",
        "events.jsonl",
        "commands.jsonl",
        "checkpoints.jsonl",
        "tests.json",
    )

    def archive_current(self) -> Optional[Path]:
        """Move the current task's records aside, keyed by its task id.

        Starting a new task in a directory that already holds one must not
        leave the old journal and checkpoint in place: the next handoff would
        hand the new agent the *previous* task's reasoning, which is worse
        than handing it nothing.
        """
        if not self.task_file.is_file():
            return None
        try:
            task_id = self.read_task().task_id
        except Exception:
            task_id = "unknown"

        target = self.archive_dir / task_id
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.archive_dir / (task_id + "-" + str(suffix))
        target.mkdir(parents=True, exist_ok=True)

        for name in self.TASK_FILES:
            path = self.dir / name
            if path.is_file():
                path.replace(target / name)
        if self.logs_dir.is_dir() and any(self.logs_dir.iterdir()):
            self.logs_dir.replace(target / "logs")
        if self.handoff_file.is_file():
            self.handoff_file.replace(target / "HANDOFF.md")
        return target

    def init(self, task: Task) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self.write_task(task)
        for f in (self.events_file, self.commands_file):
            f.touch()

    # -- task --------------------------------------------------------------
    def write_task(self, task: Task) -> None:
        from agent_handoff.models import now

        task.updated_at = now()
        atomic_write(
            self.task_file, json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n"
        )

    def read_task(self) -> Task:
        if not self.task_file.is_file():
            raise StoreNotInitialized("missing " + str(self.task_file))
        return Task.from_dict(json.loads(self.task_file.read_text(encoding="utf-8")))

    # -- git ---------------------------------------------------------------
    def write_git(self, state: GitState) -> None:
        atomic_write(self.git_file, json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n")

    def read_git(self) -> Optional[GitState]:
        if not self.git_file.is_file():
            return None
        return GitState(**json.loads(self.git_file.read_text(encoding="utf-8")))

    # -- append-only journals ----------------------------------------------
    def append(self, path: Path, record: Dict[str, Any]) -> None:
        """Append one JSON line and flush it to disk immediately.

        Append-only + flush is what makes the journal survive a killed
        process: whatever was written before the kill is still there.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if self._ends_cleanly(path) else "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(prefix + json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _ends_cleanly(path: Path) -> bool:
        """Does the journal end on a record boundary?

        A process killed mid-append leaves a line with no newline. Appending
        straight onto it would splice two records into one unreadable line and
        lose the new one too, so the next append starts on its own line.
        """
        try:
            size = path.stat().st_size
        except OSError:
            return True
        if size == 0:
            return True
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"

    def iter_jsonl(self, path: Path) -> Iterator[Dict[str, Any]]:
        if not path.is_file():
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # a partial last line from a killed process -- skip it
                    continue

    def tail_jsonl(self, path: Path, limit: int) -> List[Dict[str, Any]]:
        records = list(self.iter_jsonl(path))
        return records[-limit:] if limit > 0 else records

    # -- semantic state ----------------------------------------------------
    def read_state_md(self) -> Optional[str]:
        if not self.state_file.is_file():
            return None
        text = self.state_file.read_text(encoding="utf-8").strip()
        return text or None

    # -- logs --------------------------------------------------------------
    def tail_log(self, name: str, lines: int) -> str:
        path = self.logs_dir / name
        if not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
