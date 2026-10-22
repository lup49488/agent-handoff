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
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

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

#: How long to keep reading an agent's output after it has exited. Pipes stay
#: open while any descendant holds them; past this, the supervisor moves on.
PIPE_DRAIN_SECONDS = 10.0

#: Total silence for this long means a wedged agent, not a thinking one.
#: Silence is the absence of every sign of work, not only of output: see
#: `_ActivityProbe`.
DEFAULT_STALL_TIMEOUT = 900.0

#: How often, once an agent has gone quiet, the supervisor looks for work that
#: does not print: files changing, or its process tree using the CPU.
ACTIVITY_PROBE_SECONDS = 30.0

#: CPU time, as a share of the wall time between two looks, above which an
#: agent's process tree counts as working. A long test run or build uses a
#: core; an agent waiting on an unreachable endpoint uses almost nothing.
CPU_BUSY_FRACTION = 0.05

#: The handoff files an agent writes as it works. A checkpoint is work.
_AGENT_STATE_FILES = ("state.md", "checkpoints.jsonl", "tests.json", "events.jsonl", "commands.jsonl")

#: Outside a git repository the work tree is walked directly; these are
#: skipped (the handoff directory is watched through the files above) and the
#: walk stops after this many files.
_UNWATCHED_DIRS = {".git", ".agent-handoff", "node_modules", "__pycache__", ".venv", "venv"}
_UNWATCHED_FILE_LIMIT = 10000

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


def _kill_process_tree(process) -> bool:
    """Stop a stalled agent and every child it launched before failing over.

    Returns whether the whole tree is known to be gone. False means only the
    direct child could be stopped: its descendants may still be running, may
    still hold its output pipes, and may still be changing files.
    """
    if os.name == "nt":
        # `Popen.kill()` only kills the wrapper process. Coding CLIs often
        # spawn workers, so taskkill's /T is the first choice. Sandboxed or
        # restricted Windows environments can reject taskkill; do not silently
        # leave the direct child running in that case.
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        except OSError:
            result = None
        if result is not None and result.returncode == 0:
            return True
        if process.poll() is None:
            process.kill()
        return False
    # The group exists only if the process leads one, which a launch with
    # `start_new_session` arranges. Without that, `killpg` finds no group —
    # ProcessLookupError — and reporting success there left the process
    # running while claiming its whole tree was gone.
    try:
        os.killpg(process.pid, signal.SIGKILL)
        return True
    except PermissionError:
        # macOS refuses to signal a group whose leader has exited but not yet
        # been reaped; Linux does not. The workers that leader started are
        # still in the group, so reap the leader and signal what remains. A
        # group id stays reserved while any member lives, so the retry cannot
        # reach an unrelated process.
        if process.poll() is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return True
            except ProcessLookupError:
                return True  # nothing else was left in the group
            except PermissionError:
                pass
    except (ProcessLookupError, AttributeError):
        pass
    if process.poll() is None:
        process.kill()
    return False


def _workspace_state(store: Store) -> Tuple:
    """What a silent agent may be changing: the work tree and its own checkpoints.

    Every path `git status` reports, with its size and modification time, so
    that a second edit to an already-modified file still registers; and the
    handoff files the agent itself writes. The supervisor's own logs are not
    included: they change only when the agent prints, which already counts.
    """
    entries = []
    if gitinfo.is_repo(store.root):
        _, status, _ = gitinfo.run_git(store.root, "status", "--porcelain", "-z", "--untracked-files=all")
        for item in status.split("\0"):
            path = item[3:] if len(item) > 3 else ""
            if not path or gitinfo._is_self(path):
                continue
            try:
                stat = (store.root / path).stat()
                entries.append((path, stat.st_size, stat.st_mtime_ns))
            except OSError:
                entries.append((path, None, None))
    else:
        # No git to ask: look at the files themselves, within a bound.
        seen = 0
        for directory, dirs, files in os.walk(store.root):
            dirs[:] = [d for d in dirs if d not in _UNWATCHED_DIRS]
            for name in files:
                seen += 1
                if seen > _UNWATCHED_FILE_LIMIT:
                    break
                path = Path(directory) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((str(path.relative_to(store.root)), stat.st_size, stat.st_mtime_ns))
            if seen > _UNWATCHED_FILE_LIMIT:
                break
    for name in _AGENT_STATE_FILES:
        try:
            stat = (store.dir / name).stat()
            entries.append((name, stat.st_size, stat.st_mtime_ns))
        except OSError:
            continue
    return tuple(sorted(entries, key=repr))


def _cpu_seconds_of_tree(pid: int) -> Optional[float]:
    """Total CPU time used so far by `pid` and its descendants, or None.

    The standard library cannot see other processes, so this asks the
    system: `ps` on POSIX, CIM through PowerShell on Windows. Any failure
    means no CPU evidence either way, never a verdict of idleness.
    """
    try:
        if os.name == "nt":
            script = (
                "Get-CimInstance Win32_Process | ForEach-Object { '{0} {1} {2}' -f "
                "$_.ProcessId, $_.ParentProcessId, ($_.UserModeTime + $_.KernelModeTime) }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=20, check=True,
            ).stdout
            rows = {}
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 3:
                    rows[int(parts[0])] = (int(parts[1]), int(parts[2]) / 1e7)
        else:
            out = subprocess.run(
                ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "time="],
                capture_output=True, text=True, timeout=20, check=True,
            ).stdout
            rows = {}
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 3:
                    rows[int(parts[0])] = (int(parts[1]), _ps_time(parts[2]))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if pid not in rows:
        return None
    children: Dict[int, List[int]] = {}
    for child, (parent, _) in rows.items():
        if child != parent:
            children.setdefault(parent, []).append(child)
    total, stack, seen = 0.0, [pid], set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        total += rows.get(current, (0, 0.0))[1]
        stack.extend(children.get(current, ()))
    return total


def _ps_time(text: str) -> float:
    """`ps` cumulative time: `[[dd-]hh:]mm:ss[.ss]`."""
    days, _, clock = text.rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86400 if days else 0)


class _ActivityProbe:
    """Whether a quiet agent has done any work since the last look.

    Before this, silence meant no output, and an agent printing nothing while
    it worked was killed as stalled: `claude -p` prints its answer only at the
    end, and a long test run or build prints nothing until it finishes. Work
    leaves other traces -- files change, the agent checkpoints, its process
    tree uses the CPU -- and an agent waiting on an unreachable endpoint
    leaves none of them.
    """

    def __init__(self, store: Store, pid: int) -> None:
        self.store = store
        self.pid = pid
        self.workspace = _workspace_state(store)
        self.cpu: Optional[float] = None
        self.cpu_at: Optional[float] = None
        #: Whether CPU use has been judged at all: it takes two readings, so
        #: the first look can only set the baseline. False until it has.
        self.judged = False

    def __call__(self) -> bool:
        busy = False
        workspace = _workspace_state(self.store)
        if workspace != self.workspace:
            self.workspace = workspace
            busy = True
        cpu = _cpu_seconds_of_tree(self.pid)
        now = time.monotonic()
        if cpu is None:
            self.judged = True  # no CPU evidence to wait for
        elif self.cpu is not None and self.cpu_at is not None and now > self.cpu_at:
            self.judged = True
            if cpu - self.cpu >= CPU_BUSY_FRACTION * (now - self.cpu_at):
                busy = True
        if cpu is not None:
            self.cpu, self.cpu_at = cpu, now
        return busy


def _drain(
    store: Store, process, stall_timeout: Optional[float] = None,
    activity_probe: Optional[Callable[[], bool]] = None,
) -> Tuple[int, str, str, bool]:
    """Wait for a running agent, collecting what it printed.

    An agent can also fail by not failing: an unreachable endpoint leaves it
    retrying in silence, producing nothing and never exiting. Waiting forever
    for that is the exact loss of time this tool exists to prevent, so total
    silence past `stall_timeout` is treated as an agent becoming unavailable.
    Silence is no output *and* no other sign of work (`_ActivityProbe`),
    looked for once the agent has been quiet for half the timeout.
    """
    stdout: List[str] = []
    stderr: List[str] = []
    activity = [time.monotonic()]

    def touch() -> None:
        activity[0] = time.monotonic()

    # Daemon threads: a reader still blocked on a pipe that a surviving
    # descendant holds open must not keep the supervisor alive at exit.
    readers = [
        threading.Thread(
            target=_capture,
            args=(process.stdout, store.logs_dir / "stdout.log", stdout, sys.stdout, touch),
            daemon=True,
        ),
        threading.Thread(
            target=_capture,
            args=(process.stderr, store.logs_dir / "stderr.log", stderr, sys.stderr, touch),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    stalled = False
    probe = activity_probe
    if probe is None and stall_timeout:
        try:
            probe = _ActivityProbe(store, process.pid)
        except Exception:
            probe = None
    probe_every = min(ACTIVITY_PROBE_SECONDS, stall_timeout / 2) if stall_timeout else None
    last_probe = [0.0]
    try:
        while True:
            try:
                exit_code = process.wait(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if (probe is not None and probe_every is not None
                        and now - activity[0] >= probe_every and now - last_probe[0] >= probe_every):
                    last_probe[0] = now
                    try:
                        if probe():
                            touch()
                            continue
                    except Exception:
                        pass  # a failed look is no evidence either way
                silence = time.monotonic() - activity[0]
                # Give the CPU one more look if it has not yet been judged --
                # with a short timeout the first reading can land at the
                # deadline -- but never more than one probe interval longer.
                if (stall_timeout and probe_every is not None and silence > stall_timeout
                        and not getattr(probe, "judged", True)
                        and silence <= stall_timeout + probe_every):
                    continue
                if stall_timeout and silence > stall_timeout:
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
    except BaseException:
        # Ctrl-C, or anything else that stops the supervisor. The agent runs
        # in its own session (POSIX) or process group (Windows) so that its
        # whole tree can be killed — which also means a terminal's Ctrl-C
        # never reaches it, and Windows disables Ctrl+C for a new group
        # outright. Left alone it ran on unsupervised, spending quota and
        # editing the repository, after the user had stopped `handoff run`.
        if process.poll() is None:
            _kill_process_tree(process)
        raise

    # The agent has exited, but its pipes close only when every process
    # holding them has: a worker it left running in the background, or a
    # descendant the tree kill could not reach. Waiting on those without a
    # bound held `handoff run` here forever and the fallback never started.
    # Output read so far is kept; whatever arrives later is not waited for.
    deadline = time.monotonic() + PIPE_DRAIN_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
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
