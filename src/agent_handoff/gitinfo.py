"""Deterministic repository state, collected by running git. No LLM tokens."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from agent_handoff import HANDOFF_DIR
from agent_handoff.models import GitState

#: The handoff layer's own bookkeeping is not part of the task's work.
SELF_PATHS = (HANDOFF_DIR + "/", HANDOFF_DIR, "HANDOFF.md")


def _is_self(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in SELF_PATHS or normalized.startswith(HANDOFF_DIR + "/")


def run_git(root: Path, *args: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a git command in `root`. Returns (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _out(root: Path, *args: str) -> str:
    """stdout of a git command, trailing whitespace only.

    Leading whitespace is significant: `git status --porcelain` encodes the
    staged/unstaged distinction in the first two columns.
    """
    code, stdout, _ = run_git(root, *args)
    return stdout.rstrip() if code == 0 else ""


def _line(root: Path, *args: str) -> str:
    return _out(root, *args).strip()


def is_repo(root: Path) -> bool:
    return _line(root, "rev-parse", "--is-inside-work-tree") == "true"


def _parse_porcelain(text: str) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Split `git status --porcelain` into modified/created/deleted/untracked."""
    modified: List[str] = []
    created: List[str] = []
    deleted: List[str] = []
    untracked: List[str] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if " -> " in path:  # rename: report the destination
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if _is_self(path):
            continue
        if code == "??":
            untracked.append(path)
        elif "D" in code:
            deleted.append(path)
        elif "A" in code or "C" in code:
            created.append(path)
        else:
            modified.append(path)
    return modified, created, deleted, untracked


def collect(root: Path) -> GitState:
    if not is_repo(root):
        return GitState(is_repo=False)

    status = _out(root, "status", "--porcelain")
    modified, created, deleted, untracked = _parse_porcelain(status)
    branch = _line(root, "rev-parse", "--abbrev-ref", "HEAD") or None
    head = _line(root, "rev-parse", "--short", "HEAD") or None
    commits = [line for line in _out(root, "log", "-5", "--oneline").splitlines() if line]

    return GitState(
        is_repo=True,
        branch=branch,
        head=head,
        dirty=bool(status.strip()),
        modified=modified,
        created=created,
        deleted=deleted,
        untracked=untracked,
        diffstat=_line(root, "diff", "--stat"),
        recent_commits=commits,
    )


def diff(root: Path, max_chars: Optional[int] = None) -> str:
    """Working-tree diff, including staged changes.

    The full diff is not copied into the handoff dir -- the next agent can
    always re-run `git diff` itself. This is only for embedding a bounded
    excerpt in HANDOFF.md.
    """
    text = _out(root, "diff", "HEAD")
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n... [diff truncated, run `git diff HEAD` for the rest]"
    return text
