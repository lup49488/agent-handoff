# Recovery Benchmark v0.4 Pilot Proposal

Status: five source sessions have run, under two interruption rules, and
none was interrupted: `D-r01` (v0.4 fixture) and `D-r02` (v0.5) under a
five-minute wall-clock cutoff, and `D-baseline-r03` under a first-progress
trigger. Every one finished first, so no recovery session has run and there is
no recovery-effect comparison. `D-handoff-r03` was withdrawn before it ran (see
[the observation](#what-the-first-progress-trigger-showed)). Plans registered
from now on stop the source session at a fixed 30 seconds and render the
Handoff arm's package at the stop; no pair has run under that rule yet.

## Why a new pilot

The current Snapshot 60 cohort, run after isolation, has four accepted
Baseline/Handoff pairs for task A (r03, r04, r11, r12) and three each for B and
C (r10–r12). All 20 trials completed. The A-60 r03 pair belongs to no surviving
plan file but meets the same bar as the rest: schema 4, neutral workspace,
passed preflight, and no contamination when its logs are re-scanned. It
therefore shows no completion-rate
separation, and completion-time differences are mostly within the runner's
five-second probe interval. The current Handoff condition starts with a
prepared `.agent-handoff/` package; the Codex isolation configuration disables
user skills in both arms. This estimates the value of having a prepared
recovery package for these fixtures, not whether the Skill automatically
creates useful state before a real interruption.

The pilot should answer the narrower end-to-end question:

> When a coding task is interrupted after meaningful but incomplete work,
> does having the agent-handoff Skill available from the beginning improve the
> quality or cost of completing the task in a fresh session?

It is exploratory and should not support a public effectiveness claim.

## Pilot task

Use a public, disposable Python fixture for a cross-module CSV export change.
The first v0.4 version tested a delimiter-only task. Its observed result and
the v0.5 revision are recorded below. The task should require touching a
CLI/parser, shared export logic and shared schema, while leaving the public
tests untouched. It should be calibrated so that:

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

## First live pilot result and difficulty revision

The pre-registered `D-r01` pair used Codex CLI 0.160.0 and GPT-5.6 Terra
Medium. Baseline passed full acceptance in 54.281 seconds; Handoff passed in
35.281 seconds. Both passed before the five-minute source cutoff, so both
recovery sessions were skipped by protocol. Baseline had no checkpoint;
Handoff had a present, valid checkpoint. Both trial records validate and show
no contamination. This confirms that the Skill was made visible and produced
state in the Handoff arm, but it does not estimate interruption recovery.
Preserve the pair as recorded; do not rerun or exclude either row.

The counterbalanced v0.5 `D-r02` pair also completed before interruption:
Handoff passed in 83.046 seconds with a valid checkpoint; Baseline passed in
101.796 seconds with no checkpoint. Both records validate, both passed full
acceptance before the cutoff, and neither shows contamination. Across both
pairs (four source runs), no recovery session was reached. The harder fixture
increased work time but remains too easy for the planned five-minute boundary;
do not expand the cohort on this task or treat these pairs as evidence about
recovery effectiveness.

The original `benchmarks/fixtures/task-d/` remains unchanged. The separate
`benchmarks/fixtures/task-d-v05/` fixture raises task complexity by combining
ordered CSV column projection, null rendering, strict pre-write validation,
CSV quoting/Unicode behavior, JSON compatibility, and shared schema metadata.
Its progress evaluator checks delimiter plus projection; full acceptance adds
null rendering, invalid-option no-truncation behavior, schema-backed JSON, and
the complete public suite. Model-free calibration confirms initial failure,
partial progress without acceptance, and reference completion. This proves
evaluator sensitivity, not that a live agent will still be working at five
minutes; only a future authorized pilot can establish that empirically.

The runner now accepts a replicate identifier, derives the pair and trial IDs
from it, preserves legacy `D-r01` plans, and refuses to register an identifier
already present in another plan or trial artifact. `r01`–`r03` are used;
register the next pair as `r04`.

The scope audit's allowed files are derived from the fixture: every file its
partial or reference variant changes, plus `HANDOFF.md`. A hand-written list
had left out `schema.py`, which the v0.5 prompt tells the target to edit and
the reference does edit, so every correct solution and the partial state itself
would have been scored out of scope. It went unnoticed because no recovery
session — the only phase the audit scores — had run, and because calibration
checked the evaluators but not the audit. Calibration now runs the partial and
reference states through the scope audit as well.

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
- Give source work a five-minute hard stop and recovery work a ten-minute
  cap. Record both phase durations separately; the runner must terminate the
  source process tree at the declared boundary.
- **Interrupt at a fixed time.** The source session is stopped 30 seconds
  after launch, unless full acceptance passes first. The same rule applies to
  both arms and is frozen into each plan (`interruption` in a schema-3 plan;
  the plan will not load if it differs). A session that completes first is
  still recorded as `completed_before_interruption`, never excluded. The two
  rules before it are described below; their plans still load as the rule
  they were registered under.
- **The Handoff arm is handed over as the product hands over.** At the stop,
  the harness runs `handoff pack` in the Handoff arm, which is what a failing
  `handoff run` or a `handoff switch` does at a handoff; an agent stopped
  mid-task has usually not rendered `HANDOFF.md` itself. Only rendering
  happens: if the source session never initialised tracking, nothing is
  created for it, and that is the treatment's own outcome. The record keeps
  both `checkpoint_at_stop` (what the agent left) and `checkpoint` (what
  recovery receives), with the pack's own result. The Baseline arm receives
  nothing, and a record that shows a pack for it fails validation.
- Never create, edit or repair checkpoint content in the harness.
- `D-r01` and `D-r02` ran under the first rule — wall-clock cutoff, no
  pack — and their schema-1 plans still read back as that rule. `D-r03` was
  registered under the second, first-progress rule (schema 2).

### What the first-progress trigger showed

The second rule stopped the source session the moment the fixture's progress
check first passed, probing about every 1.2 seconds (each check takes about
0.1 seconds). `D-baseline-r03` finished at 35.8 seconds without being stopped:
progress and full acceptance first passed in the same probe round. That made
five sessions of five, under both rules, in which the two checks first passed
together — 20.4, 35.8, 46.1, 56.4 and 87.3 seconds.

The agent writes the whole solution in one burst. Before that moment the
repository is untouched; after it, the task is done. There is no
partly-written state on disk to interrupt, at any probe interval. The work in
progress exists only in the agent's context — what it has read and decided —
which is exactly what a checkpoint is meant to carry across a stop, and what
a progress trigger can never see.

So the rule became a fixed time, and the question the pilot answers became
the one that matters most for this tool: interrupted before the code is
written, does a fresh session that receives the handoff package recover
faster or more reliably than one that starts over? Thirty seconds was
calibrated on the five sessions above and frozen before the next launch: it
interrupts four of five while leaving the agent time to explore and the skill
time to record. Fifteen would interrupt all five, but most likely before the
skill had written anything, leaving the two arms nearly identical.

`D-handoff-r03` was withdrawn, not run: with no Baseline recovery, the pair
could not yield a comparison whatever the Handoff arm did, and the rule it was
registered under had been retired. The withdrawal and its reason are in the
plan (`--withdraw`); the row is not deleted and was not chosen by outcome.
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
python -m benchmarks.recovery_pilot --register-plan .trials/task-d-v05-r04.json --replicate r04 --seed 20261015 --model "<pinned model slug>" --cli-version "<exact codex --version output>"
python -m benchmarks.recovery_pilot --plan .trials/task-d-v05-r04.json --status
```

Starting the next arm requires a separate, explicit `--launch`; do not include
it in preparation or review commands.
