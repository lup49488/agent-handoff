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

- Window measurements were blind between progress and acceptance. The
  checkpoint and edits were looked at only while progress was probed, which
  stops once it passes, so a checkpoint written in the window — the one
  stretch the measurement exists for — was recorded as seen at exit, and an
  edit landing in the same round as the progress it caused went unrecorded
  (task E v2's W03). Both are now watched every round, every edit is
  recorded with its files, and each checkpoint write is timed from the
  package's own log. Task E v2's W01–W08 cannot say whether the skill
  checkpointed inside the window.

- A recovery-pilot record could not say what the recovery session was handed.
  Only a hash of the package was kept and the workspace is deleted after each
  trial, so when D-handoff-r04 — in the first pair whose sessions were both
  interrupted — recovered about 40 seconds slower than its Baseline, there was
  no way to tell whether its package held a checkpoint or only the task.
  Records now keep the text of `HANDOFF.md` and the checkpoint behind it, as
  recovery received them and as recovery left them.

- The recovery pilot's first-progress trigger could not interrupt anything.
  Under it, D-baseline-r03 finished at 35.8 seconds without being stopped:
  progress and full acceptance first passed in the same probe round, about
  1.2 seconds apart at most. Five source sessions of five, under both rules
  tried, have shown the same: the agent writes the whole solution in one
  burst, so there is no partly-written state on disk to interrupt, and the
  work in progress lives only in the agent's context — what a checkpoint is
  for. New plans (schema 3) stop the source session at a fixed 30 seconds,
  calibrated on those five sessions; earlier plans still load as the rule
  they were registered under. `--withdraw` retires a pending pilot row with
  its reason; D-handoff-r03 was withdrawn, since without a Baseline recovery
  its pair could not yield a comparison.

- `python benchmarks/recovery_pilot.py` failed on its first import; only
  `python -m benchmarks.recovery_pilot` worked.

- Ctrl-C stopped `handoff run` but not its agent. Adapters start the agent
  in its own session (POSIX) or process group (Windows) so its whole tree can
  be killed, which also keeps a terminal's Ctrl-C from reaching it — Windows
  disables Ctrl+C for a new group outright. Interrupting the supervisor left
  the agent running unsupervised, spending quota and editing the repository.
  The supervisor now kills the agent's tree before letting any interruption
  through. `run_trial.py` had the same gap once it, too, began starting
  targets in their own session on POSIX; it is closed the same way. Checked
  on Linux with a real SIGINT.

- On macOS, a worker could outlive the kill of the agent that started it.
  When the agent had already exited but not been reaped, macOS refuses to
  signal its process group (`PermissionError`), where Linux signals it; the
  worker still in that group was left running. The leader is now reaped and
  the group signalled again. Found because a new test built its worker from a
  nested `-c` string whose newline became a real one: the leader died of a
  SyntaxError, Linux killed the group anyway and the test passed by accident,
  and macOS did not. Test scripts now go in files.

- On Linux and macOS, `_kill_process_tree` reported a process killed without
  killing it. `killpg` finds a group only when the process leads one; for a
  process started outside its own session it raises ProcessLookupError, and
  the previous change treated that as "the whole group had already gone".
  Every POSIX CI job failed. It now falls back to the direct child and reports
  the kill as partial. The benchmark harnesses also start their targets in
  their own session on POSIX, as the product's adapters always have, so a
  group kill reaches everything a target started.

- `handoff run` could wait forever for an agent that had already exited. Its
  output pipes close only when every process holding them has, so a worker the
  agent left in the background — or a descendant a refused `taskkill` could not
  reach — kept the supervisor joined on its readers, and the fallback agent
  never started. Output is now read for at most ten seconds after the agent
  exits, with what arrived by then kept. `_kill_process_tree` now reports
  whether the whole tree is known to be gone.

- The benchmark harnesses had the same wait, and worse consequences. A worker
  holding the pipes made `run_trial.py` sit out the whole wall-clock budget
  and then record the trial as over budget; after a kill that could not reach
  the whole tree, its second wait had no bound. In the recovery pilot, a
  session stopped by the progress trigger was recorded as timed out, and a
  surviving descendant could go on changing the project after the
  interruption. Both now wait boundedly, and a trial whose process tree
  outlived the stop is excluded (`process_tree_not_terminated`): neither the
  workspace nor the logs the contamination scan reads can be trusted. A
  workspace such a process still sits in — locked, on Windows — no longer
  fails a trial whose record was already written.

- A `handoff pack` that errored at the interruption would have discarded the
  whole recovery-pilot trial after its source session had spent quota. The
  error is now recorded and recovery starts from whatever exists.

- `run_trial.py` read the CLI version from `codex` or `claude` on PATH, though
  a config table can launch the agent from another executable. It now asks
  the executable the adapter will actually run.

- Every correct v0.5 recovery would have been scored out of scope. The
  recovery pilot's hand-written list of files a target may change left out
  `schema.py`, which the v0.5 prompt tells the target to edit and the
  reference solution does edit; the partial state at interruption failed the
  audit too. Unnoticed because no recovery session — the only phase the audit
  scores — had run, and because calibration checked the evaluators but not the
  audit. The list is now derived from what the fixture's own partial and
  reference variants change, and calibration runs both through the audit. With
  the old list, calibration now fails.

- The recovery pilot never interrupted anything. All four source sessions
  finished well inside the five-minute cutoff, so no recovery session ever
  ran. New plans (schema 2) stop the source session the moment the progress
  check first passes, unless acceptance passes in the same round, probing
  every second with no grace: in all four sessions progress and completion
  were first seen in the same five-second round, so the agent writes the
  solution in a burst and a grace period would mostly let it finish. The rule
  is frozen into each plan; D-r01 and D-r02 still read back as the wall-clock
  rule they ran under.

- A Handoff arm stopped mid-task was not handed over. The harness now runs
  `handoff pack` in that arm at the stop, as a failing `handoff run` or a
  `handoff switch` would; an agent stopped mid-task has usually not rendered
  `HANDOFF.md` itself. Only rendering: a session that never started tracking
  gets nothing created for it. Records keep what the agent left and what
  recovery received separately; a pack in the Baseline arm fails validation.

- Hand-typed CLI versions split cohorts. One Codex release was recorded as
  `codex-cli 0.160.0`, `codex-cli-0.160.0` and `0.160.0`, splitting the clean
  snapshot cohort in two and keeping it apart from the contaminated v0.3
  cohort only by a typo. `run_trial.py` now reads the version from the CLI and
  refuses a typed one that disagrees; the summary compares versions by release
  number and never pools record schemas.

- `summarize.py .trials`, the documented command, could not run. Two
  verification trials reused the id `A-60-baseline-r01`; a sandbox had left
  pytest records under `.trials/.sandbox-tmp`; and the pattern for exported
  records also matched pilot records and cohort plans. Verification runs now
  live in `.verification/`, dot-directories are skipped, and exported records
  must be named like one.

- The recovery pilot kept its own copy of the contamination scan, which no
  longer recognised `python -m agent_handoff`. It now uses the snapshot
  runner's scan, granting its Handoff arm the one skill its treatment
  consists of; the scan names whichever user skill was read rather than only
  agent-handoff.

### Added

- Recovery-pilot task E, an investigation whose findings are not on disk.
  Eight pairs on task D showed the package cannot help there: everything a
  session learns before the stop is reading four short files, which its
  successor has to redo to edit them. Task E's report is wrong for one store
  because of two bugs visible only by running it — an offset whose minutes
  lose their sign, and refunds booked to their UTC date — with a misleading
  rounding note and TODO to rule out. Hidden evaluator rows (another store,
  refunds in every store) fail fixes that special-case store B.
- The recovery pilot takes `--fixture`; plans record theirs, and pair and
  trial ids carry its letter (`E-r01`). A fixture can declare its public
  check in `public_check.txt` — task E's is `check_report.py`, not unittest.
  Plans without a fixture are read as task D.
- Window measurements record the first edit to a file the solution changes.
  On task E it came 1.2 to 4.8 seconds after the first checkpoint in four
  sessions that finished in 30 to 48 seconds: both bugs were plain from one
  run of `debug_report.py` and a rule stated in `NOTES.md`, so task E, like
  task D, leaves no window for a package to help in.
- Recovery-pilot task E v2. The offset bug stays; the tools stop printing it,
  the refund bug is gone, and the second cause is in a four-week ledger: two
  tills' exports re-imported, the same receipts under new ids. Evaluators
  check the ledger's hash and add a re-import at another store and two
  genuine look-alike sales. A fixture may widen its scope in `scope.txt`.
- Task E v2 has a window: eight sessions spent 21 to 64 seconds between
  fixing the offset and finding the re-imported receipts, findings that were
  on disk nowhere. With the skill revised to checkpoint before every edit
  that follows new findings, four more sessions all recorded the duplicates
  before removing them — but 8 to 13 seconds before acceptance, so a package
  holds them only in a stretch too short and too variable for a fixed stop.
  Revised again to record findings when established, sessions checkpoint the
  narrowing within seconds of the offset fix but still write the decisive
  finding just before, or after, the fix it calls for.
- Recovery-pilot task E v3: task E v2 with a nine-month, 1.8-million-row
  ledger, so that every run of the report takes 10 to 13 seconds and what an
  investigation establishes costs a successor real time to establish again.
  A fixture's `prepare.py` now writes what it cannot ship into each project
  copy; v3's generates the ledger and finance's figures from a fixed seed.
  The evaluators judge the code on v2's ledger, embedded, to stay fast.

- pytest always prints the names of failing tests (`-rf`). One intermittent
  failure in a full local run went unidentified because a quiet run reports
  only a count, and six further runs did not reproduce it.

- `plan.py --withdraw` marks a pending row that will never run, with the
  reason. Used on the four rows the `post-actions-r04` plan was left with,
  whose ids the contaminated v0.3 cohort had already used.

- On Windows, an exclusive lock-file create can report an existing lock as
  `PermissionError` instead of `FileExistsError`. The lock now treats that as
  contention only when the lock path exists, preserving real permission errors.

- Codex's pre-launch skill check searched every rendered message for a skill's
  bare name, so unrelated session-context mentions could block a trial. It now
  checks for the exact name-and-description entry in developer skill context,
  treats unknown output or unreadable metadata as unproven, and refuses a
  Codex launch unless the check passes. A no-model synthetic-skill probe
  verified that the installed CLI hides the catalogue entry when disabled.

- A Claude Code target could not run a single command. The adapter's
  `acceptEdits` mode refuses every shell command in print mode, so a Claude
  trial could not run the acceptance check it was told to pass — one
  verification trial was refused four times and finished by editing blind —
  nor the `handoff checkpoint` its package asks for, while a Codex target
  could do both. Benchmark launches now admit exactly `python acceptance.py`,
  read-only Git, and the recording `handoff` subcommands, for both arms. A
  probe against Claude Code 2.1.287 ran each of those and refused
  `python -c` and `handoff run`; a real trial then ran its own acceptance
  check with no refusals. The product adapter is unchanged.

- Claude Code trials were not auditable. Print mode logs only the final
  answer, so a skill read or a `handoff` command would have left no trace for
  the contamination scan. Targets now log stream JSON; the session's first
  event is checked for loaded skills, and the scan recognises Claude Code's
  memory paths as well as Codex's. Verified against Claude Code 2.1.287:
  `--disable-slash-commands` takes the session from 35 skills to none, and one
  real A/60 Baseline trial made eight tool calls, all inside its own neutral
  workspace, with no skill, memory or `handoff` reference in its log.

- The Baseline arm was never a no-handoff condition. Targets ran with their
  developer's own Codex setup, and three things reached them: a globally
  installed agent-handoff skill, which every Baseline agent from r02 on read
  before initialising handoff tracking and running the `handoff` CLI itself;
  persistent memories describing the Baseline/Handoff design, read in most
  trials of both arms; and a working directory named after the arm. All 24
  v0.3 trials are now excluded, each with its reason. Launched targets run in
  a neutral temporary directory with memories off and every user skill
  disabled; before launching, the model's input is rendered with
  `codex debug prompt-input` and the launch refused if a disabled skill is
  still in it; after the run, a log scan excludes any trial that was reached
  anyway. Verified against the installed Codex: the skill is in the rendered
  input without the arguments and absent with them, and one real A/60
  Baseline trial ran with no skill, memory or `handoff` reference in its logs.

- The progress metric carried no information of its own. Both checks run in
  one probe round, so every v0.3 B/60 progress time sat a fraction of a
  second before completion (20.079 s against 20.094 s) — one observation
  reported as two. Task C's milestone was behavioural, and a refactor keeps
  behaviour at every step, so it passed before any work at every snapshot.
  Records now say whether progress was seen in an earlier round
  (`progress_separable`), the summary takes the progress median from those
  trials only, and C's milestone is structural: the shared module exists and a
  consumer imports it.

- A balanced cohort plan recorded an arm order it never ran. `run_trial`
  recomputed the order with the unbalanced shuffle rather than reading the
  plan's rows. The real r06–r09 cohort agreed only by luck; with seed 1, half
  of four replicates would have recorded the wrong order.

- The r01 round was excluded because a Baseline trial ran over budget — an
  outcome, which is not a reason to exclude. Its logs show the independent
  cause: 12 stream disconnects and 17–18 reconnects each in three of four
  trials, with 502/503 responses. `summarize.py --exclusions` now takes such
  reasons from a file, so a summary and a written report set aside the same
  trials; it also prints times to the millisecond rather than as
  `37.617000000000004`.

### Added

- Cohort plans can restrict the pre-registered task/snapshot cells with
  repeatable `--task` and `--snapshot` filters, while recording those choices
  in the plan file. Defaults still include every task and snapshot.

- Cohort plans accept `--replicate-start` so a fresh addendum cohort can use
  new trial IDs without overwriting earlier records.

- Cohort plans can opt into `--balance-arm-order`, which deterministically
  balances which arm runs first across replicates within each task/snapshot.

- Benchmark schema v3 separates each fixture's first regression check from
  full acceptance, records independent progress and completion times, and adds
  a cohort-stratified summary that shows missing cells and does not pool tasks,
  snapshots, or pinned agent/model configurations.

- Cohort-plan files now have explicit pending/running/recorded states. A real
  runner launch can claim only the next pending pre-registered trial, preventing
  accidental arm reordering or concurrent duplicate runs. New trial records use
  schema v2, whose fixture, environment, start time, and scope-audit evidence
  are required by the validator while legacy v1 records remain readable.

- Benchmark runner v0.2 evidence: each new record includes fixture hashes,
  platform metadata, planned arm-order metadata when supplied, an externally
  sourced acceptance evaluation, final acceptance logs, a pre-registered scope
  audit, and separate first-completion and process-exit timings. Editing a
  workspace `acceptance.py` can no longer make a trial pass.

### Fixed

- Task B could never produce a completed trial. Its pre-registered edit
  surface allowed only `api.py`, but the source agent's own interrupted work —
  shipped in the 60% and 80% snapshots — had already edited `service.py`, so
  four of eighteen cells were disqualified before any target ran. Worse, the
  package told the Handoff arm to sort in the service layer, so the guardrail
  penalised the arm being evaluated for following the package being evaluated.
  The surface now includes `service.py`, and a test derives the invariant that
  missed it: every file a fixture's snapshots change must be inside the surface.

- A runner killed mid-trial wedged its cohort plan. The row stayed `running`
  forever, every later claim refused, and the only way out was hand-editing
  JSON. A claim now records the process that took it; the next claim names a
  dead holder, and `plan.py --release` returns the row to `pending` — refusing
  while the holder is still alive, since that would let one row be claimed
  twice.

- An invalid trial consumed its plan row as `recorded`, so a cell could end
  short of its required replicates with nothing saying so. It now ends as
  `invalid`, and `plan.py --status` names every short cell.

- The run order was reimplemented inside `run_trial.py` rather than imported
  from `plan.py`. The copies agreed; they would have stopped agreeing silently
  the first time either changed.

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

### Documented

- Recovery-pilot pair D-r08, the first to run entirely as designed: both
  sources stopped at 45 seconds with nothing on disk, and the Handoff recovery
  received a substantive checkpoint. It recovered in 63.5 seconds against the
  Baseline's 55.5, after re-reading every file the checkpoint said had been
  read and re-deriving the same decision. To edit a file an agent has to read
  it, and on task D the progress before the stop is just that reading, which
  costs seconds to redo. Task D cannot show what a package is for; the next
  pilot needs a task whose pre-interruption findings are expensive to repeat
  and invisible on disk.

- Recovery-pilot pair D-r07, the first at 45 seconds. Its Handoff source
  finished at 33.5 seconds, before the stop — expected in about one session in
  four — so the pair yields no comparison; it is recorded, not excluded or
  re-run. Its Baseline recovery, at 32.4 seconds, was the fastest yet, against
  66.7 to 112.5 in earlier pairs.

- The window in which a handoff package can help, measured: four
  uninterrupted Handoff sessions with the revised skill wrote a substantive
  first checkpoint, before any edit, at 20.7 to 38.1 seconds and completed at
  39.3 to 93.9. Recovery-pilot plans now stop the source session at 45
  seconds (schema 4), inside the window in three of four; 30 seconds had been
  inside it in two. `--measure-window` runs such a session and keeps each
  version of the checkpoint with the time it appeared. Records are in
  `benchmarks/results/window-2026-10-16/`.

- Recovery-pilot pair D-r06, with the skill revised to checkpoint before the
  first edit. Its Handoff source had started tracking but written no
  checkpoint by the 30-second stop — it skipped the empty one as asked and
  had not reached the substantive one — so the package held only the task,
  and the Handoff recovery's 18.7-second lead is noise. The recovery's own
  checkpoint shows the revision working: it records the files read, what
  they do and the decisions taken, after 30 seconds. A package can help only
  between the first substantive checkpoint and completion; 30 seconds falls
  before that window.

- Recovery-pilot pair D-r05, run Handoff first to balance D-r04. Its Handoff
  recovery reached acceptance in 39.6 seconds against the Baseline's 66.7 —
  the reverse of D-r04 — and in each pair the arm that ran first was faster,
  so the two pairs support no direction. D-r05 was the first record to keep
  the package's text: the checkpoint the skill had written by the 30-second
  stop only restated the task, so the recovery received little its prompt did
  not already hold. The consequence is for the skill: in its first 30 seconds
  it records almost nothing a successor could use.

- Time to verified progress and completion time are the same measurement as
  implemented: each fixture has one acceptance script, so for every completed
  trial the two fields hold the same number. The design lists them as two
  comparative metrics; publishing both would present one result as two pieces
  of evidence. Separating them is a fixture change — acceptance split into a
  first regression check and the full set — and is left for that decision.

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
