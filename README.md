# agent-handoff

[中文版 README](README.zh-CN.md) · [Design notes](docs/design.md) · [MIT License](LICENSE)

**A failover layer for coding agents.** When one runs out of quota, gets rate
limited, crashes, or goes silent, the work it already did is still in the
repository — this hands that work to the next agent instead of starting over.

> The agent does the work. The handoff layer remembers it.

## Contents

- [Status](#status)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Failover and checkpoints](#failover-and-checkpoints)
- [Configuring the chain](#configuring-the-chain)
- [Durability, health and session reading](#durability-health-and-session-reading)
- [What gets stored](#what-gets-stored)
- [Using it from an agent](#using-it-from-an-agent)
- [Development](#development)

## Status

Implemented:

- manual handoff and supervised automatic failover;
- deterministic state capture — git status, diff, commands, logs, test results;
- semantic checkpoints in `state.md`, with a staleness note;
- Codex, Claude Code, Gemini, OpenCode, Pi, and any agent defined in config;
- an ordered fallback chain that works in either direction;
- atomic writes, single-writer locking, crash recovery, and package validation;
- per-user agent health, with limited reading of the agents' own session logs.

Verified:

- automated tests cover state capture, failover, checkpoints, config, locking,
  recovery, health-aware selection and session reading;
- Codex → Claude Code and Claude Code → Codex quota handoffs have been run by
  hand against real exhaustions;
- the built-in command lines for Codex and Claude Code were checked against
  installed CLIs. **Gemini, OpenCode and Pi were not** — try them on a small
  task in your own environment first, and correct them in config if they differ.

Deliberately out of scope: a multi-agent platform, a planner, a web dashboard,
remote task state, shared long-term memory.

## Install

```bash
pip install .
```

An editable install fails on Windows when the project path contains non-ASCII
characters. Run from source instead:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m agent_handoff --help
```

## Quick start

Start tracking before the agent begins, then run it under supervision:

```bash
handoff init "Fix the authentication refresh bug" --from codex --to claude
handoff run
```

On a recognised failure the tool captures repository state, writes `HANDOFF.md`,
and starts the next available agent. A clean exit marks the task done — only a
nonzero exit or the configured silence timeout triggers failover.

Working with an agent interactively instead? Track the task the same way, and
hand it over when the agent stops:

```bash
handoff switch --to claude --reason quota_exhausted
```

`--no-launch` prepares the package without starting anything; `--dry-run` prints
what would happen and changes nothing.

## Commands

| Command | What it does |
| --- | --- |
| `handoff status` | task, checkpoint, test result and recent events |
| `handoff checkpoint --next "..."` | record progress, decisions, the current problem and the next step |
| `handoff tests "pytest -q" --exit-code 0` | put the latest test result in the package |
| `handoff pack --to claude` | re-render `HANDOFF.md` without launching anything |
| `handoff agents -v` | which agents are installed, and the exact command line each is started with |
| `handoff config --init` | write a starter chain configuration |
| `handoff health` | per-user cooldowns; `--refresh` updates them from session logs |
| `handoff sessions` | what an agent's own log says about how it stopped |
| `handoff verify` / `handoff recover` | check, or clean up, what an interruption left behind |

## Failover and checkpoints

`handoff run` saves stdout and stderr, and on a nonzero exit recognises quota
exhaustion, rate limiting, a full context window, provider errors, network
failures, process crashes, and an agent that has gone silent past the timeout.

The failure patterns were checked against strings taken from the shipped
binaries of Codex CLI and Claude Code, and against two real exhaustions. That
mattered: a first version written from plausible-sounding prose misclassified
more than half of the real messages, because real errors arrive as identifiers
(`rate_limit_error`, `QuotaExceeded`) at least as often as sentences — and
because Codex says "usage limit" where Claude Code says "session limit".

A checkpoint is written at meaningful milestones, not after every tool call. It
says what the objective is, what is done, what was decided, what is blocking,
and what to do next. Git and the logs already show *what happened*; the
checkpoint is the only place that records **why**.

## Configuring the chain

In `.agent-handoff/config.toml`, or `handoff.toml` at the project root:

```toml
primary = "codex"
fallback = ["claude-code", "pi"]

# Ask the working agent to keep state.md current at milestones.
checkpoint_protocol = true

# Total silence for this long means a wedged agent. 0 waits forever.
stall_timeout_seconds = 900

# Move agents that recently ran out to the back of the queue.
health = true

[agents.my-agent]
executable = "my-agent"
exec = ["--non-interactive", "{prompt}"]
interactive = ["{prompt}"]
```

Nothing in the chain is privileged, so pointing it the other way is just:

```toml
primary = "claude-code"
fallback = ["codex"]
```

Agents are data, not code: a config table is a complete adapter, and the same
tables correct a built-in whose flags differ in your version. Each agent
declares two command lines, because a supervised run pipes stdio and a CLI
driving a terminal UI refuses to start that way.

## Durability, health and session reading

- State files are written flush → fsync → atomic replace, so a crash mid-write
  cannot replace good state with half a file.
- JSONL journals skip the partial final line a killed process leaves, and the
  next append starts a fresh line rather than splicing onto it.
- Writes are serialised. A supervised agent can still record its own
  checkpoints while it works — the supervisor does not hold the write lock.
- A fallback is launched only after `HANDOFF.md` passes validation. Starting an
  agent on a truncated or stale package is worse than not handing off at all.
- Health is per-user, because quota belongs to an account rather than to a
  repository. A cooling agent moves to the back of the queue but is never
  banned: a cooldown is a guess, and a guess must not strand a task.
- Session reading extracts **only** the failure message and the rate-limit
  telemetry, and only from sessions recorded for this project — never the
  conversation, the commands, or the diffs. Both Codex and Claude Code publish a
  real reset time, which beats any cooldown this tool could estimate. Turn it
  off with `read_agent_sessions = false`.

## What gets stored

```text
.agent-handoff/
├── task.json           the task and its handoff state
├── git.json            branch, head, changed files, diffstat
├── events.jsonl        append-only journal
├── commands.jsonl      recorded commands and exit codes
├── tests.json          the latest test result
├── state.md            the semantic checkpoint (optional)
├── checkpoints.jsonl   when each checkpoint was taken
├── logs/               a supervised agent's stdout and stderr
├── lock, run.lock      the short write lock and the session lock
└── config.toml         optional project configuration
```

`HANDOFF.md` is written at the project root. Run state, logs, build output and
`HANDOFF.md` are all gitignored — none of it belongs in a commit.

## Adding an agent

Usually you do not: add an `[agents.<name>]` table to the config. To ship one
with the tool, add an `AgentSpec` to `BUILTIN_SPECS` in
`src/agent_handoff/adapters/builtins.py` — one entry, no class.

Write an adapter class only for behaviour a command line cannot express:
subclass `AgentAdapter`, implement `resume_argv()`, and override
`detect_failure` if the shared error patterns get that provider wrong.

To teach it to read a new agent's session log, add a `SessionReader` to
`READERS` in `src/agent_handoff/sessions.py` — but only against a real failing
session. Guessing at the format is how the failure classifier ended up wrong
for half its inputs the first time.

## Using it from an agent

The CLI is the whole tool, but an agent has to know *when* to reach for it. A
skill in `skills/agent-handoff/` carries that judgement: track substantive
repository work, checkpoint at milestones, never launch another agent without
being asked.

**Claude Code** — install the repository as a plugin, from the desktop app or
the CLI:

```text
/plugin marketplace add lup49488/agent-handoff
/plugin install agent-handoff@agent-handoff
```

Or copy the skill in by hand — `~/.claude/skills/` for every project,
`.claude/skills/` inside one project:

```bash
cp -r skills/agent-handoff ~/.claude/skills/agent-handoff
```

**Codex** — copy the same directory into `~/.codex/skills/`. Codex reads the
same `SKILL.md`; `agents/openai.yaml` supplies the name, blurb and default
prompt its interface shows.

```bash
cp -r skills/agent-handoff ~/.codex/skills/agent-handoff
```

Either way the skill assumes `handoff` is on `PATH`. If it is not, it falls
back to running the module from a source checkout, as [Install](#install)
describes.

## Development

```bash
python -m pytest -q
```

The test configuration hides the real coding-agent executables from `PATH`, so
no test can spend your quota or start a live agent by accident. On Python 3.9
and 3.10 there is no `tomllib`, so the built-in TOML reader is what loads every
config; it is checked against the reference implementation wherever that exists.

## License

[MIT](LICENSE), © 2026 Pinjia Lu.
