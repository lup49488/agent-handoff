# Recovery Benchmark v0.4 Pilot Proposal

Status: fixture, offline calibration, and the opt-in two-phase runner are
implemented. This milestone did not use the live-launch switch; no model trial
has been run.

## Why a new pilot

The current Snapshot 60 cohort has three accepted Baseline/Handoff pairs per
task. All 18 trials completed. It therefore shows no completion-rate
separation, and completion-time differences are mostly within the runner's
five-second probe interval. The current Handoff condition starts with a
prepared `.agent-handoff/` package; the Codex isolation configuration disables
user skills in both arms. This estimates the value of having a prepared
recovery package for these fixtures, not whether the Skill automatically
creates useful state before a real interruption.

The v0.4 pilot should answer the narrower end-to-end question:

> When a coding task is interrupted after meaningful but incomplete work,
> does having the agent-handoff Skill available from the beginning improve the
> quality or cost of completing the task in a fresh session?

It is exploratory and should not support a public effectiveness claim.

## Pilot task

Create one public, disposable Python fixture for a cross-module CSV export
change. The task should require touching a CLI/parser, shared export logic,
and tests. It should be calibrated so that:

- the initial repository fails a fixture-owned regression check;
- a separate progress check can pass while full acceptance still fails;
- full acceptance covers CSV quoting and delimiter behavior, invalid-input
  handling, and unchanged JSON behavior;
- the task prompt and repository contain all requirements, with no condition-
  specific hints;
- the recovery point ordinarily leaves meaningful work to do, rather than a
  nearly complete task that both arms finish immediately.

The progress and completion evaluators must live outside the target's writable
project, be hashed in each record, and be tested against both the initial and a
known-complete fixture before any model launch. If the task regularly completes
before the interruption trigger, revise the fixture before expanding the
cohort; do not select or discard individual trials based on their outcome.

## Conditions

One matched pair starts each arm from the same initial fixture revision,
prompt, model/version, effort, OS, and budgets. Each arm has two fresh sessions:

| Phase | Baseline | Handoff |
|---|---|---|
| Source work | Skill unavailable; ordinary repository and Git only | Only the agent-handoff Skill is enabled; it may initialize and checkpoint according to its normal instructions |
| Interruption | Same pre-registered trigger and hard-stop policy | Same trigger and hard-stop policy; preserve only the package actually produced before interruption |
| Recovery | Fresh session; worktree and Git history only | Fresh session; same worktree and Git history plus the generated `.agent-handoff/` and `HANDOFF.md` |

Disable persistent memories and every unrelated user Skill in both arms. Do not
share chat history, terminal output, process memory, or recovery-session
transcripts. Record the exact Skill version/hash and verify the Baseline did not
load it. The Handoff arm's failure to initialize, checkpoint, or verify is a
real treatment outcome, not an exclusion or a reason to rerun.

This is a total-workflow comparison: source worktrees may differ because the
Skill is active in only one arm. A later same-snapshot package-ablation test
can isolate the recovery artifact itself, but it is not part of this pilot.

## Interruption and budgets

- Pin the exact Codex CLI version, model, and effort before pre-registration;
  keep the same configuration for source and recovery sessions in both arms.
- Give source work a fixed five-minute cap and recovery work a fixed ten-minute
  cap. Record both phase durations separately; the runner must terminate the
  source process tree at the declared boundary.
- Use a fixture-owned progress predicate to make the interruption state
  inspectable. The trigger must be condition-independent and frozen before
  launch. Full acceptance passing before interruption is recorded as
  `completed_before_interruption` and makes that task unsuitable for a
  recovery-effect estimate; it is not silently excluded from the pilot log.
- The Handoff package must be captured exactly as it exists at the stop. Never
  create or repair a checkpoint in the harness after interruption.
- Randomize which arm runs first. Run one process at a time. Pre-register the
  pair and write each trial record before the first model call.
- Use only synthetic/public task data. Keep authentication material and raw
  provider sessions out of result bundles.

## Outcomes

Primary outcome: after interruption, did the fresh recovery session pass every
fixture-owned acceptance check within its ten-minute budget, with no
out-of-scope changes?

Report separately for each arm and each pair:

1. source-phase progress and completion at the interruption boundary;
2. automatic package creation and `handoff verify` result;
3. recovery completion and final-diff quality;
4. time to first verified post-interruption progress and full completion;
5. repeated test runs, file reads, and commands, audited from structured logs
   without retaining full private transcripts.

Do not use stderr's `tokens used` line as provider or billing telemetry. The
latest records leave `provider_tokens` null and contain an OAuth refresh
warning. A token-cost claim requires independently verified provider
telemetry, plus a cleanly recorded authentication state.

## Pilot gate and scale-up

First do fixture-only validation: verify the initial failure, progress
predicate, complete acceptance, scope audit, source-stop behavior, and
package capture without invoking a model. Then run exactly one matched pair
(four model sessions: source and recovery for each arm). This pilot checks
whether the treatment was delivered as designed; it is not evidence of
effectiveness.

Proceed to a broader exploratory cohort only if:

- the Skill is visibly available only in the Handoff source/recovery sessions;
- the generated package is present before the interruption and verifies
  without harness assistance;
- both arms receive the same task and the same trigger/budget;
- logs and acceptance results are complete and schema-valid; and
- the task leaves meaningful recovery work at the boundary.

If those checks pass, add two more task families and pre-register five paired
replicates per task as an exploratory stage, with arm order balanced across
replicates. Keep task-level results separate. Reconsider a ten-pair-per-task
claim-oriented study only after the pilot establishes useful outcome variance
and reliable instrumentation.

## Required harness work

- [x] Add Task D's initial, partial-progress, and reference-complete fixture
  variants with fixture-owned progress and completion evaluators.
- [x] Add an offline calibration command and test proving the initial state
  fails, partial state passes progress but fails full acceptance, and reference
  state passes both evaluators and all public tests.
- [x] Add an explicit two-phase source/interruption/recovery trial state machine.
- [x] Add a treatment allowlist that enables exactly the handoff Skill while
  retaining memory and unrelated-Skill isolation.
- [x] Capture and validate the package at the interruption boundary without
  injecting additional hints into either recovery prompt.
- [x] Record phase-specific CLI/model/version, budget, skill hash, trigger time,
  process exit, package verification, evaluator hashes, acceptance evidence,
  contamination findings, progress timings, and a final diff snapshot/hash.
- [x] Add adversarial tests for early completion, no progress before timeout,
  missing/invalid checkpoint, process cleanup, and interrupted plan claims.

Offline calibration is `python -m benchmarks.recovery_pilot --calibrate` and
never invokes an agent. Plan registration and status inspection are also
model-free. Real work requires a pre-registered plan and the explicit
`--launch` flag; the runner pins one Codex version/model, checks actual
prompt-input Skill visibility before each session, and runs one arm at a time.
Do not use `--launch` until the user separately approves the live pilot and its
quota budget.

Example model-free registration (replace both placeholders with the exact
configuration chosen for this run):

```powershell
python -m benchmarks.recovery_pilot --register-plan .trials/task-d-v04.json --seed 20261009 --model "<pinned model slug>" --cli-version "<exact codex --version output>"
python -m benchmarks.recovery_pilot --plan .trials/task-d-v04.json --status
```

Starting the next arm requires a separate, explicit `--launch`; do not include
it in preparation or review commands.
