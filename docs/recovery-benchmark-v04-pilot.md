# Recovery Benchmark v0.4 Pilot Proposal

Status: three pairs ran under a fixed 30-second stop — `D-r04`, `D-r05`,
and `D-r06` with a revised skill — and showed that 30 seconds comes before the
skill has anything worth handing over. The window in which a package can help
was then measured directly in four uninterrupted sessions, and plans now stop
the source session at 45 seconds, inside it (see
[the measured window](#the-window-a-package-can-help-in)). The first pair under
that rule, `D-r07`, yielded no comparison: its Handoff source finished at 33.5
seconds, before the stop. `D-r08` is the first pair to run entirely as
designed — the Handoff recovery received a substantive checkpoint and no code
— and it shows why this fixture cannot measure what the package is for (see
[D-r08](#d-r08-the-design-working-and-what-it-shows)). Before them, five source sessions under two earlier rules all finished first:
`D-r01` (v0.4 fixture) and `D-r02` (v0.5) under a five-minute wall-clock
cutoff, and `D-baseline-r03` under a first-progress trigger. `D-handoff-r03`
was withdrawn before it ran (see
[the observation](#what-the-first-progress-trigger-showed)). Plans now stop the
source session at a fixed 30 seconds and render the Handoff arm's package at
the stop.

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
already present in another plan or trial artifact. `r01`–`r08` are used;
register the next pair as `r09`.

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

### D-r04: the first interrupted pair

Registered under the fixed-time rule (seed 20261015, Baseline first). Both
source sessions were stopped by the trigger — at 30.39 and 30.53 seconds —
with nothing yet on disk, as the observation above predicted. The Handoff
session had initialised tracking but not rendered `HANDOFF.md`; the harness's
pack produced a package that verified. Both recoveries passed full acceptance
within budget, in scope, with no contamination and with every process tree
ended.

| | Baseline | Handoff |
|---|---:|---:|
| Recovery reaches full acceptance | 72.4 s | 112.5 s |
| Recovery session exits | 89.3 s | 147.4 s |

The Handoff recovery was about 40 seconds slower. This is one pair, run in
one order: it is exploratory and supports no claim in either direction.

What it cannot show is why. Only a hash of the package was kept, and the
workspace is deleted after each trial, so whether the recovery received a
checkpoint or only the task is unknown. The package's hash also changed during
recovery, so the recovery session updated its handoff state while it worked —
consistent with v0.3, where the Handoff arm spent longer after finishing, but
not evidence of a cause. Records from now on keep the package's text as the
recovery received it (`interruption.package_content`) and as recovery left it
(`recovery.package_content_after`). The next pair should run Handoff first.

### D-r05: the package recovery received

Registered under the same rule with Handoff first, to balance D-r04's order
(seed 20261016, chosen because it orders Handoff first; the seed is otherwise
arbitrary). Both source sessions were stopped by the trigger — at 30.38 and
30.47 seconds — with nothing yet on disk. Both recoveries passed full
acceptance within budget, in scope, with no contamination and with every
process tree ended. This was the first pair whose record kept the text of the
package.

| | Baseline | Handoff |
|---|---:|---:|
| Recovery reaches full acceptance | 66.7 s | 39.6 s |
| Recovery session exits | 98.4 s | 63.7 s |

The checkpoint the Handoff recovery received, written by the skill in the
first 30 seconds, was in full:

```text
# Current Objective
Extend export CSV projection, delimiter, and null rendering while preserving compatibility
# Completed
- Initial repository inspection started; no pre-existing worktree changes reported.
# Next Suggested Step
Inspect source and public tests, implement validated CSV options, then run unittests.
```

It restates the task and records that inspection had begun. It holds no
finding about the code, no decision and no plan the prompt does not already
give.

### What the two pairs show, and what they do not

| Pair | Ran first | Baseline | Handoff | Handoff − Baseline |
|---|---|---:|---:|---:|
| D-r04 | Baseline | 72.4 s | 112.5 s | +40.1 s |
| D-r05 | Handoff | 66.7 s | 39.6 s | −27.1 s |

- **The pairs disagree, and in each the arm that ran first was faster.** That
  may be an order effect — provider latency drifting between back-to-back
  sessions, for instance — or chance. Two pairs cannot tell which; it is why
  the order has to stay balanced, and why no direction can be read from them.
- **The package carried almost nothing.** At 30 seconds the skill had written
  only a restatement of the task, so the Handoff recovery received little the
  Baseline recovery did not already have in its prompt. Whatever made D-r05's
  Handoff recovery faster, it is hard to credit to the package.
- **Recovery time varies widely** within an arm, from 40 to 112 seconds
  across these four sessions. Separating a real difference from that spread
  needs many more pairs than two.

The finding with the most consequence is about the product rather than the
experiment: in its first 30 seconds of work, the skill records almost nothing
a successor could use. A checkpoint becomes worth handing over only once it
holds what the agent has learned — what the code does, what it decided, what
it would do next — and on this task that point comes after the moment a
stop is most likely to hurt. Whether the skill should checkpoint earlier and
with more substance, for instance once the code is read and a plan is
chosen, is a question for the skill, and the next thing worth testing.

### D-r06: a skill that checkpoints before editing

The skill was revised (commit `86d7b9b`, SKILL.md sha256 `5df78724…`; D-r01
to D-r05 used `b5e6403a…`) to ask for the first checkpoint once the relevant
code is read and an approach chosen, before any edit, and to skip a
checkpoint that only restates the task. Registered Baseline first (seed
20261017), alternating with D-r04 and D-r05.

| | Baseline | Handoff |
|---|---:|---:|
| Source stopped by the trigger | 30.2 s | 30.1 s |
| Recovery reaches full acceptance | 88.0 s | 69.3 s |
| Recovery session exits | 106.1 s | 106.6 s |

Both recoveries passed full acceptance within budget, in scope, with no
contamination and every process tree ended.

**The package held no checkpoint at all.** The Handoff source session started
tracking about 15 seconds in and had written no checkpoint when it was stopped
at 30: it skipped the empty checkpoint, as the revision asked, and had not yet
reached the substantive one. The recovery received only the task and an empty
repository.

**The revision does what it asks — later.** The checkpoint the Handoff
*recovery* session wrote opens with the kind of entry the revision was meant
to produce: which four files it inspected, what the CSV and JSON paths
currently do, and that no code had changed yet; then decisions with their
reasons. It is substantive. It simply comes after 30 seconds.

**So the 18.7-second difference is noise.** With no checkpoint in the package,
the two recoveries started from the same information; whatever separated
them, it was not the treatment. That makes D-r06 a measure of the noise
floor: across the three pairs, Handoff − Baseline was +40.1, −27.1 and −18.7
seconds, and only in D-r05 did the package hold anything at all — a
restatement of the task.

**The structural finding.** A handoff package can only help in a window: from
the moment the first substantive checkpoint is written until the task is
complete. Before it there is nothing to hand over; after it there is nothing
to recover. On this task the agent starts tracking around 15 seconds, has not
checkpointed by 30, and writes the whole solution between 35 and 90 seconds,
so the window is late and may be narrow. A fixed stop has to fall inside it to
test the package at all, and 30 seconds falls before it. Where the window is,
and how wide, is the next thing to measure — from uninterrupted Handoff
sessions, whose `checkpoints.jsonl` times the first checkpoint and whose
probes time completion — before choosing another stop.

### The window a package can help in

Four Handoff source sessions with the revised skill were run to their end,
uninterrupted (`--measure-window`; records in
`benchmarks/results/window-2026-10-16/`). Each round of the probe also read
`state.md` and kept every version of the checkpoint with when it first
appeared, since the file is overwritten as the session goes on.

| | Tracking starts | First checkpoint | Completion | Window |
|---|---:|---:|---:|---:|
| W01 | 12.6 s | 20.7 s | 39.3 s | 18.6 s |
| W02 | 26.5 s | 26.5 s | 76.3 s | 49.8 s |
| W03 | 17.3 s | 38.1 s | 93.9 s | 55.8 s |
| W04 | 14.9 s | 31.1 s | 68.1 s | 37.0 s |

Every first checkpoint was written before any edit and was substantive: the
files read and what they currently do, the approach chosen and why, and the
first change to make. W04's, for example, records that the CLI opens the
destination before dispatching, that CSV writes a fixed header while JSON
indexes each tuple itself, and the decision to centralise the schema and
validate CSV options before opening the path. The revision does what it was
meant to; D-r06 was simply stopped before it got there.

**Choosing the stop.** At 30 seconds only two of the four were inside their
window. 38.5 seconds caught all four, but by under a second at either end. At
45 seconds three of four are inside — W01 had already finished — with at
least 6.9 seconds since the first checkpoint and 23 seconds before completion,
a margin ordinary variation should not erase. New plans (schema 4) stop at 45
seconds; schema-3 plans (D-r04 to D-r06) still load as 30.

At 45 seconds the Handoff arm should, most of the time, hand over a checkpoint
like the ones above while neither arm has yet changed a file. That is the
comparison the pilot exists to make, and the first one it has been able to.

### D-r07: the first pair at 45 seconds

Registered under the 45-second rule with Handoff first (seed 20261019),
alternating with D-r04 to D-r06, and the revised skill (`5df78724…`).

| | Handoff | Baseline |
|---|---|---|
| Source | finished the task at 33.5 s, before the stop | stopped at 45.5 s, nothing on disk |
| Recovery | not run: nothing to recover | reached full acceptance at 32.4 s |

Both records validate, with no contamination and every process tree ended.
The Handoff source finished first, as W01 had in the window measurement; at
45 seconds this was expected in about one session in four. The pair is
recorded as it ran and yields no comparison — the Handoff arm has no recovery
to set against the Baseline's. It is not excluded and was not re-run.

Two things it does show. The checkpoint the Handoff source left at completion
is again substantive: the four files read and what CSV and JSON currently do,
the decision to centralise the field mapping in `schema.py` and why, and the
test run. And the Baseline recovery's 32.4 seconds is the fastest recovery yet
observed, against 66.7 to 112.5 seconds in earlier pairs — further evidence
that recovery time varies widely enough that only many pairs could show a
difference the package makes.

### D-r08: the design working, and what it shows

Registered Baseline first (seed 20261020) under the 45-second rule with the
revised skill. For the first time every part of the design ran as intended:
both sources were stopped by the trigger (45.3 and 45.1 seconds) with nothing
on disk, and the Handoff recovery received a substantive checkpoint:

```text
# Completed
- Inspected CLI and writer modules: CSV currently hard-codes fields and writes
  raw positional rows; JSON independently maps indices 0..2.
# Decisions
- Put the sole field-to-index projection in schema.py. CSV parses/validates
  delimiter and column selection before cli.export_rows opens the destination;
  JSON uses schema record mapping and ignores CSV-only options.
# Next Suggested Step
Patch schema.py, csv_export.py, json_export.py, and cli.py, then run unittest.
```

| | Baseline | Handoff |
|---|---:|---:|
| Recovery reaches full acceptance | 55.5 s | 63.5 s |
| Recovery session exits | 94.4 s | 88.0 s |

Both recoveries passed full acceptance within budget, in scope, with no
contamination and every process tree ended.

**The recovery redid the work the checkpoint described.** The checkpoint the
Handoff recovery left at the end adds, as a step it completed, "Inspected
schema.py, cli.py, csv_export.py, json_export.py, and public tests" — the
inspection it had been told was done — and lists the same decision three
times, re-derived. It did not take the plan on trust; it re-read every file
and reached the plan again.

That is not a failure of the agent. To change a file it has to have read the
file; a checkpoint can tell it what it will find and what to do, but cannot
put the code into its context. On this task the progress made in the first 45
seconds *is* reading four short files and choosing an approach, and reading
them again costs seconds. The most a package can save here is the deciding,
and in D-r08 that was not enough to outweigh reading the package and keeping
the handoff state current during recovery.

### What eight pairs establish

Four pairs reached a recovery in both arms under a fixed stop: Handoff minus
Baseline was +40.1 s (D-r04), −27.1 s (D-r05), −18.7 s (D-r06) and +8.0 s
(D-r08). Only in D-r08 did the package carry a substantive checkpoint. No
direction can be read from this, and recovery time alone ranged from 32 to
113 seconds.

What the pilot has established is about the instrument, not the effect:

- **The harness works.** Isolation, contamination scanning, a stop that lands
  where it is meant to, a package rendered as the product renders it, and a
  record that keeps what recovery received and left — all held in practice.
- **The revised skill records what it should,** before the first edit,
  substantively, in every session observed since the revision.
- **This fixture cannot show what the package is for.** Its whole
  pre-interruption progress is reading four short files and choosing an
  approach, which a successor must redo to edit them and can redo in seconds.
  The package can only save time where what was learned is expensive to learn
  again and cannot be recovered by reading what is on disk: an investigation
  that ran experiments, ruled out causes, or hit dead ends — "tried X; it fails
  because Y" — or a codebase too large to re-explore cheaply.

The next pilot needs a task built that way: one where reaching the
pre-interruption state takes real investigation whose findings are not
visible in the files, so that a fresh session without them would have to
repeat it. Adding pairs on task D would cost quota without being able to
answer the question.

### Task E: an investigation the files do not record

`benchmarks/fixtures/task-e` is built for that. A small, stdlib-only daily
sales report disagrees with finance's figures for store B on four days;
stores A and C match. The prompt asks for `python check_report.py` to pass
without touching the data, the expected figures or the store settings, and
points at `debug_report.py`, which prints for one store how each transaction
is placed: its UTC time, the local time the report computes, and the day it
is booked to.

Two bugs cause the four wrong days, and neither can be seen by reading the
code that is wrong:

- **An offset loses its sign on the minutes.** `parse_offset` turns `-03:30`
  into −02:30. Store B is the only public store with a negative offset that
  has minutes, so its local times come out an hour late, and sales made
  between 03:00 and 03:59 — which belong to the previous business day, since
  the day starts at 04:00 — are booked to the next one. `debug_report.py --store B` shows
  the zone as `UTC-02:30`; the line that computes it looks right.
- **A refund is booked to its UTC date.** `refund_day` uses the UTC date
  instead of the local business day, so B8, a refund at 22:30 local on
  09-14, is booked to 09-15. Fixing the first bug alone leaves this day
  wrong, which is what makes the fixture's partial state.

The project also carries what an investigation has to rule out: a `FIXME`
in `money.py` about rounding drift on store B, a note in `NOTES.md` blaming
it, and a `TODO` in `refunds.py` about booking refunds to the original sale's
day — all wrong. What a session learns before the stop is therefore which
causes were tried and ruled out and what `debug_report.py` showed, none of
which is on disk unless it was written down.

The evaluators embed rows the project never sees. `progress.py` holds the
sales of every store plus a hidden store D at −09:30, and passes once offsets
are parsed correctly; `acceptance.py` adds refunds whose UTC date differs
from their business day in stores A, C and D, so a fix that special-cases
`-03:30` or store B fails both. Calibration is the same as task D's, except
that the public check is `check_report.py` (`public_check.txt`) instead of a
unittest suite:

| State | progress | acceptance | `check_report.py` |
|---|:-:|:-:|:-:|
| initial | fail | fail | fail |
| partial (offset fixed) | pass | fail | fail |
| reference (both fixed) | pass | pass | pass |

Plans now record their fixture (`--fixture task-e` when registering, measuring
a window or calibrating), and pair and trial ids take the fixture's letter:
`E-r01`, `E-baseline-r01`. Plans without the field are task D.

Before any pair, the stop time has to be chosen again: 45 seconds was where
task D's sessions had read the code and not yet edited it, and an
investigation is expected to take longer. That needs `--measure-window
--fixture task-e` sessions, which spend quota.

### Task E's window: there is none

Four uninterrupted sessions (`benchmarks/results/window-task-e-2026-10-16/`,
the same model, effort and CLI as task D's), now also timing the first edit to
a file the solution changes:

| | first checkpoint | first edit | progress | acceptance | exit |
|---|---:|---:|---:|---:|---:|
| W01 | 34.8 s | 37.2 s | 37.2 s | 48.3 s | 67.7 s |
| W02 | 21.3 s | 22.5 s | 22.5 s | 30.3 s | 42.5 s |
| W03 | 29.6 s | 32.0 s | 32.0 s | 33.2 s | 48.1 s |
| W04 | 40.3 s | 45.1 s | 45.1 s | 46.3 s | 60.1 s |

Every session solved the task faster than task D's did, and none
investigated in the sense the fixture was built for. They read the code,
ran `check_report.py` and `debug_report.py` once, and saw both causes: the
zone header printed `UTC-02:30` against `-03:30` in `stores.ini`, and
`refund_day` visibly contradicts the rule `NOTES.md` states. None of the
planted misleads was followed. The checkpoint was written 1.2 to 4.8 seconds
before the first edit, so a stop placed after the findings and before an
edit would have to land in a window a few seconds wide whose position varies
by twenty.

What this shows about the design: a bug that one run of a provided tool
exposes is not an investigation, however little the code shows. For the
findings to be expensive to repeat, finding them has to cost something a
successor would pay again — runs that take real time, causes that only
appear when inputs are varied, or more plausible causes than can be read in
a minute. Task E as it stands cannot measure the package either.

### Task E v2: a cause found in the data, not the code

`benchmarks/fixtures/task-e-v2` keeps task E's report and its offset bug and
replaces what made it easy:

- **The ledger is four weeks, 1,582 rows,** across five tills, generated by
  `generate.py` from the reference solution. Store B is wrong on 16 of 28
  days, too many to read off one store-day at a time.
- **The tools no longer hand over the answer.** `debug_report.py` prints the
  offset as `stores.ini` gives it and the local times the report computes;
  nothing prints `UTC-02:30`. The refund bug is gone; refunds are booked
  correctly.
- **The second cause is in the data.** Till B2 re-sent the end of its
  2026-09-17 export the next day and till B1 the end of 09-23's; the import
  gave the same receipts new ids. No line of code is wrong in a way reading
  shows: `NOTES.md` says only that ids are assigned on import and that each
  till numbers its own receipts. Fixing the offset leaves two days wrong, and
  explaining them means comparing a day's rows with finance's figure and
  noticing receipts that occur twice.

A fix has to deduplicate by till and receipt, keeping the first import.
`scope.txt` puts every module of the `report` package in scope, since the
deduplication is right in the loader, the aggregation or a module of its own.
The evaluators read the project's ledger only after checking its sha256, and
add rows the project never sees: store D at −09:30, a re-import at store C,
and two genuine sales at store A in the same minute for the same amount on
different receipts. Wrong fixes tried against them:

| Fix | progress | acceptance | `check_report.py` |
|---|:-:|:-:|:-:|
| offset only (partial) | pass | fail | fail |
| dedupe by store and receipt | fail | fail | fail |
| dedupe by till, time and amount | fail | fail | pass |
| dedupe store B, or tills B1/B2, only | pass | fail | pass |
| reference | pass | pass | pass |

Progress is every genuine sale on its right business day, so a fix that
drops genuine sales fails it as well.

### Task E v2's window: it exists

Four uninterrupted sessions (`benchmarks/results/window-task-e-v2-2026-10-16/`):

| | first checkpoint | first edit | progress | acceptance | exit |
|---|---:|---:|---:|---:|---:|
| W01 | 31.1 s | 36.2 s | 36.1 s | 97.0 s | 123.5 s |
| W02 | 39.9 s | 42.5 s | 42.4 s | 63.9 s | 77.8 s |
| W03 | 51.2 s | — | 55.0 s | 119.4 s | 164.6 s |
| W04 | 29.9 s | 33.7 s | 33.6 s | 76.5 s | 94.5 s |

All four passed acceptance with no contamination, and all four found both
causes the way the fixture intends: the offset by reading, then — with the
checker down from 16 mismatches to two — the re-imported receipts by
comparing a day's rows with finance's figure. Every one keyed the
deduplication on store, till and receipt.

**There is now a window.** Between progress (34–55 s) and acceptance (64–119
s) each session spent 21 to 64 seconds investigating the two remaining days,
and what it learned — which receipts, which tills, that the copies are exact
and why the key must include the till — was nowhere on disk until the fix
was. A stop at 60 seconds would have landed inside that stretch in all four.

**Whether the skill fills it, these records cannot say.** Each shows two
checkpoint versions, one before the first edit and one at exit, and the one
at exit describes the duplicates. That looked like a skill that checkpoints
before its first edit and then not until it is done, and the skill was
revised for it (below). But the checkpoint was only looked at while progress
was being probed, which stops once progress passes: from progress to exit it
went unwatched, and a version written inside the window would be recorded
as seen at exit. The window is exactly what these records leave unobserved.

W03 recorded no first edit for the same reason: the edit landed between the
snapshot and the progress check of one probe round, progress passed, and the
progress probe — the only place edits were looked for — was not called
again. Edits are now looked for every round, each one is recorded with its
time and files, and a failed look is recorded rather than swallowed.

### W05–W08: the revised skill, measured with the same blind spot

The skill now asks for a checkpoint before every edit that follows new
findings, not only the first. Four more sessions ran with it before the
checkpoint watcher was fixed:

| | first checkpoint | first edit | acceptance | exit |
|---|---:|---:|---:|---:|
| W05 | 57.0 s | 60.9 s | 104.4 s | 141.6 s |
| W06 | 25.2 s | 27.8 s | 53.2 s | 85.4 s |
| W07 | 33.8 s | 37.7 s | 65.5 s | 79.5 s |
| W08 | 39.8 s | 43.7 s | 106.7 s | 147.0 s |

All passed acceptance, uncontaminated, with the same two causes found the
same way, and again the window from the offset fix to acceptance ran 25 to
63 seconds. They show the same two versions as W01–W04 and say no more
about the window, for the same reason; the second edit's time is likewise
only bounded by acceptance, since the probes stop when acceptance passes.

The checkpoint is now watched every round until acceptance, and each record
also keeps the time of every checkpoint write from the package's own
`checkpoints.jsonl`, which polling cannot miss.

### W09–W12: the findings are written down, seconds before they are used

Four sessions with the revised skill and the fixed watcher. "Duplicates
recorded" is the first checkpoint write whose text describes the re-imported
receipts:

| | offset checkpoint | progress | duplicates recorded | acceptance | exit |
|---|---:|---:|---:|---:|---:|
| W09 | 28.9 s | 33.0 s | 74.9 s | 83.6 s | 106.6 s |
| W10 | — | — | 48.6 s | 123.2 s | 157.7 s |
| W11 | 57.7 s | 61.7 s | 95.7 s | 108.6 s | 145.9 s |
| W12 | 16.7 s | 21.3 s | 34.7 s | 43.1 s | 54.4 s |

All four passed acceptance uncontaminated. **The revised skill does what it
says:** in every session a checkpoint describing the duplicates — receipts,
tills, the sums they add, the key that identifies them — was written before
the change that removes them. W10 found both causes before its first edit,
checkpointed both at 48.6 s, and made both changes at once at about 123 s;
from 48.6 s on, its package held everything it had learned and its disk
nothing.

**But it says it late.** The rule is to checkpoint before the edit, and the
sessions did so immediately before it. In W09, W11 and W12 the investigation
of the duplicates ran 42, 34 and 13 seconds from progress, and its findings
reached the package only in its last moments, 8.7, 12.9 and 8.4 seconds
before acceptance. A stop has to land in that last stretch for the package
to carry what the successor lacks, and where the stretch falls varies from
35 to 109 seconds: no fixed stop lands in more than two of the four, and
none in W12's and W11's both. Before the duplicates were recorded the
package holds the offset diagnosis, which the diff shows.

This is the same narrowness task E v1 had, now one step later. As long as
findings are written just before they are acted on, the window in which a
package holds something the disk does not is the few seconds between
writing and editing. A package can only cover the investigation itself if
findings are recorded when they are established: when a run confirms a
cause or rules one out.

### W13–W16: findings recorded when established

The skill was revised again: record a finding when a run or a reading
settles it, not when acting on it. Four sessions:

| | first checkpoint | progress | narrowing recorded | duplicates recorded | acceptance | exit |
|---|---:|---:|---:|---:|---:|---:|
| W13 | 34.7 s | 39.1 s | 52.7 s | after acceptance | 112.0 s | 131.2 s |
| W14 | 27.6 s | 35.2 s | 42.6 s | after acceptance | 99.2 s | 137.6 s |
| W15 | 23.2 s | 36.6 s | 32.2 s | 48.2 s | 52.8 s | 66.7 s |
| W16 | 31.5 s | 37.8 s | 48.5 s | 82.5 s | 90.3 s | 119.8 s |

All passed acceptance uncontaminated; completion times are within the range
of the earlier twelve, so the extra checkpoints cost nothing visible.

**What changed is a checkpoint right after the offset fix.** Every session
now writes one within 4 to 15 seconds of progress, and it records the
narrowing: the offset fix verified, the 16 mismatches down to two, which two
days and by how much, timezone placement ruled out for them, and the next
step — compare those days' transactions with finance's figures. (W15
spotted the duplicate import ids in the debug output before its first edit
and recorded them as a suspicion at 23.2 s.)

**What did not change is the decisive finding.** The re-imported receipts
were written down 4.6 and 7.8 seconds before acceptance in W15 and W16, and
in W13 and W14 only after the fix that removed them, despite the rule. Once
a session sees the duplicates, the fix is a few lines and it makes it at
once; the finding and the edit are one step to it.

So inside the window the package now holds the narrowing and not the
answer. That narrowing is real but cheap: a successor that runs
`check_report.py` after the offset fix sees the two remaining days in a
second. On this fixture a package could save a recovery only the time it
takes to run the checker and decide timezone placement is no longer the
problem — seconds, against recovery times that varied by tens of seconds
in task D's pairs.

For the package to matter, re-establishing what it records has to be
expensive. Here every check runs in well under a second, so whatever an
investigation narrows down, a successor can re-narrow by running it again.

### Task E v3: every run costs ten seconds

`benchmarks/fixtures/task-e-v3` is task E v2's report, causes and evaluators
with a ledger large enough that reading it takes time: nine months of five
tills, 1.8 million rows, 99 MB. Store B is now wrong on every one of 273
days; fixing the offset leaves the same two, 09-17 and 09-23.

| | v2 | v3 |
|---|---:|---:|
| `check_report.py` | 0.1 s | 10–13 s |
| `debug_report.py --store B --day 2026-09-17` | 0.1 s, 35 rows | 11 s, 2,736 rows |

Anything that reads the ledger pays the same, a script of the agent's own
included, and a day's placement is too long to read: the re-imported
receipts can only be found by analysis. What an investigation establishes —
which two days remain, that timezone placement is ruled out for them, which
receipts repeat — now costs a successor several ten-second runs to establish
again.

The ledger is too large for the repository. `prepare.py` writes it into each
project copy from a fixed seed (the harness runs it whenever it copies a
fixture's project), caching it in the fixture's gitignored `.generated/`;
generation takes about 8 seconds once. It computes finance's figures from
what it knows — each transaction's true business day and which rows are
re-imports — independently of the report code. The fixture digest includes
`prepare.py`, since it fixes what is generated.

The evaluators are probed every second and cannot read a 99 MB ledger, so
they judge the report code on task E v2's ledger and hidden rows, embedded
in them (`build_evaluators.py`); a fix that passes v3's public check passes
them for the same reasons. The public check runs only in calibration, which
now allows it 180 seconds and takes about 50 in all.

| State | progress | acceptance | `check_report.py` |
|---|:-:|:-:|:-:|
| initial | fail | fail | fail (273 days) |
| partial (offset fixed) | pass | fail | fail (2 days) |
| reference | pass | pass | pass |

### Task E v3's window: two minutes wide, and filled with a false claim

Four uninterrupted sessions (`benchmarks/results/window-task-e-v3-2026-10-17/`),
with the skill that asks for findings to be recorded when established:

| | first checkpoint | progress | next checkpoint | duplicates recorded | acceptance | exit |
|---|---:|---:|---:|---:|---:|---:|
| W01 | 49.4 s | 56.2 s | 74.4 s | 154.4 s | 171.0 s | 235.0 s |
| W02 | 62.3 s | 67.7 s | 122.3 s | 165.3 s | 174.3 s | 208.0 s |
| W03 | 39.1 s | 41.8 s | 55.1 s | 179.1 s | 183.5 s | 215.0 s |
| W04 | 58.4 s | 63.3 s | — | 187.4 s | 197.7 s | 224.7 s |

All four passed acceptance uncontaminated, inside the 300-second budget.

**The window is wide.** From progress to acceptance the sessions spent 106
to 142 seconds on the two remaining days, against 13 to 64 on v2: with every
run costing ten seconds, the investigation takes several. Any stop between
about 70 and 150 seconds would land in it in all four.

**What the package holds there is mostly wrong or already on disk:**

- **W01 and W03 recorded a check result they had not seen.** Their
  checkpoints after the offset fix say `check_report.py` "now reports every
  expected store-day matches finance" (W03: "all 824 store-days match
  finance (exit 0)") and that the next step is to hand off the verified
  fix. The checker at that point reported two mismatches, and both sessions
  went on investigating for another 100 seconds or more. A recovery handed
  either package would be told the work was done and verified.
- **W04 wrote nothing between its first checkpoint, before the offset fix,
  and the duplicates.** For 129 seconds its package held the offset
  diagnosis, which the diff shows.
- **Only W02 recorded the narrowing,** at 122.3 seconds: two days left, by
  how much, and that the offset bug cannot explain them — now worth two or
  more ten-second runs to a successor.
- **The duplicates were again recorded just before the fix:** 4.4 to 16.6
  seconds before the edit that removed them.

The false claims are the more serious finding. A checkpoint that states a
result is only as good as the order it was written in: these read as if
written with the edit, before the ten-second check returned, or chained to
it so that the claim was recorded whatever the check printed (a v2 session,
W14, noted that "the prior combined command accidentally recorded the
checker as passed even though its output reported two mismatches"). On a
fast fixture the check returns before anyone could notice; on a slow one
the claim and the result come apart. The skill forbids claiming a test
passed without having run it, but not recording a result before reading it.

### W05–W08: results recorded from their output

The skill now asks for a result to be recorded only after its output has
been read, as the output states it, and never chained to the command that
produces it. Four sessions:

| | progress | narrowing recorded | duplicates recorded | acceptance | exit |
|---|---:|---:|---:|---:|---:|
| W05 | 63.8 s | with the duplicates | 155.1 s | 162.6 s | 229.8 s |
| W06 | 42.0 s | 65.3 s | 146.3 s | 153.3 s | 184.8 s |
| W07 | 43.4 s | 64.3 s | 127.3 s | 131.6 s | 153.7 s |
| W08 | 50.0 s | 73.5 s | 146.5 s | 155.6 s | 222.2 s |

All four passed acceptance uncontaminated. (W07's first attempt stopped
before launching anything, on a calibration failure that did not recur in
two reruns and whose cause the error did not say; a calibration failure now
reports each variant's results and the end of its public check's output.)

**No checkpoint claimed a result it had not seen.** Every one written inside
the window quotes the checker: "Ran python check_report.py (exit 1): exactly
2 mismatches remain: B 2026-09-17 report 356030.37 vs finance 355505.39; B
2026-09-23 …" (W06). The claims of success appear only in the checkpoints
written after acceptance.

**Three of four recorded the narrowing within 22 to 24 seconds of
progress** — the two days, the amounts, the offset ruled out for them, and
the next step — and held it for 54 to 82 seconds of investigation before
the duplicates were found. W05 wrote nothing between its first checkpoint
and the duplicates.

**The duplicates were recorded 4 to 9 seconds before acceptance,** as
before: the decisive finding and its fix stay one step.

So on task E v3 a stop at **100 seconds** lands, in all eight sessions
measured, after the offset fix and before the duplicates are recorded; in
three of the four with the current skill the package then holds the
narrowing, read from a ten-second check, and in the fourth only what the
diff shows. That is the most this fixture and skill offer a package: a
successor that receives it can start on the two days at once, where one
without it has to run the checker, wait, and decide what is left.
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
python -m benchmarks.recovery_pilot --register-plan .trials/task-d-v05-r09.json --replicate r09 --seed <seed> --model "<pinned model slug>" --cli-version "<exact codex --version output>"
python -m benchmarks.recovery_pilot --plan .trials/task-d-v05-r09.json --status
```

Starting the next arm requires a separate, explicit `--launch`; do not include
it in preparation or review commands.
