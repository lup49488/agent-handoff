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

Run the trials in the order it prints. The same seed reprints the same plan on
any machine, so a single cell can be re-run later without disturbing the rest.

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

### What the record contains

| Field | Source |
|---|---|
| `completion_seconds` | target launch until the process exits |
| `first_verified_progress_seconds` | a probe re-runs the acceptance check every few seconds and records the first pass |
| `completed` | the acceptance check against the final tree |
| `budget_exceeded` | the target was still running at the wall clock |
| `target_turns`, `provider_tokens` | **null** |

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

## Validating a record

```text
python benchmarks/validate_trial.py .trials/A-60-handoff-r01/trial.json
```

The validator rejects malformed records, impossible completion states,
contradictory acceptance states, negative measurements, and common credential
markers. It is a publication guardrail, not a substitute for human redaction
review.
