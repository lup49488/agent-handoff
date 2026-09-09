"""`handoff` CLI.

    handoff init "Fix the auth refresh bug"
    handoff snapshot
    handoff event file_modified path=src/auth.py
    handoff command "pytest tests/test_auth.py" --exit-code 1
    handoff checkpoint --next "Move persistence inside the refresh lock"
    handoff codex --to claude          # == handoff switch --from codex --to claude
    handoff run                        # primary and chain come from config
    handoff agents                     # who can be handed to, and how
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from agent_handoff import __version__, gitinfo, handoff as handoff_pkg
from agent_handoff import checkpoint as checkpoint_mod
from agent_handoff import config as config_mod
from agent_handoff import health as health_mod
from agent_handoff import integrity as integrity_mod
from agent_handoff import lock as lock_mod
from agent_handoff import sessions as sessions_mod
from agent_handoff.adapters import AdapterError
from agent_handoff.config import ConfigError
from agent_handoff.adapters import registry
from agent_handoff.events import (
    FAILURE_REASONS,
    log_agent_unavailable,
    log_command,
    log_event,
    recent,
)
from agent_handoff.models import Task
from agent_handoff.models import now as models_now
from agent_handoff.runner import run as supervised_run
from agent_handoff.store import Store, StoreNotInitialized, atomic_write, find_root


def _echo(msg: str = "") -> None:
    print(msg)


def _open_store() -> Store:
    return Store.open(Path.cwd())


#: How long to wait for a write lock someone else is holding for a moment.
#: Long enough to ride out another command's write, short enough that a lock
#: held by a real session still reports rather than hanging.
LOCK_WAIT_SECONDS = 5.0

#: How much of a test run's output to keep in the handoff package.
TEST_OUTPUT_LINES = 30


@contextlib.contextmanager
def _lock(path: Path, command: str, wait: float = LOCK_WAIT_SECONDS):
    """Hold a lock for the duration of a block, releasing it on any exit.

    Brief contention is normal -- an agent recording a checkpoint can overlap
    with the handoff that launched it -- so a busy lock is waited on for a
    moment before it is reported as busy.
    """
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        held = lock_mod.Lock(path, command=command)
        try:
            held.acquire()
            break
        except lock_mod.LockBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    if held.broke is not None:
        _echo("note: broke a stale lock left by " + held.broke.describe())
    try:
        yield held
    finally:
        held.release()


#: Commands that only read. They must never block or be blocked.
#: `health` is per-user state, so it neither takes nor waits for a project lock.
READ_ONLY = {"status", "verify", "agents", "protocol", "health", "sessions"}


def _wants_lock(args: argparse.Namespace) -> bool:
    if args.command in READ_ONLY:
        return False
    # ``run`` and ``switch`` own run.lock while the agent they started works,
    # and take the write lock only around their own writes -- so the agent can
    # record checkpoints while it runs.
    if args.command in ("run", "switch"):
        return False
    # A force recovery is explicitly allowed to break a live ordinary lock.
    if args.command == "recover" and args.break_lock:
        return False
    if args.command == "checkpoint" and args.show:
        return False
    if args.command == "config" and not args.init:
        return False
    return True


# -- commands --------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    existing = find_root(root)
    store = Store(existing or root)
    if store.exists and not args.force:
        _echo("already initialized at " + str(store.dir))
        _echo("use --force to start a new task in the same directory")
        return 1

    cfg = config_mod.load(store.root)
    source = registry.canonical_name(args.source or cfg.primary)
    fallback = registry.canonical_name(args.fallback) if args.fallback else cfg.next_after(source)
    if fallback is None:
        _echo("no fallback agent for '" + source + "' -- pass --to, or configure a chain")
        return 2

    task = Task(
        original_request=args.request,
        source_agent=source,
        fallback_agent=fallback,
    )
    archived = store.archive_current() if store.exists else None
    store.init(task)
    if archived is not None:
        _echo("archived the previous task to " + str(archived))
    log_event(store, "task_started", task_id=task.task_id, agent=task.source_agent)

    git = gitinfo.collect(store.root)
    store.write_git(git)

    _echo("initialized " + str(store.dir))
    _echo("  task_id : " + task.task_id)
    _echo("  source  : " + task.source_agent)
    _echo("  fallback: " + task.fallback_agent)
    _echo("  chain   : " + " -> ".join(cfg.chain))
    if not git.is_repo:
        _echo("  note    : not a git repository -- deterministic state will be thin")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    store = _open_store()
    git = gitinfo.collect(store.root)
    store.write_git(git)
    log_event(
        store,
        "snapshot",
        branch=git.branch,
        head=git.head,
        changed=len(git.changed_files),
    )
    _echo("snapshot: " + str(len(git.changed_files)) + " changed file(s) on " + str(git.branch))
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    store = _open_store()
    fields = {}
    for pair in args.fields:
        if "=" not in pair:
            _echo("bad field '" + pair + "' -- expected key=value")
            return 2
        key, value = pair.split("=", 1)
        fields[key] = value
    record = log_event(store, args.name, **fields)
    _echo(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_command(args: argparse.Namespace) -> int:
    store = _open_store()
    log_command(store, args.cmd, exit_code=args.exit_code, note=args.note or "")
    _echo("recorded: " + args.cmd)
    return 0


def cmd_tests(args: argparse.Namespace) -> int:
    """Record the latest test result.

    Test status is the one piece of deterministic state that a supervised run
    cannot infer and a git diff does not carry: whether the work in progress
    currently passes. `HANDOFF.md` has always had a section for it -- this is
    what fills it in.
    """
    store = _open_store()

    output = ""
    if args.from_file == "-":
        output = sys.stdin.read()
    elif args.from_file:
        output = Path(args.from_file).read_text(encoding="utf-8", errors="replace")

    record = {
        "ts": models_now(),
        "cmd": args.cmd,
        "exit_code": args.exit_code,
        "passed": args.exit_code == 0,
    }
    if args.summary:
        record["summary"] = args.summary
    if output.strip():
        lines = output.strip().splitlines()
        record["output_tail"] = "\n".join(lines[-TEST_OUTPUT_LINES:])

    atomic_write(store.tests_file, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    log_command(store, args.cmd, exit_code=args.exit_code, note="tests")
    log_event(store, "tests_recorded", cmd=args.cmd, exit_code=args.exit_code)
    _echo("recorded: " + args.cmd + " -> " + ("passed" if record["passed"] else "failed"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = _open_store()
    task = store.read_task()
    git = store.read_git()
    _echo("root     : " + str(store.root))
    _echo("task_id  : " + task.task_id + "  (" + task.status + ")")
    _echo("request  : " + task.original_request)
    _echo("source   : " + task.source_agent + "   fallback: " + task.fallback_agent)
    _echo("handoffs : " + str(task.handoff_count))
    if git and git.is_repo:
        _echo(
            "git      : "
            + str(git.branch)
            + "@"
            + str(git.head)
            + ("  dirty" if git.dirty else "  clean")
            + "  ("
            + str(len(git.changed_files))
            + " changed)"
        )
    else:
        _echo("git      : not a repository (or no snapshot yet)")
    _echo("checkpt  : " + checkpoint_mod.summary_line(store))
    if store.tests_file.is_file():
        try:
            tests = json.loads(store.tests_file.read_text(encoding="utf-8"))
            _echo(
                "tests    : "
                + ("passed" if tests.get("passed") else "FAILED")
                + "  "
                + str(tests.get("cmd", ""))
                + ("  (" + tests["summary"] + ")" if tests.get("summary") else "")
            )
        except ValueError:
            _echo("tests    : unreadable tests.json")
    events = recent(store, 5)
    if events:
        _echo("recent events:")
        for record in events:
            _echo("  " + json.dumps(record, ensure_ascii=False))
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    store = _open_store()
    config_mod.load(store.root)  # installs any [agents.*] overlay
    if not args.no_snapshot:
        store.write_git(gitinfo.collect(store.root))
    target = registry.canonical_name(args.to) if args.to else None
    handoff_pkg.write_package(store, reason=args.reason, target_agent=target)
    _echo("wrote " + str(store.handoff_file))
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    store = _open_store()
    task = store.read_task()

    cfg = config_mod.load(store.root)
    source = registry.canonical_name(args.source) if args.source else task.source_agent
    if args.to:
        target = registry.canonical_name(args.to)
    else:
        book = health_mod.Health.open()
        target = cfg.next_after(source, exclude=task.attempted)
        if target is None:
            # Nothing left *after* this agent -- but an agent that ran out
            # earlier in the task may have recovered since. Its cooldown
            # expiring is exactly the signal that it is worth trying again.
            recovered = [
                name
                for name in cfg.chain
                if name != source
                and name in task.attempted
                # Known to have failed *and* known to be past it. An agent we
                # have no health record for has not been observed recovering.
                and name in book.records
                and not book.cooling(name)
            ]
            target = recovered[0] if recovered else None
            if target is not None:
                _echo("chain exhausted, but " + target + " has recovered since it ran out")
        if target is None:
            _echo("no agent left in the chain after " + source + " -- pass --to explicitly")
            _echo("  chain: " + " -> ".join(cfg.chain))
            waiting = [
                name + " (" + book.get(name).describe() + ")"
                for name in cfg.chain
                if book.cooling(name)
            ]
            if waiting:
                _echo("  waiting on: " + "; ".join(waiting))
            return 2
    if target in task.attempted:
        # Handing work back to an agent that already ran out usually just
        # repeats the failure, so say so -- but the user may know better.
        _echo("note: " + target + " already ran out on this task once")
    if target == source:
        _echo("source and target are both " + target + " -- nothing to hand off")
        # `handoff codex ...` names the agent to hand off *from*. Reading it as
        # "hand off to codex" is the natural mistake, so answer it here.
        _echo("  the task records " + task.source_agent + " as the agent doing the work")
        _echo("  to hand the work TO " + target + ", run: handoff switch --to " + target)
        _echo("  if " + target + " was never the one working, run: handoff switch --from <agent> --to " + target)
        return 2

    adapter = registry.get(target, store.root)
    instruction = handoff_pkg.RESUME_INSTRUCTION

    # A dry run must not touch the journal: an `agent_unavailable` event that
    # never happened would mislead whoever reads the handoff later.
    if args.dry_run:
        _echo("dry run -- would hand " + source + " -> " + target + " and launch:")
        _echo("  " + " ".join(adapter.resume_argv(instruction)))
        return 0

    if not args.no_launch and not adapter.is_available():
        _echo("error: " + adapter.executable + " not found on PATH -- package was not changed")
        return 1

    # Everything that writes happens under the write lock, held only for as
    # long as the writing takes.
    with _lock(store.lock_file, "switch"):
        # 1. freeze the deterministic state
        store.write_git(gitinfo.collect(store.root))
        # 2. record why we are switching -- for this task, and for the agent
        # An interactive session leaves no output for us to classify, but the
        # agent recorded its own ending. Prefer that over the default reason.
        found = None
        if cfg.read_agent_sessions and args.reason == "manual_handoff":
            found = sessions_mod.latest_failure(source, store.root)
        reason = args.reason
        if found is not None and found.reason:
            reason = found.reason
            _echo("from " + source + "'s own session log: " + found.describe())

        log_agent_unavailable(store, source, reason)
        if cfg.health:
            health_mod.Health.open().record_failure(
                source,
                reason,
                cfg.cooldowns,
                retry_at=found.resets_at if found is not None else None,
            )
        # A manual handoff means this agent ran out on this task, just as a
        # supervised one does. Without recording that, the chain cannot later
        # tell that it has recovered and offer it again.
        if source not in task.attempted:
            task.attempted = list(task.attempted) + [source]
            store.write_task(task)
        # 3. render the package
        handoff_pkg.write_package(store, reason=reason, target_agent=target)
        _echo("wrote " + str(store.handoff_file))

        broken = integrity_mod.errors(integrity_mod.verify_package(store))
        if broken:
            for finding in broken:
                _echo("error: " + finding.message)
            _echo("refusing to launch " + target + " on a package that is not usable")
            return 1

        if args.no_launch:
            log_event(store, "handoff_prepared", from_agent=source, to=target, launched=False)
            task.status = "handoff_prepared"
            store.write_task(task)
            _echo("not launching " + target + " (--no-launch). Start it with:")
            _echo("  " + " ".join(adapter.resume_argv(instruction)))
            return 0

        # Popen returning is the point at which the fallback is truly running.
        process = adapter.start(instruction)
        handoff_pkg.bump_task(store, task, target)
        log_event(store, "handoff_started", from_agent=source, to=target)

    # The write lock is released before waiting: the agent that just took over
    # is asked to record its own checkpoints, and must not be blocked by the
    # handoff that launched it. `run.lock` marks the session instead.
    _echo("launching " + target + " ...")
    with _lock(store.run_lock_file, "switch:" + target, wait=0):
        code = process.wait()
    with _lock(store.lock_file, "switch"):
        log_event(store, "handoff_agent_exited", agent=target, exit_code=code)
    return code


def cmd_run(args: argparse.Namespace) -> int:
    store = _open_store()
    cfg = config_mod.load(store.root)

    primary_name = registry.canonical_name(args.agent or cfg.primary)
    if args.fallback:
        chain_names = [registry.canonical_name(name) for name in args.fallback]
    else:
        chain_names = [name for name in cfg.chain if name != primary_name]
    if not chain_names:
        _echo("no fallback agents for '" + primary_name + "' -- pass --fallback, or configure a chain")
        return 2

    book = health_mod.Health.open() if cfg.health else _no_health()
    configured = [registry.get(name, store.root) for name in [primary_name, *chain_names]]
    ordered_names = book.order([adapter.name for adapter in configured])
    ordered = sorted(configured, key=lambda adapter: ordered_names.index(adapter.name))
    available = [adapter for adapter in ordered if adapter.is_available()]
    for adapter in ordered:
        if adapter not in available:
            _echo("note: " + adapter.name + " is not installed -- skipping it in the chain")
    if not available:
        _echo(
            "error: no configured agent is installed -- "
            + configured[0].executable
            + " not found on PATH; run was not started"
        )
        return 1
    primary, chain = available[0], available[1:]
    if not chain:
        _echo("error: no fallback agent is installed -- run was not started")
        return 1

    # Health-aware selection applies to the first launch too. Otherwise a new
    # task still burns a round-trip on the configured primary we already know
    # is cooling down, defeating the purpose of global health state.
    if primary.name != primary_name:
        with _lock(store.lock_file, "select-agent"):
            task = store.read_task()
            if task.status != "running":
                _echo("error: task is " + task.status + " -- initialize a new task before running it")
                return 1
            task.source_agent = primary.name
            task.fallback_agent = chain[0].name
            store.write_task(task)
            detail = (
                book.get(primary_name).describe()
                if book.cooling(primary_name)
                else "primary executable unavailable"
            )
            log_event(store, "agent_deprioritised", agent=primary_name, detail=detail)
            log_event(store, "agent_selected", agent=primary.name, reason="health_or_availability")
        _echo("starting " + primary.name + " instead of " + primary_name + " (" + detail + ")")

    _echo("chain: " + " -> ".join([primary.name] + [a.name for a in chain]))
    protocol = cfg.checkpoint_protocol and not args.no_checkpoint_protocol
    try:
        with _lock(store.run_lock_file, "run"):
            code = supervised_run(
                store,
                primary,
                chain,
                checkpoint_protocol=protocol,
                stall_timeout=cfg.stall_timeout_seconds or None,
                health=book,
                cooldowns=cfg.cooldowns,
            )
    except (RuntimeError, AdapterError) as exc:
        _echo("error: " + str(exc))
        return 1

    if store.read_task().status == "handoff_prepared":
        _echo("chain exhausted -- " + str(store.handoff_file) + " is ready for whoever picks it up")
    return code


def cmd_checkpoint(args: argparse.Namespace) -> int:
    store = _open_store()

    if args.show:
        current = store.read_state_md()
        _echo(current if current else "no checkpoint recorded yet")
        return 0

    if args.from_file:
        text = sys.stdin.read() if args.from_file == "-" else Path(args.from_file).read_text(
            encoding="utf-8"
        )
        entry = checkpoint_mod.Checkpoint.parse(text)
        if entry.is_empty():
            _echo("nothing recognisable in the input -- expected headings such as:")
            _echo("  " + ", ".join(checkpoint_mod.SECTION_ORDER))
            return 2
    else:
        entry = checkpoint_mod.update(
            store,
            objective=args.objective,
            done=args.done,
            decisions=args.decision,
            problem=args.problem,
            next_step=args.next_step,
            replace=args.replace,
        )
        if entry.is_empty():
            _echo("nothing to record -- pass at least one of --objective/--done/"
                  "--decision/--problem/--next")
            return 2

    git = gitinfo.collect(store.root)
    record = checkpoint_mod.write(store, entry, git_head=git.head)
    _echo("checkpoint written (~" + str(record["tokens"]) + " tokens)")
    if entry.over_budget():
        _echo(
            "warning: over the "
            + str(checkpoint_mod.TOKEN_BUDGET)
            + "-token guideline -- record decisions and next steps, not a "
            "conversation summary"
        )
    return 0


def cmd_protocol(args: argparse.Namespace) -> int:
    """Print the instruction to give an agent so it maintains the checkpoint."""
    _echo(checkpoint_mod.CHECKPOINT_PROTOCOL)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    # Config is a property of the project, not of a task: it is readable and
    # writable before anything has been initialized here.
    root = find_root(Path.cwd()) or Path.cwd()
    if args.init:
        path = config_mod.config_path(root)
        if path and not args.force:
            _echo("config already exists at " + str(path) + " -- use --force to overwrite")
            return 1
        _echo("wrote " + str(config_mod.write_starter(root)))
        return 0
    _echo(config_mod.load(root).describe())
    return 0


def _no_health() -> "health_mod.Health":
    """A no-op health view, for `health = false`."""
    return health_mod.DisabledHealth()


def cmd_health(args: argparse.Namespace) -> int:
    book = health_mod.Health.open()

    if args.refresh:
        # Health is written when a handoff happens. An agent that stopped on
        # its own leaves the record stale until then -- but it also wrote down
        # what happened, so ask it.
        root = find_root(Path.cwd())
        if root is None:
            _echo("no project here -- run this inside a repository")
            return 1
        cfg = config_mod.load(root)
        if not cfg.read_agent_sessions:
            _echo("read_agent_sessions is off; nothing to refresh from")
            return 0
        updated = 0
        for name in cfg.chain:
            found = sessions_mod.latest_failure(name, root)
            if found is None or not found.reason:
                continue
            book.record_failure(name, found.reason, cfg.cooldowns, retry_at=found.resets_at)
            _echo("updated " + name + " from its own log: " + found.describe())
            updated += 1
        if not updated:
            _echo("nothing to update -- no recorded failures for this project")
        book = health_mod.Health.open()

    if args.clear is not None:
        agent = registry.canonical_name(args.clear) if args.clear else None
        book.clear(agent)
        _echo("cleared health for " + (agent or "every agent"))
        return 0

    names = sorted(set(list(book.records) + registry.agent_names()))
    width = max(len(n) for n in names)
    for name in names:
        record = book.get(name)
        mark = "-- " if record.is_cooling() else "ok "
        _echo(mark + name.ljust(width) + "  " + record.describe())
    _echo("")
    _echo("from " + str(book.path))
    _echo("Cooldowns are guesses: a deprioritised agent is still tried if nothing else is left.")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    """Show what an agent's own log says -- and exactly what was read."""
    store = _open_store()
    cfg = config_mod.load(store.root)
    if not cfg.read_agent_sessions:
        _echo("read_agent_sessions is off in " + str(cfg.path or "the defaults"))
        return 0

    names = [registry.canonical_name(args.agent)] if args.agent else cfg.chain
    anything = False
    for name in names:
        found = sessions_mod.latest_failure(name, store.root)
        if found is None:
            _echo("-- " + name + ": nothing recorded for this project")
            continue
        anything = True
        _echo("ok " + found.describe())
        _echo("   message : " + found.message.splitlines()[0][:100])
        if found.resets_at:
            _echo("   resets  : " + found.resets_at)
        _echo("   from    : " + found.source)
    if anything:
        _echo("")
        _echo("Only the failure message and rate-limit telemetry are read from these files.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    store = _open_store()
    findings = integrity_mod.inspect(store)
    if not findings:
        _echo("ok: nothing wrong with " + str(store.dir))
        return 0
    for finding in findings:
        _echo(finding.render())
    return 1 if integrity_mod.errors(findings) else 0


def cmd_recover(args: argparse.Namespace) -> int:
    store = _open_store()
    actions = integrity_mod.recover(store, break_lock=args.break_lock, dry_run=args.dry_run)
    if not actions:
        _echo("nothing to recover")
        return 0
    for action in actions:
        _echo(("would " if args.dry_run else "") + action)
    if not args.dry_run:
        log_event(store, "recovered", actions=len(actions))
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    """List every agent and the command line it will actually be started with."""
    root = find_root(Path.cwd()) or Path.cwd()
    try:
        cfg = config_mod.load(root)
        chain = cfg.chain
    except ConfigError as exc:
        _echo("warning: config not usable (" + str(exc) + ") -- showing built-ins")
        registry.reset()
        chain = []

    width = max(len(name) for name in registry.agent_names())
    for name in registry.agent_names():
        adapter = registry.get(name, root)
        spec = adapter.spec
        marks = "ok " if adapter.is_available() else "-- "
        position = str(chain.index(name) + 1) + "." if name in chain else "  "
        _echo(marks + position + " " + name.ljust(width) + "  " + spec.display(interactive=False))
        if args.verbose:
            _echo(" " * (width + 8) + "interactive: " + spec.display(interactive=True))
            details = [spec.source]
            details.append("verified against " + spec.verified if spec.verified else "unverified")
            if spec.aliases:
                details.append("aliases: " + ", ".join(spec.aliases))
            if spec.note:
                details.append(spec.note)
            _echo(" " * (width + 8) + "(" + "; ".join(details) + ")")

    if not args.verbose:
        _echo("")
        _echo("`ok` = installed, `1.` = position in the chain. --verbose for details.")
        unverified = [n for n, sp in registry.specs().items() if not sp.verified]
        if unverified:
            _echo("Unverified command lines: " + ", ".join(sorted(unverified)) + ".")
        _echo("Wrong command line for your version? Override it under [agents.<name>].")
    return 0


# -- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handoff",
        description="Failover for coding agents. Hand unfinished work to another agent.",
    )
    parser.add_argument("--version", action="version", version="agent-handoff " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="start tracking a task in this directory")
    p_init.add_argument("request", help="the original task, in the user's words")
    p_init.add_argument("--source", "--from", default=None, help="agent doing the work")
    p_init.add_argument("--fallback", "--to", default=None, help="agent to hand off to")
    p_init.add_argument("--root", default=None, help="project root (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="re-init over existing state")
    p_init.set_defaults(func=cmd_init)

    p_snap = sub.add_parser("snapshot", help="refresh git.json from the working tree")
    p_snap.set_defaults(func=cmd_snapshot)

    p_event = sub.add_parser("event", help="append an event to the journal")
    p_event.add_argument("name", help="event name, e.g. file_modified")
    p_event.add_argument("fields", nargs="*", help="key=value pairs")
    p_event.set_defaults(func=cmd_event)

    p_cmd = sub.add_parser("command", help="record a command that was run")
    p_cmd.add_argument("cmd")
    p_cmd.add_argument("--exit-code", type=int, default=None)
    p_cmd.add_argument("--note", default="")
    p_cmd.set_defaults(func=cmd_command)

    p_tests = sub.add_parser("tests", help="record the latest test result")
    p_tests.add_argument("cmd", help="the test command that was run")
    p_tests.add_argument("--exit-code", type=int, required=True)
    p_tests.add_argument("--summary", default="", help='e.g. "3 failed, 12 passed"')
    p_tests.add_argument(
        "--from-file", default=None, metavar="PATH", help="test output to keep the tail of, or -"
    )
    p_tests.set_defaults(func=cmd_tests)

    p_status = sub.add_parser("status", help="show what has been recorded so far")
    p_status.set_defaults(func=cmd_status)

    p_pack = sub.add_parser("pack", help="render HANDOFF.md without launching anything")
    p_pack.add_argument("--to", default=None, help="agent the package is addressed to")
    p_pack.add_argument("--reason", default="manual_handoff", choices=FAILURE_REASONS)
    p_pack.add_argument("--no-snapshot", action="store_true", help="reuse existing git.json")
    p_pack.set_defaults(func=cmd_pack)

    p_switch = sub.add_parser("switch", help="hand the task to another agent and launch it")
    p_switch.add_argument("--from", dest="source", default=None)
    p_switch.add_argument("--to", default=None, help="target agent (default: next in the chain)")
    p_switch.add_argument("--reason", default="manual_handoff", choices=FAILURE_REASONS)
    p_switch.add_argument("--dry-run", action="store_true", help="print the launch command only")
    p_switch.add_argument(
        "--no-launch", action="store_true", help="write the package, leave launching to the user"
    )
    p_switch.set_defaults(func=cmd_switch)

    p_run = sub.add_parser("run", help="supervise an agent and auto-handoff on failure")
    p_run.add_argument("agent", nargs="?", default=None, help="primary agent (default: configured)")
    p_run.add_argument(
        "--fallback",
        action="append",
        default=[],
        help="agent to fall back to; repeat for a chain (default: configured)",
    )
    p_run.add_argument(
        "--no-checkpoint-protocol",
        action="store_true",
        help="do not ask the agent to maintain a semantic checkpoint",
    )
    p_run.set_defaults(func=cmd_run)

    p_ckpt = sub.add_parser("checkpoint", help="record why the work is where it is")
    p_ckpt.add_argument("--objective", default=None, help="what is being solved right now")
    p_ckpt.add_argument("--done", action="append", default=[], help="a completed step (repeatable)")
    p_ckpt.add_argument(
        "--decision", action="append", default=[], help="a decision worth keeping (repeatable)"
    )
    p_ckpt.add_argument("--problem", default=None, help="what is currently blocking")
    p_ckpt.add_argument("--next", dest="next_step", default=None, help="the next step to take")
    p_ckpt.add_argument(
        "--replace", action="store_true", help="start from empty instead of patching the existing one"
    )
    p_ckpt.add_argument(
        "--from-file", default=None, metavar="PATH", help="read markdown from a file, or - for stdin"
    )
    p_ckpt.add_argument("--show", action="store_true", help="print the current checkpoint and exit")
    p_ckpt.set_defaults(func=cmd_checkpoint)

    p_proto = sub.add_parser(
        "protocol", help="print the checkpoint instruction to give an agent"
    )
    p_proto.set_defaults(func=cmd_protocol)

    p_health = sub.add_parser("health", help="which agents are believed to be out of action")
    p_health.add_argument(
        "--refresh",
        action="store_true",
        help="update from the agents' own session logs before reporting",
    )
    p_health.add_argument(
        "--clear",
        nargs="?",
        const="",
        default=None,
        metavar="AGENT",
        help="forget one agent's health, or all of it",
    )
    p_health.set_defaults(func=cmd_health, needs_lock=False)

    p_sessions = sub.add_parser(
        "sessions", help="what an agent's own log says about how it stopped"
    )
    p_sessions.add_argument("agent", nargs="?", default=None, help="default: every agent in the chain")
    p_sessions.set_defaults(func=cmd_sessions, needs_lock=False)

    p_verify = sub.add_parser("verify", help="check the handoff state for damage or drift")
    p_verify.set_defaults(func=cmd_verify, needs_lock=False)

    p_recover = sub.add_parser("recover", help="clean up what an interrupted run left behind")
    p_recover.add_argument(
        "--break-lock", action="store_true", help="also remove a lock held by a live process"
    )
    p_recover.add_argument("--dry-run", action="store_true", help="report without changing anything")
    p_recover.set_defaults(func=cmd_recover, needs_lock=False)

    p_config = sub.add_parser("config", help="show or create the fallback-chain config")
    p_config.add_argument("--init", action="store_true", help="write a starter config.toml")
    p_config.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_config.set_defaults(func=cmd_config)

    p_agents = sub.add_parser("agents", help="list agents and how each one is started")
    p_agents.add_argument("--verbose", "-v", action="store_true", help="show every detail")
    p_agents.set_defaults(func=cmd_agents)

    return parser


def _command_names(parser: argparse.ArgumentParser) -> set:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _rewrite_shorthand(argv: Sequence[str], commands: set) -> List[str]:
    """`handoff codex --to claude` -> `handoff switch --from codex --to claude`."""
    args = list(argv)
    if not args or args[0].startswith("-") or args[0] in commands:
        return args
    try:
        source = registry.canonical_name(args[0])
    except AdapterError:
        return args
    return ["switch", "--from", source, *args[1:]]


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_rewrite_shorthand(argv, _command_names(parser)))
    try:
        root = find_root(Path.cwd())
        if root is None or not _wants_lock(args):
            return args.func(args)
        # One writer at a time, released even if the command raises.
        with _lock(Store(root).lock_file, args.command):
            return args.func(args)
    except lock_mod.LockBusy as exc:
        _echo("error: " + str(exc))
        return 1
    except (StoreNotInitialized, AdapterError, ConfigError) as exc:
        _echo("error: " + str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
