"""Supervised execution, failure classification, and chain failover.

The supervisor runs one agent at a time and walks down the fallback chain as
each becomes unavailable. Every hop renders a fresh handoff package, so the
third agent inherits the second agent's work rather than the first agent's.
"""

from __future__ import annotations

import re
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from agent_handoff import gitinfo
from agent_handoff.adapters.base import AdapterError, AgentAdapter
from agent_handoff.checkpoint import CHECKPOINT_PROTOCOL
from agent_handoff.events import log_agent_unavailable, log_event
from agent_handoff.handoff import RESUME_INSTRUCTION, bump_task, write_package
from agent_handoff.health import Health
from agent_handoff.integrity import errors as integrity_errors
from agent_handoff.integrity import verify_package
from agent_handoff.lock import Lock, LockBusy
from agent_handoff.store import Store

# Patterns matched against an agent's own error output.
#
# Every string below was observed verbatim in the shipped binaries of
# Claude Code 2.1.263 and Codex CLI 0.153.4 (see tests/test_real_agents.py).
# They are regexes, not plain substrings, because real errors arrive in two
# shapes: prose ("You've hit your usage limit.") and identifiers
# ("rate_limit_error", "InternalServerError", "QuotaExceeded"). Matching only
# the spaced prose form misses over half of them.
FAILURE_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "quota_exhausted": (
        # Codex says "usage limit"; Claude Code says "session limit". Both mean
        # the plan's window is spent, and both were observed verbatim.
        r"(usage|session) limit",
        # "quota exceeded", "The quota has been exceeded.", "QuotaExceeded" --
        # but not the OS-level "disk quota exceeded" or "SystemFdQuotaExceeded",
        # which no other agent can help with.
        r"(?<!disk )\bquota[\w ]{0,20}(exceeded|exhausted)",
        r"out of quota",
        r"credit balance",
        r"token budget",
    ),
    "rate_limited": (
        r"rate[ _-]?limit",  # "rate limit", "rate_limit_error", "rate-limit-reset"
        r"too many requests",
        r"\b429\b",  # the same test Codex itself uses internally
    ),
    "context_exhausted": (
        r"context[ _-]?window",
        r"context[ _-]?length",
        r"maximum context",
    ),
    "provider_error": (
        r"overloaded",  # Anthropic's "overloaded_error"
        r"internal ?server ?error",  # Codex's "InternalServerError"
        r"provider error",
        r"api error",
        r"service unavailable",
        r"bad gateway",
    ),
    "network_failure": (
        r"connection ?(failed|refused|reset|closed|error)",  # "ConnectionFailed"
        r"network ?(error|is unreachable)",
        r"timed out",
        r"e(conn(refused|reset)|timedout|notfound|hostunreach)",
    ),
}

_COMPILED = {
    reason: tuple(re.compile(pattern) for pattern in patterns)
    for reason, patterns in FAILURE_PATTERNS.items()
}


def classify_failure(stdout: str, stderr: str, exit_code: int) -> Optional[str]:
    """Classify known provider failures; other nonzero exits are crashes.

    Only a nonzero exit is a failure. An agent that finished cleanly may still
    have printed "rate limit" or "timed out" -- it reads code and test output
    for a living -- and matching that would trigger a handoff for a task that
    is already done.
    """
    if exit_code == 0:
        return None
    text = (stdout + "\n" + stderr).lower()
    for reason, patterns in _COMPILED.items():
        if any(pattern.search(text) for pattern in patterns):
            return reason
    return "process_crashed"


def initial_prompt(request: str, with_protocol: bool = True) -> str:
    """The task, plus the checkpoint protocol the agent is asked to follow."""
    if not with_protocol:
        return request
    return request + "\n\n---\n\n" + CHECKPOINT_PROTOCOL


#: How often the supervisor checks on a running agent.
POLL_SECONDS = 1.0

#: Total silence for this long means a wedged agent, not a thinking one.
DEFAULT_STALL_TIMEOUT = 900.0

# Checkpoint writes normally hold the ordinary lock for milliseconds. The
# supervisor must not hold it while the agent works, but must coordinate each
# of its own state transitions with those checkpoint writes.
WRITE_LOCK_WAIT_SECONDS = 5.0


@contextmanager
def _state_lock(store: Store):
    """Acquire the ordinary write lock briefly, waiting for a checkpoint."""
    deadline = time.monotonic() + WRITE_LOCK_WAIT_SECONDS
    while True:
        held = Lock(store.lock_file, "run-state")
        try:
            held.acquire()
            break
        except LockBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        yield
    finally:
        held.release()


def _capture(stream, path: Path, collected: List[str], echo, touch=None) -> None:
    """Tee one stream to its log file and to the console.

    Line by line and flushed: a supervised agent that gets killed has already
    left everything it printed on disk.
    """
    if stream is None:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for line in iter(stream.readline, ""):
            handle.write(line)
            handle.flush()
            collected.append(line)
            if touch is not None:
                touch()
            if echo is not None:
                echo.write(line)
                echo.flush()
    stream.close()


def _kill_process_tree(process) -> None:
    """Stop a stalled agent and every child it launched before failing over."""
    if os.name == "nt":
        # `Popen.kill()` only kills the wrapper process. Coding CLIs often
        # spawn workers, so taskkill's /T is required before the fallback runs.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        process.kill()


def _drain(
    store: Store, process, stall_timeout: Optional[float] = None
) -> Tuple[int, str, str, bool]:
    """Wait for a running agent, collecting what it printed.

    An agent can also fail by not failing: an unreachable endpoint leaves it
    retrying in silence, producing nothing and never exiting. Waiting forever
    for that is the exact loss of time this tool exists to prevent, so total
    silence past `stall_timeout` is treated as an agent becoming unavailable.
    """
    stdout: List[str] = []
    stderr: List[str] = []
    activity = [time.monotonic()]

    def touch() -> None:
        activity[0] = time.monotonic()

    readers = [
        threading.Thread(
            target=_capture,
            args=(process.stdout, store.logs_dir / "stdout.log", stdout, sys.stdout, touch),
        ),
        threading.Thread(
            target=_capture,
            args=(process.stderr, store.logs_dir / "stderr.log", stderr, sys.stderr, touch),
        ),
    ]
    for reader in readers:
        reader.start()

    stalled = False
    while True:
        try:
            exit_code = process.wait(timeout=POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if stall_timeout and (time.monotonic() - activity[0]) > stall_timeout:
                stalled = True
                _kill_process_tree(process)
                # Process-tree tools can fail or return before the direct
                # child observes termination. Never let the recovery path
                # itself wait forever; the direct child is the final fallback.
                try:
                    exit_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait()
                break

    for reader in readers:
        reader.join()
    return exit_code, "".join(stdout), "".join(stderr), stalled


def _classify(adapter: AgentAdapter, stdout: str, stderr: str, exit_code: int) -> Optional[str]:
    """An adapter's own reading of a failure wins over the shared patterns."""
    specific = adapter.detect_failure(stdout, stderr, exit_code)
    return specific if specific is not None else classify_failure(stdout, stderr, exit_code)


def run(
    store: Store,
    primary: AgentAdapter,
    fallbacks: Union[AgentAdapter, Sequence[AgentAdapter]],
    *,
    checkpoint_protocol: bool = True,
    stall_timeout: Optional[float] = DEFAULT_STALL_TIMEOUT,
    health: Optional[Health] = None,
    cooldowns: Optional[Dict[str, int]] = None,
) -> int:
    """Run ``primary``, handing off down ``fallbacks`` as agents become unavailable."""
    chain = [fallbacks] if isinstance(fallbacks, AgentAdapter) else list(fallbacks)

    task = store.read_task()
    if task.status != "running":
        raise RuntimeError("task is " + task.status + " -- initialize a new task before running it")
    if primary.name != task.source_agent:
        raise RuntimeError("primary agent does not match task source agent")
    if not chain:
        raise RuntimeError("no fallback agents configured -- nothing to hand off to")
    if any(adapter.name == primary.name for adapter in chain):
        raise RuntimeError("'" + primary.name + "' cannot be its own fallback")

    health = Health.open() if health is None else health

    attempted: List[str] = []
    current = primary
    process = current.start(initial_prompt(task.original_request, checkpoint_protocol), capture_output=True)
    with _state_lock(store):
        log_event(store, "agent_started", agent=current.name)

    while True:
        exit_code, stdout, stderr, stalled = _drain(store, process, stall_timeout)
        reason = "agent_stalled" if stalled else _classify(current, stdout, stderr, exit_code)

        if reason is None:
            with _state_lock(store):
                task.status = "done"
                store.write_task(task)
                log_event(store, "agent_completed", agent=current.name, exit_code=exit_code)
            # Per-user state, not the project's: written outside the store lock.
            health.record_success(current.name)
            return exit_code

        health.record_failure(current.name, reason, cooldowns)
        with _state_lock(store):
            attempted.append(current.name)
            task.attempted = list(attempted)
            store.write_git(gitinfo.collect(store.root))
            log_agent_unavailable(store, current.name, reason)

            # Chain order, with agents believed to be out of action moved to
            # the back. Nothing is dropped -- see health.py.
            remaining = [adapter for adapter in chain if adapter.name not in attempted]
            preferred = health.order([adapter.name for adapter in remaining])
            candidates = sorted(remaining, key=lambda a: preferred.index(a.name))
            for name in health.skipped([a.name for a in candidates]):
                log_event(
                    store,
                    "agent_deprioritised",
                    agent=name,
                    detail=health.get(name).describe(),
                )

            write_package(
                store, reason=reason, target_agent=candidates[0].name if candidates else None
            )
            task.status = "handoff_prepared"
            store.write_task(task)

            # Launching an agent on a damaged package is worse than not handing
            # off: it looks busy while working from the wrong state.
            broken = integrity_errors(verify_package(store))
        if broken:
            with _state_lock(store):
                for finding in broken:
                    log_event(store, "handoff_package_invalid", detail=finding.message)
            raise RuntimeError(
                "refusing to hand off: " + "; ".join(f.message for f in broken)
            )

        # An agent that will not start has not taken over anything, so keep
        # walking the chain and only give up once nothing is left.
        for candidate in candidates:
            try:
                process = candidate.start(RESUME_INSTRUCTION, capture_output=True)
            except AdapterError as exc:
                with _state_lock(store):
                    log_event(
                        store,
                        "handoff_launch_failed",
                        from_agent=current.name,
                        to=candidate.name,
                        error=str(exc),
                    )
                    attempted.append(candidate.name)
                    task.attempted = list(attempted)
                    store.write_task(task)
                continue
            break
        else:
            with _state_lock(store):
                log_event(
                    store,
                    "handoff_exhausted",
                    from_agent=current.name,
                    reason=reason,
                    tried=", ".join(attempted),
                )
            return exit_code

        # The process is running -- only now is the handoff a fact.
        with _state_lock(store):
            bump_task(store, task, candidate.name)
            log_event(store, "handoff_started", from_agent=current.name, to=candidate.name)
        current = candidate
