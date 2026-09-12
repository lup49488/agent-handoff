# Changelog

This project was built in the phases its [design notes](docs/design.md) lay
out. Where an entry says a thing was *found*, it was found by using the tool
against a real repository or by checking a real agent — those are called out,
because they are the ones that changed the design rather than the code.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
is still below 1.0, so the interface may still change.

## [Unreleased]

### Fixed

- A benchmark trial was never its own Git repository, so the comparison it
  exists to make could not be valid. Prepared inside a checkout — which the
  documented command line does — the package described the *enclosing*
  project: its branch, its HEAD, its commits. Prepared anywhere else, the
  Baseline arm had no history at all, while the design gives it the worktree
  *and* the history. Each decides the result before the target agent starts,
  in opposite directions. Every trial now commits the pre-task state and
  leaves the snapshot's interrupted progress uncommitted, which is what the
  design asked for in the first place.

- The documented benchmark commands could not run. `benchmarks/*.py` put only
  `src` on the path, so every `benchmarks.*` import failed — all of
  `run_trial.py` and Task C's preparation. The tests passed because pytest
  adds the project root itself and they import the modules rather than running
  the scripts; the regression tests now run them the way the README does.

- The checkpoint a trial measures was scrambled. Three fixture sections —
  progress, reasoning, next step — were flattened into "next step" by two
  string replacements, while "done" and "decisions" carried instrumentation
  notes. It is now parsed by heading and rendered through the product's own
  `Checkpoint`, so the package under test is shaped like one a real agent
  writes.

- The package told the target agent it was in an experiment: a
  `benchmark_preparation` reason (not a real handoff reason at all) and
  `benchmark-source` / `benchmark-target` agent names, all rendered into the
  `HANDOFF.md` the Handoff arm reads. That is not a neutral prompt.

- A trial that ran past its budget was recorded as `accepted` *and* invalid at
  once, putting it in the sample and out of it simultaneously. Over-budget is
  now an outcome (`budget_exceeded`) and `invalid_reason` means excluded; the
  validator rejects records that claim both.

- `task-b`'s 60% snapshot had no next step — the only one of nine missing it.

### Added

- `benchmarks/plan.py` prints a cohort's run order with arm order randomised
  per cell, as the design requires. Seeded by a string rather than `hash()`,
  because Python randomises string hashing per process and a plan that differs
  every run cannot be pre-registered.

- `run_trial.py` now records `first_verified_progress_seconds` — the design's
  leading comparative metric — by re-running the acceptance check while the
  target works. Turns and provider tokens stay null: collecting them needs a
  telemetry-capable harness, and the design forbids estimating them. A target
  that outruns its budget has its whole process tree killed, so a timed-out
  trial stops spending quota.

- Failure rehearsals for the four interruption shapes the drills had skipped:
  a silent stall, a journal line cut in half, a temp file from an unfinished
  write, and a lock whose holder is gone (plus the live-run lock that recovery
  must refuse to break).

- `handoff status` died with `UnicodeEncodeError` when the console's code page
  could not encode the task title — a Chinese title on a `cp932` console. Found
  by running the tool that way: `init` had written the package correctly, so
  the state existed and was unreadable at the same time, which is the failure
  this tool exists to prevent. The CLI now relaxes the error policy on its own
  streams, so an unrepresentable character degrades to `?` instead of taking
  the command down. Only the policy is relaxed: forcing UTF-8 onto a legacy
  console would garble the text it *can* display.

  `checkpoint --show` failed the same way, and `runner`'s output pump would
  have too — it writes a supervised agent's output straight to `sys.stdout`,
  where the exception kills the thread rather than one command. The fix is at
  the process entry point, which is the only place that covers both.

- CI never ran on a push. The workflow triggered on `main`; the default branch
  is `master`.

### Added

- An agent skill in `skills/agent-handoff/`. One directory serves both Codex
  and Claude Code: they read the same `SKILL.md`, `agents/openai.yaml` carries
  the interface metadata Codex shows, and a plugin manifest lets Claude Code
  install this repository directly. The skill exists because the CLI cannot
  tell an agent *when* to track a task — that judgement, and the rule that
  another agent is never launched unasked, live in the skill.

## [0.9.2] — 2026-09-08

### Fixed

- The built-in TOML reader — the one that loads *every* config on Python 3.9
  and 3.10, which have no `tomllib` — split arrays on commas inside strings and
  never resolved escapes. A command line containing a comma was cut into
  pieces, and every Windows path lost its backslashes. Found by running the
  suite on 3.10 for the first time, against a compatibility claim that had
  never been tested.
- A supervised agent inherited an open standard input. Codex appends piped
  stdin to its prompt, so `handoff run` hung until the stall timeout fired a
  quarter of an hour later. Supervised children now get `DEVNULL`; an
  interactive launch keeps its terminal.
- The commented cooldown in the starter config still suggested one hour, the
  value a real exhaustion had already disproved.

### Added

- A differential test that requires the built-in TOML reader to agree with
  `tomllib` wherever the reference implementation exists.
- A GitHub Actions matrix over three operating systems and Python 3.9–3.13.

### Removed

- `checkpoint.as_json` and `AgentAdapter.capabilities`, both unreachable.
  `capabilities` described what `handoff agents` already prints from the spec,
  and two descriptions of one thing drift apart.

## [0.9.0] — 2026-09-08

### Added

- Reading an agent's own session log, so an interactive handoff records why the
  agent stopped instead of a bare `manual_handoff`. Verified against real
  failures from codex-cli 0.153.4 and Claude Code 2.1.263.
- Both agents publish a real reset time, so a cooldown becomes a measured value
  rather than an estimate. `handoff health` marks those, and `--refresh`
  updates them without waiting for a handoff.
- `handoff sessions`, which prints exactly what was read and from which file.
  Reading someone's private logs has to be auditable.

### Fixed

- The failure classifier missed Claude Code's real message. Codex says "usage
  limit"; Claude Code says "session limit", and only the first was covered.
- Session files were matched to a project by the first record's working
  directory. Claude Code's transcripts open with several records that have no
  working directory at all, so every one of them was skipped.

### Security

- Session reading extracts only the failure message and the rate-limit
  telemetry, only from sessions recorded for the current project, and can be
  turned off entirely. A session log holds whatever the agent saw, and the
  handoff package is fed straight to another agent.

## [0.7.0] — 2026-09-08

### Added

- `handoff tests`, which fills the `Latest test results` section the package
  had always rendered and nothing had ever written.
- Previous tasks are archived under `.agent-handoff/archive/<task_id>/` instead
  of being left in place.

### Fixed

- `init --force` left the previous task's journal and checkpoint behind, so the
  next handoff handed the new agent the *old* task's reasoning. Being
  confidently wrong about the work is worse than knowing nothing about it.
- A manual `switch` never recorded that an agent had run out, so the chain
  could not later notice it had recovered.
- The chain gave up at its tail even when an earlier agent's window had reset.
- `switch` held the write lock while waiting for the agent it had launched,
  blocking the very checkpoints that agent was asked to record. Found while a
  real handoff was in progress.
- `handoff config` required an initialised store, though configuration belongs
  to the project and exists before any task.
- The quota cooldown was an hour. A real Codex exhaustion reported a five-hour
  window with 226 minutes left, so an hour called the agent ready almost three
  hours early.

## [0.6.0] — 2026-09-08

### Added

- Health-aware agent selection: an agent that recently ran out is moved to the
  back of the chain rather than launched to find out. Health is per user,
  because quota belongs to an account rather than to a repository.
- `handoff health`, with per-reason cooldowns configurable in `[cooldowns]`.

### Fixed

- The per-user directory shares its name with a project store and sits above
  every path under the home directory, so `find_root` mistook the home
  directory for a project root from anywhere. A store is now identified by its
  `task.json`.

## [0.5.0] — 2026-09-07

### Added

- Advisory locking, so only one command writes at a time. A lock whose holder
  is gone is broken automatically; a lock from another machine is respected.
- `handoff verify` and `handoff recover` for damage, drift and leftovers.
- Package validation before any handoff. An agent started on a truncated or
  stale package looks busy while working from the wrong state.
- A stall timeout. An agent retrying against an unreachable endpoint prints
  nothing and never exits, which is the same lost time this tool exists to
  prevent. Found by probing a real CLI, which produced no output for 90 seconds.

### Fixed

- An append after a killed write spliced two records into one unreadable line
  and lost the new one too.

### Notes

- Liveness is checked with `OpenProcess` on Windows, never `os.kill(pid, 0)` —
  CPython maps that to `TerminateProcess`, so the probe would kill the process
  it was asking about.

## [0.4.0] — 2026-09-07

### Added

- Agents are described as data. A config table is a complete adapter, and the
  same tables correct a built-in whose flags differ. Gemini, OpenCode and Pi
  ship alongside Codex and Claude Code.
- Each agent declares two command lines: `codex "<prompt>"` drives a terminal
  UI and refuses piped stdio, which is exactly how a supervised run starts it.

### Fixed

- `codex exec` defaults to a read-only sandbox, where the agent can reason but
  never change a file — a supervised run would have produced nothing to hand
  on. Both built-ins now ask for the least privilege that still works.

## [0.3.0] — 2026-09-07

### Added

- A configurable, ordered fallback chain, walked in either direction.
- `handoff config`, and a TOML reader that falls back to a built-in parser on
  Python versions without `tomllib`.

### Fixed

- `switch --dry-run` wrote the package and recorded a failure that never
  happened. A dry run must not touch the journal.

## [0.2.0] — 2026-09-07

### Added

- Semantic checkpoints in `state.md`, with a token budget and a staleness note.
  Deterministic state says what happened; this is the only place that records
  why.
- The checkpoint protocol given to the working agent, so it maintains its own.

## [0.1.0] — 2026-09-07

### Added

- Supervised runs with failure classification and automatic failover.

### Fixed

- The classifier matched an agent's output regardless of exit code, so an agent
  that finished cleanly after reading rate-limiting code was handed off anyway.

## [0.0.1] — 2026-09-07

### Added

- The handoff store, deterministic git state capture, `HANDOFF.md` rendering,
  and the adapter layer. Manual handoff only.
