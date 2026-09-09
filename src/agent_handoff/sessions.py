"""Reading what an agent recorded about its own failure.

Interactive sessions are the blind spot: `handoff run` captures an agent's
output, but when you drive Codex yourself, the layer sees nothing. It cannot
tell whether the agent stopped because of quota, a crash, or you changing your
mind -- so a manual handoff records `manual_handoff` and a cooldown that is
pure guesswork.

The agents themselves already know. Codex writes every session to
``~/.codex/sessions/``, including the failure that ended it and the provider's
own rate-limit telemetry -- with a real reset time, not an estimate.

**What this reads, and nothing else:**

* the failure message that ended the session
* the rate-limit telemetry beside it (used percent, window, reset time)

Both agents publish a real reset time -- Codex as `resets_at`, Claude Code as
`quotaLimits.resetsAt` -- which is worth more than any cooldown this tool could
estimate.

Not the conversation, not the commands, not the diffs. Session logs contain
whatever the agent happened to see -- credentials, private paths, unrelated
work -- and the handoff package is fed straight to another agent, so the
narrowest possible extraction is the only safe one. Two fields cannot leak
what was never read.

Only sessions whose recorded working directory *is* this project are
considered, and reading is skipped entirely when `read_agent_sessions = false`.
"""

from __future__ import annotations

import abc
import dataclasses
import datetime as _dt
import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Never read more than this many recent session files, or this much of one.
MAX_FILES = 12
MAX_BYTES = 8 << 20
#: The failure text kept, in characters. Enough to classify and to show.
MAX_MESSAGE = 600


@dataclasses.dataclass
class SessionFinding:
    """What an agent's own log says about how it stopped."""

    agent: str
    message: str = ""
    reason: Optional[str] = None
    resets_at: Optional[str] = None
    used_percent: Optional[float] = None
    window_minutes: Optional[int] = None
    ended_at: Optional[str] = None
    source: str = ""

    def seconds_until_reset(self, at: Optional[_dt.datetime] = None) -> Optional[float]:
        if not self.resets_at:
            return None
        try:
            moment = _dt.datetime.fromisoformat(self.resets_at)
        except ValueError:
            return None
        now = at or _dt.datetime.now(_dt.timezone.utc)
        return max(0.0, (moment - now).total_seconds())

    def describe(self) -> str:
        bits = [self.agent + ": " + (self.reason or "unclassified")]
        if self.used_percent is not None:
            bits.append(str(self.used_percent) + "% of its window used")
        left = self.seconds_until_reset()
        if left:
            bits.append("resets in " + str(int(left // 60)) + " min")
        return ", ".join(bits)


class SessionReader(abc.ABC):
    """Knows where one agent keeps its session log, and how to read a failure."""

    agent = ""

    @abc.abstractmethod
    def latest_failure(self, project_root: Path) -> Optional[SessionFinding]:
        """The most recent failure recorded for this project, if any."""


def _classify(message: str) -> Optional[str]:
    """Classify a recorded failure message, or say nothing.

    The shared classifier falls back to `process_crashed` for anything it does
    not recognise, which is right for an exit code and wrong here: these
    messages come from the provider, not from a process dying. Unrecognised
    means no opinion, leaving room for the structured fields beside it.
    """
    from agent_handoff.runner import classify_failure

    if not message.strip():
        return None
    reason = classify_failure(message, "", 1)
    return None if reason == "process_crashed" else reason


class CodexSessionReader(SessionReader):
    """Codex CLI writes JSONL rollouts under ~/.codex/sessions/YYYY/MM/DD/.

    Verified against codex-cli 0.153.4, whose records carry a `session_meta`
    header with the working directory, `task_complete` payloads with an
    `error.message`, and `token_usage_record` events carrying `rate_limits`
    with `resets_at`.
    """

    agent = "codex"

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else Path.home() / ".codex" / "sessions"

    def _candidates(self) -> List[str]:
        files = glob.glob(str(self.home / "**" / "*.jsonl"), recursive=True)
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files[:MAX_FILES]

    @staticmethod
    def _belongs_to(path: str, project_root: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                header = json.loads(fh.readline() or "{}")
        except (OSError, ValueError):
            return False
        cwd = (header.get("payload") or {}).get("cwd")
        if not cwd:
            return False
        try:
            return Path(cwd).resolve() == Path(project_root).resolve()
        except OSError:
            return False

    @staticmethod
    def _rate_limits(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        limits = payload.get("rate_limits")
        if not isinstance(limits, dict):
            return None
        primary = limits.get("primary")
        return primary if isinstance(primary, dict) else None

    def _scan(self, path: str) -> Optional[SessionFinding]:
        if os.path.getsize(path) > MAX_BYTES:
            return None

        found = SessionFinding(agent=self.agent, source=path)
        message = ""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"error"' not in line and '"rate_limits"' not in line:
                    continue  # the vast majority of a transcript, never parsed
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue

                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    message = error["message"][:MAX_MESSAGE]
                    found.ended_at = record.get("timestamp") or found.ended_at

                primary = self._rate_limits(payload)
                if primary:
                    if isinstance(primary.get("used_percent"), (int, float)):
                        found.used_percent = float(primary["used_percent"])
                    if isinstance(primary.get("window_minutes"), int):
                        found.window_minutes = primary["window_minutes"]
                    resets = primary.get("resets_at")
                    if isinstance(resets, (int, float)):
                        found.resets_at = (
                            _dt.datetime.fromtimestamp(resets, _dt.timezone.utc)
                            .replace(microsecond=0)
                            .isoformat()
                        )

        if not message:
            return None
        found.message = message
        found.reason = _classify(message)
        return found

    def latest_failure(self, project_root: Path) -> Optional[SessionFinding]:
        if not self.home.is_dir():
            return None
        for path in self._candidates():
            if not self._belongs_to(path, project_root):
                continue
            found = self._scan(path)
            if found is not None:
                return found
        return None


class ClaudeCodeSessionReader(SessionReader):
    """Claude Code writes one JSONL transcript per session per project.

    Verified against Claude Code 2.1.263, which marks a failed turn with
    `isApiErrorMessage: true` and carries the provider's own quota state
    alongside it::

        {"type": "assistant", "isApiErrorMessage": true, "apiErrorStatus": 429,
         "error": "rate_limit", "cwd": "...",
         "quotaLimits": {"status": "rejected", "resetsAt": 1788852000,
                         "rateLimitType": "five_hour"},
         "message": {"content": [{"type": "text",
                     "text": "You've hit your session limit · resets 12:20am ..."}]}}
    """

    agent = "claude-code"

    #: `rateLimitType` values seen, as window lengths in minutes.
    WINDOWS = {"five_hour": 300, "seven_day": 7 * 24 * 60}

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else Path.home() / ".claude" / "projects"

    def _candidates(self) -> List[str]:
        files = glob.glob(str(self.home / "*" / "*.jsonl"))
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files[:MAX_FILES]

    #: A transcript opens with mode and permission records that carry no
    #: working directory; the first one that does is a few lines in.
    HEADER_LINES = 20

    @classmethod
    def _belongs_to(cls, path: str, project_root: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for _ in range(cls.HEADER_LINES):
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        cwd = json.loads(line).get("cwd")
                    except ValueError:
                        continue
                    if cwd:
                        return Path(cwd).resolve() == Path(project_root).resolve()
        except (OSError, ValueError):
            return False
        return False

    @staticmethod
    def _text(message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return " ".join(p for p in parts if p)
        return ""

    def _scan(self, path: str) -> Optional[SessionFinding]:
        if os.path.getsize(path) > MAX_BYTES:
            return None

        found: Optional[SessionFinding] = None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "isApiErrorMessage" not in line:
                    continue  # every other line of the transcript is untouched
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not record.get("isApiErrorMessage"):
                    continue

                message = self._text(record.get("message"))[:MAX_MESSAGE]
                entry = SessionFinding(
                    agent=self.agent,
                    message=message,
                    ended_at=record.get("timestamp"),
                    source=path,
                )

                quota = record.get("quotaLimits")
                if isinstance(quota, dict):
                    resets = quota.get("resetsAt")
                    if isinstance(resets, (int, float)):
                        entry.resets_at = (
                            _dt.datetime.fromtimestamp(resets, _dt.timezone.utc)
                            .replace(microsecond=0)
                            .isoformat()
                        )
                    entry.window_minutes = self.WINDOWS.get(quota.get("rateLimitType"))
                    # "rejected" means the window is spent, not that one request
                    # was throttled -- a distinction worth hours of cooldown.
                    if quota.get("status") == "rejected":
                        entry.reason = "quota_exhausted"

                entry.reason = entry.reason or _classify(message)
                status = record.get("apiErrorStatus")
                if entry.reason is None and isinstance(status, int):
                    entry.reason = "rate_limited" if status == 429 else (
                        "provider_error" if status >= 500 else None
                    )
                found = entry  # keep the last failure in the file
        return found

    def latest_failure(self, project_root: Path) -> Optional[SessionFinding]:
        if not self.home.is_dir():
            return None
        for path in self._candidates():
            if not self._belongs_to(path, project_root):
                continue
            found = self._scan(path)
            if found is not None:
                return found
        return None


#: Agents whose session format has been verified against a real failure.
READERS: Dict[str, SessionReader] = {
    "codex": CodexSessionReader(),
    "claude-code": ClaudeCodeSessionReader(),
}


def latest_failure(agent: str, project_root: Path) -> Optional[SessionFinding]:
    reader = READERS.get(agent)
    if reader is None:
        return None
    try:
        return reader.latest_failure(Path(project_root))
    except OSError:
        return None
