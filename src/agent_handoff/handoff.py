"""Render the handoff package (HANDOFF.md) and the resume instruction.

Nothing here calls the source agent -- by the time a handoff happens it may
already be gone. Everything is rendered from what is already on disk.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from agent_handoff import checkpoint as checkpoint_mod
from agent_handoff import gitinfo
from agent_handoff.events import last_failure
from agent_handoff.models import Task, now
from agent_handoff.store import Store, atomic_write

DIFF_BUDGET_CHARS = 20000
LOG_TAIL_LINES = 40
EVENT_TAIL = 25
COMMAND_TAIL = 15

RESUME_INSTRUCTION = """You are continuing an unfinished coding task from another agent.

Do not restart the task from scratch.

First inspect:
1. HANDOFF.md
2. current git diff
3. modified files
4. latest test results

Verify the current repository state before making further changes.

Continue the original task from the most advanced valid state available.

Where the checkpoint prose and the diff disagree, the diff is the truth.

Keep the checkpoint alive for whoever continues after you: at each meaningful
milestone, run `handoff checkpoint` with what changed."""


def _section(title: str, body: str) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    return "## " + title + "\n\n" + body + "\n"


def _fenced(body: str, lang: str = "") -> str:
    body = (body or "").rstrip()
    if not body:
        return ""
    return "```" + lang + "\n" + body + "\n```"


def _demote_headings(text: str) -> str:
    """state.md is written with h1 headings; nest it under HANDOFF's own h2."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            lines.append("##" + stripped)
        else:
            lines.append(line)
    return "\n".join(lines)


def _bullets(items: List[str]) -> str:
    return "\n".join("- " + i for i in items)


def _events_block(records: List[Dict]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)


def render(store: Store, reason: str = "manual_handoff", target_agent: Optional[str] = None) -> str:
    """Build HANDOFF.md content from the durable state on disk."""
    task = store.read_task()
    git = store.read_git()
    failure = last_failure(store)
    to_agent = target_agent or task.fallback_agent

    parts: List[str] = []
    parts.append("# HANDOFF\n")
    parts.append(
        _section(
            "Task",
            "\n".join(
                [
                    "**Original request**",
                    "",
                    "> " + task.original_request.replace("\n", "\n> "),
                    "",
                    "- task_id: `" + task.task_id + "`",
                    "- source agent: `" + task.source_agent + "`",
                    "- continuing agent: `" + to_agent + "`",
                    "- handoff reason: `" + reason + "`",
                    "- handoff #" + str(task.handoff_count + 1)
                    + ", generated at " + now(),
                ]
            ),
        )
    )

    if failure and failure.get("reason") != reason:
        parts.append(
            _section(
                "Recorded failure",
                _fenced(json.dumps(failure, ensure_ascii=False, indent=2), "json"),
            )
        )

    semantic = store.read_state_md()
    if semantic:
        stale = checkpoint_mod.staleness(store, git_head=git.head if git else None)
        body = (stale + "\n\n" if stale else "") + _demote_headings(semantic)
    else:
        body = (
            "_None recorded. Reconstruct intent from the git diff, events and "
            "test output below._"
        )
    parts.append(_section("Last semantic checkpoint", body))

    if git and git.is_repo:
        repo_lines = [
            "- branch: `" + str(git.branch) + "`",
            "- HEAD: `" + str(git.head) + "`",
            "- working tree: " + ("dirty" if git.dirty else "clean"),
            "- snapshot taken: " + git.collected_at,
        ]
        parts.append(_section("Repository state", "\n".join(repo_lines)))

        if git.changed_files:
            parts.append(_section("Files changed", _bullets(git.changed_files)))
        if git.diffstat:
            parts.append(_section("Diffstat", _fenced(git.diffstat)))
        if git.recent_commits:
            parts.append(_section("Recent commits", _fenced("\n".join(git.recent_commits))))

        patch = gitinfo.diff(store.root, max_chars=DIFF_BUDGET_CHARS)
        parts.append(
            _section(
                "Current diff",
                _fenced(patch, "diff") if patch else "_No uncommitted changes._",
            )
        )
    else:
        parts.append(_section("Repository state", "_Not a git repository._"))

    commands = store.tail_jsonl(store.commands_file, COMMAND_TAIL)
    if commands:
        parts.append(_section("Recent commands", _fenced(_events_block(commands), "json")))

    events = store.tail_jsonl(store.events_file, EVENT_TAIL)
    if events:
        parts.append(_section("Recent events", _fenced(_events_block(events), "json")))

    if store.tests_file.is_file():
        parts.append(
            _section(
                "Latest test results",
                _fenced(store.tests_file.read_text(encoding="utf-8").strip(), "json"),
            )
        )

    for label, name in (("stdout", "stdout.log"), ("stderr", "stderr.log")):
        tail = store.tail_log(name, LOG_TAIL_LINES)
        if tail:
            parts.append(_section("Last " + label, _fenced(tail)))

    parts.append(_section("Resume instructions", RESUME_INSTRUCTION))

    return "\n".join(p for p in parts if p).rstrip() + "\n"


def write_package(
    store: Store, reason: str = "manual_handoff", target_agent: Optional[str] = None
) -> str:
    """Render HANDOFF.md to disk and return its content."""
    content = render(store, reason=reason, target_agent=target_agent)
    atomic_write(store.handoff_file, content)
    return content


def bump_task(store: Store, task: Task, target_agent: str) -> None:
    task.handoff_count += 1
    task.status = "handed_off"
    task.source_agent = target_agent
    store.write_task(task)
