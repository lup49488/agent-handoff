"""Phase 5 -- one writer at a time.

Two `handoff` processes in the same repository would interleave their journal
appends and race each other's task.json. A lock file makes the second one wait
its turn, or say why it cannot.

A lock outlives the process that took it if that process is killed, so the
holder is recorded and a lock whose holder is gone is broken rather than
respected -- an abandoned lock must never be able to wedge a repository.
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class LockBusy(RuntimeError):
    """Another live process holds the lock."""


def process_alive(pid: int) -> bool:
    """Is a process with this id running?

    On Windows `os.kill(pid, 0)` is not a liveness probe -- CPython maps it to
    TerminateProcess, so it would kill the very process we are asking about.
    Query the process object instead.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False  # gone, or not ours to see
        try:
            # Still running == the process object has not been signalled.
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    return True


@dataclasses.dataclass
class LockInfo:
    pid: int
    host: str
    command: str
    acquired_at: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockInfo":
        return cls(
            pid=int(data.get("pid", 0)),
            host=str(data.get("host", "")),
            command=str(data.get("command", "")),
            acquired_at=str(data.get("acquired_at", "")),
        )

    @property
    def is_local(self) -> bool:
        return self.host == socket.gethostname()

    def is_stale(self) -> bool:
        """True when the holder is gone, so the lock can be taken over.

        A lock from another machine (a shared filesystem) cannot be checked,
        so it is never assumed stale.
        """
        return self.is_local and not process_alive(self.pid)

    def describe(self) -> str:
        return (
            "pid " + str(self.pid) + " on " + self.host
            + " (" + (self.command or "?") + ") since " + self.acquired_at
        )


def read(path: Path) -> Optional[LockInfo]:
    try:
        return LockInfo.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _mine(command: str) -> LockInfo:
    from agent_handoff.models import now

    return LockInfo(
        pid=os.getpid(),
        host=socket.gethostname() or platform.node(),
        command=command,
        acquired_at=now(),
    )


class Lock:
    """Advisory lock over one `.agent-handoff/` directory."""

    def __init__(self, path: Path, command: str = ""):
        self.path = Path(path)
        self.info = _mine(command)
        self.held = False
        self.broke: Optional[LockInfo] = None

    def _write(self) -> bool:
        """Create the lock file, failing if someone got there first."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.info.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True

    def acquire(self) -> "Lock":
        if self._write():
            self.held = True
            return self

        holder = read(self.path)
        if holder is None:
            # An empty or unreadable lock file is debris, not a claim.
            self.path.unlink(missing_ok=True)
        elif holder.is_stale():
            self.broke = holder
            self.path.unlink(missing_ok=True)
        elif not holder.is_local:
            raise LockBusy(
                "locked by " + holder.describe()
                + " -- another machine, so its liveness cannot be checked; "
                "run `handoff recover --break-lock` if you know it is gone"
            )
        else:
            raise LockBusy(
                "locked by " + holder.describe() + " -- wait for it, or run "
                "`handoff recover --break-lock` if you know it is gone"
            )

        if not self._write():  # someone raced us to the freed lock
            raise LockBusy("lock was taken while breaking a stale one -- try again")
        self.held = True
        return self

    def release(self) -> None:
        """Drop the lock, but only if it is still ours."""
        if not self.held:
            return
        holder = read(self.path)
        if holder and (holder.pid, holder.acquired_at) == (self.info.pid, self.info.acquired_at):
            self.path.unlink(missing_ok=True)
        self.held = False

    def __enter__(self) -> "Lock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
