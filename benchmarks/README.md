# Benchmark assets

`manifest.json` is the fixed task/snapshot map. Keep task fixtures, source
snapshots, and raw local transcripts outside published results until they have
been reviewed for secrets and licensing. Follow `docs/benchmark-design.md` for
the experiment protocol and the rules on what a result may claim.

## Planning a cohort

The design requires randomising arm order within each task/snapshot pair, and
requires the plan to be pre-registered. Both hold only if the order is
reproducible, so it comes from a seed you record alongside the results:

```text
python benchmarks/plan.py --seed 20260911 --replicates 3
```

By default, every task and snapshot is included. Repeat `--task` and
`--snapshot` to pre-register a smaller cohort; the selected values are recorded
in the stateful plan. `--replicate-start` assigns fresh trial IDs without
reusing earlier replicates. Add `--balance-arm-order` to balance which arm
runs first across replicates within each task/snapshot cell; with an odd number
of replicates, the extra first position is selected reproducibly from the seed.
For example, one replicate at snapshot 60 across all three tasks contains six
trials (one Baseline and one Handoff per task):

```text
python benchmarks/plan.py --seed 20261004 --replicates 1 --task A --task B --task C --snapshot 60 --output .trials/v03-instrumentation.json
```

A four-pair B/60 follow-up can balance the first arm and use new `r06`–`r09`
IDs as follows:

```text
python benchmarks/plan.py --seed 20261009 --replicates 4 --replicate-start 6 --balance-arm-order --task B --snapshot 60 --output .trials/v03-b60-balanced.json
```

Run the trials in the order it prints. The same seed reprints the same plan on
any machine, so a single cell can be re-run later without disturbing the rest.
For an executable pre-registration, write the plan once and pass it only to a
real launch. The runner atomically marks just the next pending row as running,
then recorded; it refuses a later row or a second concurrent claim.

```text
python benchmarks/plan.py --seed 20260911 --replicates 3 --output .trials/cohort-plan.json
python benchmarks/run_trial.py A 60 handoff codex 1 .trials/A-60-handoff-r01 --model <model> --target-version <version> --cohort-plan .trials/cohort-plan.json --launch
```

## Preparing a trial

```text
python benchmarks/prepare_trial.py A 60 baseline .trials/A-60-baseline-r01
python benchmarks/prepare_trial.py A 60 handoff .trials/A-60-handoff-r01
```

The command never starts an agent. It refuses to reuse an existing output
directory, so a setup error cannot overwrite an earlier trial.

Each trial gets **its own Git repository**: the pre-task state is committed and
the snapshot's interrupted progress is left uncommitted. That is what gives the
Baseline arm the worktree *and* history the design says it gets. Without it a
trial placed inside a checkout reports the enclosing project's branch, HEAD and
commits, and one placed outside any checkout gives Baseline no history at all —
each decides the comparison before the target agent starts.

Both arms contain the same incomplete project and `prompt.md`; only the Handoff
arm adds `.agent-handoff/` and a validated `HANDOFF.md`. Those are excluded
through `.git/info/exclude` rather than a tracked `.gitignore`, so the Baseline
worktree carries no hint that a package could exist.

At snapshot `30` the tree is clean: the source agent had read the code and
reproduced the failure without editing it yet. That cell measures what the
checkpoint's reasoning is worth on its own, and is not a preparation bug.

## Running a trial

```text
python benchmarks/run_trial.py A 60 handoff codex 1 .trials/A-60-handoff-r01
```

Preparation is free and repeatable. Adding `--launch` starts the one target
named on the command line and **spends real quota**; it also requires `--model`
and `--target-version`, because a trial whose target is not pinned cannot join a
cohort. A target that runs past `--wall-seconds` has its whole process tree
killed, so a timed-out trial stops costing money.

### Isolation

A target measures its arm's condition only if nothing else reaches it. The
v0.3 cohort showed three things that did: a globally installed agent-handoff
skill (every Baseline agent from r02 on started handoff tracking itself),
persistent memories that describe the experiment, and a working directory
named after the arm. A launched trial therefore:

- runs in a fresh temporary directory whose name carries no task, arm or
  replicate, outside any repository, and is moved to the destination you gave
  once the target has finished;
- for Codex, runs with memories off and every skill under `$CODEX_HOME/skills`
  disabled for that one invocation (skills Codex ships in `skills/.system`
  stay; your configuration on disk is not changed);
- for Codex, renders the model's input with `codex debug prompt-input` before
  launching — no model call — and checks the developer skill catalogue for the
  exact name-and-description signature of each disabled user skill. A bare
  name mention in session context is not treated as a loaded skill; an unknown
  output shape or unreadable skill metadata refuses the launch as unproven;
- for Claude Code, runs with `--disable-slash-commands`, which disables all
  skills, and logs in stream JSON so the scan can see every tool call. Claude
  Code cannot render its input without a model call, so the check Codex gets
  before launching is made afterwards from the session's own first event: a
  session that loaded any skill is contaminated. Its auto-memory is keyed by
  working directory, so the neutral directory gives each trial an empty one.
  Plugins marked `builtin` load either way; they ship with Claude Code;
- for Claude Code, admits exactly the commands a trial needs: the fixture's
  `python acceptance.py`, read-only Git, and the `handoff` subcommands that only
  record. Without this, print mode refused every command — a Claude target
  could neither run the check it was told to pass nor the `handoff checkpoint`
  its package asks for, while Codex could do both. `python` in general and
  `handoff run`/`switch` stay refused. Both arms get the same list;
- after the run, scans the target's logs. Reading the handoff skill or the
  agent's memories, in either arm, or using the `handoff` CLI or creating a
  package in the Baseline arm, makes the trial `contaminated`: it is excluded,
  with what was found recorded in `contamination`.

### What the record contains

| Field | Source |
|---|---|
| `first_verified_progress_seconds` | a fixture-owned progress check is re-run every 5 s |
| `completion_seconds` | the full fixture acceptance check, in the same probe rounds; null if incomplete |
| `progress_separable` | progress was seen during the run, in an earlier round than completion |
| `isolation`, `contamination` | what the target was launched with, and anything that reached it anyway |
| `agent_exit_seconds` | target launch until the target process exits |
| `completed` | immutable acceptance against the final tree, with no scope violation |
| `budget_exceeded` | the target was still running at the wall clock |
| `target_turns`, `provider_tokens` | **null** |

Each task has a separate, fixture-owned first regression check and a full
acceptance script. The runner records whether each already passed before
launch; when the progress check is already satisfied, its time is recorded as
zero. Otherwise the runner records the first later pass. Both times are
quantised by the five-second probe interval, so differences smaller than that
interval are not meaningful. An accepted but incomplete trial has no
`completion_seconds` value. New records use schema v4.

Both checks run in the same probe round, a fraction of a second apart. A
progress pass seen in the round that also saw completion is one observation,
not two, and in v0.3 that was every B/60 trial (20.079 s against 20.094 s).
`progress_separable` is true only when progress was seen in an earlier round;
the summary reports the progress median from those trials alone.

The progress checks are one bounded milestone per task: accent handling for
A, owner filtering with closed tasks excluded for B, and for C the shared
module existing with at least one consumer importing it. C's milestone is
structural because a refactor preserves behaviour at every step — a
behavioural check passed before any work, at every snapshot. A snapshot that
already passes its milestone (A at 60, C at 80) records zero seconds and marks
it in `initial_checks`; that zero is not progress the target made.

Turns and provider tokens stay null on purpose. Collecting them needs a harness
that can read the target's own telemetry, which this runner is not; the design
forbids estimating either from output length, so a null is the honest value
until such a harness exists. The design's `repeated_commands` and
`repeated_file_reads` need the same harness plus a neutral reviewer, and are
not recorded here either.

A trial that ran is `accepted` and carries no `invalid_reason`, whether or not
it completed within budget. `invalid_reason` means the trial does not belong in
the sample at all — the target never started, or failed before accepting the
task.

The evaluator source comes from the fixture definition outside `project/`; final
acceptance stdout/stderr are saved beside `trial.json`. A final diff outside the
pre-registered task edit surface makes `completed` false. `--plan-seed` records
the deterministic arm order printed by `plan.py`; it does not launch any arm.
`--cohort-plan` adds strict next-pending enforcement and therefore requires
`--launch`.

### When a runner dies mid-trial

A claim records the process that took it. If that process is killed, its row
stays `running` and the next claim refuses to proceed, naming the dead holder.
Inspect that trial's directory, then release the row:

```text
python benchmarks/plan.py --plan .trials/cohort-plan.json --release A-60-handoff-r01
python benchmarks/plan.py --plan .trials/cohort-plan.json --status
```

Release is refused while the holder is still alive, since that would let one
row be claimed twice; a holder on another machine cannot be checked, and needs
`--force`. A trial that ran but is invalid ends its row as `invalid` rather
than `recorded`: the row is used, the cell is one replicate short, and
`--status` names every cell in that state. Topping a cell up means adding a
new pre-registered row, not re-running the old one silently.

## Validating a record

```text
python benchmarks/validate_trial.py .trials/A-60-handoff-r01/trial.json
```

The validator rejects malformed records, impossible completion states,
contradictory acceptance states, negative measurements, and common credential
markers. It is a publication guardrail, not a substitute for human redaction
review.

## Summarizing a cohort

The summary accepts schema-v3 and v4 records and groups by pinned agent, version,
model, effort, task, snapshot, and arm. It reports completion rate over accepted
trials, invalid attempts, median times, and missing accepted replicates without
pooling cells. It also emits empty cells for a cohort present in the input, so
missing snapshots remain visible:

```text
python benchmarks/summarize.py .trials --expected-replicates 3
python benchmarks/summarize.py .trials --format json
```

A trial set aside for a reason the record itself does not carry — a provider
outage, say — goes in an exclusions file mapping its id to that reason, so the
summary and any written report exclude the same trials:

```text
python benchmarks/summarize.py benchmarks/results/v03-records --exclusions benchmarks/results/v03-exclusions.json
```

Exclude for a cause independent of the outcome, and record the evidence for
it. Running over budget is an outcome, not a reason.
