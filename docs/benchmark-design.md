# Recovery Benchmark Design

This document defines how to test whether `agent-handoff` reduces the cost of
recovering an interrupted coding task. It is a measurement protocol, not a
claim that a benefit has already been observed.

## Decision

Decide whether the handoff package is good enough to recommend as a recovery
workflow for a target coding agent. A successful benchmark must show recovery
that is at least as reliable as a fresh start and does not trade a faster
restart for a lower-quality final change.

## Scope and boundary

- Compare a fresh target-agent session with the same target agent resuming from
  the `agent-handoff` package.
- Start with one target agent and one pinned model/version. Add other agents as
  separate cohorts; never merge their token or turn counts into one average.
- Use disposable local repositories and test-only credentials. Do not capture
  private conversations, provider tokens, or session files.
- Record the source commit, tool version, task fixture revision, operating
  system, target-agent version, model, and all configured budgets for every
  trial.

## Tasks and interruption snapshots

Use three public, self-contained fixtures that can be evaluated mechanically:

| Task | Shape | Required acceptance check |
|---|---|---|
| A | Small bug in one module | Focused regression test passes |
| B | Medium feature touching API and tests | Feature tests and public interface check pass |
| C | Multi-file refactor | Full suite passes and a pre-registered structural assertion holds |

For each task, prepare three immutable interruption snapshots at approximately
30%, 60%, and 80% of the pre-registered implementation path. Each snapshot
contains the same working tree, uncommitted changes, source-task command log,
and expected next work. The snapshots are created once and copied for both
conditions; do not let the baseline and handoff arms start from different work.

An interrupted source process is useful for an end-to-end demonstration, but
the comparative benchmark uses prepared snapshots so the interruption point is
repeatable.

## Experimental arms

For each task, interruption point, and replicate, create two fresh copies of
the same snapshot.

| Arm | Target-agent input | Files available to the target |
|---|---|---|
| Baseline | Original user request plus the ordinary repository state | Worktree and Git history only |
| Handoff | Original user request plus an instruction to inspect the package | Same worktree plus `.agent-handoff/` and `HANDOFF.md` |

The target session must be fresh in both arms. Do not give it a transcript,
hidden summary, prior terminal history, or a reused session. The evaluator may
state the time and token/turn budget, but must not give condition-specific
implementation hints.

Randomize arm order within each task/snapshot pair to reduce time-of-day,
rate-limit, and evaluator-learning effects. Keep a trial invalid, rather than
silently rerunning it, when the target CLI fails before accepting the task or
the environment changes.

## Metrics

### Primary KPI: recovery completion rate

`completed accepted trials / accepted trials`

A trial is complete only when every pre-registered acceptance check passes
within the stated budget and the final diff has no out-of-scope change. This is
the quality guardrail for every speed or cost metric.

### Comparative KPI: recovery overhead

Report each component separately, then compare the median within the same
agent/model cohort:

| Metric | Definition | Source |
|---|---|---|
| Time to verified progress | Wall-clock time from target launch to the first pre-registered test passing | Timestamped command log |
| Completion time | Wall-clock time from target launch to all acceptance checks passing | Timestamped command log |
| Target turns | Number of user-visible target turns required to complete | Harness or transcript index, if available |
| Target tokens | Provider-reported tokens consumed after target launch | Provider telemetry, when available |
| Repeated exploration | Commands or file reads the source already performed that the target repeats before verified progress | Normalized command/file log plus manual audit |

Do not estimate token counts from prose length, and do not substitute model
output characters for provider-reported tokens. When a platform does not expose
tokens, report time, turns, and repeated exploration only.

### Guardrails

- Final acceptance checks pass and the final diff remains in task scope.
- `handoff verify` succeeds before the Handoff target starts.
- No secret, session transcript, or provider credential appears in the result
  bundle.
- The recovery package does not alter tracked source files before the target
  makes its own change.

## Trial record

Store one JSON record per trial, with paths redacted or made fixture-relative.
`accepted` means the trial belongs in the sample; `invalid_reason` is set only
when it does not. Running past the budget is an outcome, not a disqualification,
so it is carried by `budget_exceeded` and the two are never set together:

```json
{
  "trial_id": "B-60-handoff-r03",
  "task": "B",
  "snapshot": "60",
  "arm": "handoff",
  "replicate": 3,
  "source_commit": "<sha>",
  "handoff_version": "<version>",
  "target": {"agent": "<name>", "version": "<version>", "model": "<model>"},
  "budget": {"wall_seconds": 1800, "target_turns": 20},
  "accepted": true,
  "completed": true,
  "budget_exceeded": false,
  "first_verified_progress_seconds": 242,
  "completion_seconds": 611,
  "target_turns": 7,
  "provider_tokens": null,
  "repeated_commands": 2,
  "repeated_file_reads": 4,
  "invalid_reason": null
}
```

Keep raw transcripts outside the published result set. Publish the fixture,
manifest, normalized command log, acceptance output, and aggregation script so
the result can be reproduced without exposing private task content.

## Phases and decision gates

1. **Instrumentation dry run:** one pair for each task verifies cloning,
   logging, acceptance checks, redaction, and package validation. Do not report
   comparative percentages.
2. **Exploratory run:** three accepted replicates per arm/task/snapshot
   (`3 tasks × 3 snapshots × 2 arms × 3 = 54` trials for one cohort). Report
   medians and every raw trial; label the result exploratory.
3. **Claim-ready run:** ten accepted replicates per cell (`180` trials for one
   cohort). Report completion-rate confidence intervals and paired median
   differences by task/snapshot. Pre-register exclusions before running it.

Promotion rule: public copy may claim reduced recovery overhead only when the
Handoff arm has no worse completion rate or guardrail failure, and shows a
consistent reduction in the pre-registered comparative metric across the three
tasks. A single favourable task, a pooled cross-model average, or a failed
baseline retry is not sufficient.

## Open decisions before instrumentation

- Select the first target agent/model and its reproducible invocation method.
- ~~Author the three fixtures and their interruption snapshots without relying
  on private repositories.~~ Done: `benchmarks/fixtures/`, nine snapshots, each
  in its own Git repository with the pre-task state committed.
- Choose the harness that can collect target turns and provider telemetry
  without storing credentials or full conversations. Until one exists,
  `run_trial.py` records time and completion and leaves turns and tokens null;
  it does not estimate them.
- Define the neutral reviewer procedure for classifying repeated file reads and
  out-of-scope final diffs.
