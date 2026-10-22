# Recovery Pilot: Findings

This summarizes what the recovery pilot found, from its first pair on task D
to its last on task E v3. The full record — every pair, every window
measurement, and every change to the harness and the skill along the way — is
in [the pilot log](recovery-benchmark-v04-pilot.md); the protocol it measures
against is [the benchmark design](benchmark-design.md).

## The question

> When a coding task is interrupted after meaningful but incomplete work, does
> having the agent-handoff skill available from the start make it cheaper to
> finish the task in a fresh session?

Each pair runs one task twice with the same agent (Codex CLI 0.160.0,
`gpt-5.6-terra`, medium effort). In the **Handoff** arm the skill is available
and the harness packs whatever the session recorded at the moment it is
stopped; in the **Baseline** arm it is not. Both sessions are stopped at the
same pre-registered time, and a fresh session in each arm is asked to finish
the task from the state on disk — plus, in the Handoff arm, the package. The
measure is time to full acceptance.

## The answer, for this agent on these tasks

**No.** On the task built to give a package the best chance, the Handoff
recovery was slower in four of five pairs. A package helps only with what it
carries that a successor would otherwise have to work out again, and on every
task tried that was either nothing or something the successor's own first
check re-derived in passing; meanwhile taking the package over cost four to
seven extra tool calls.

That is a result about short, single-agent tasks with an agent that verifies
before it acts. It does not show that a package is worthless where a session's
context is large, or long-lived, or hard to reconstruct — but none of the tasks
here was, and building one that is turned out to be the hard part.

## What was run

| Task | What made it | Window measurements | Pairs |
|---|---|---:|---:|
| D v0.5 | Unify a CSV and a JSON exporter; four short files | 4 | 8 (D-r01–D-r08) |
| E | Store B's daily totals wrong: an offset bug and a refund bug, plain from one debug run | 4 | — |
| E v2 | The offset bug, plus re-imported till receipts found only in a four-week ledger | 16 | — |
| E v3 | E v2 with a 1.8-million-row ledger: every run of the report takes 10–13 s | 8 | 5 (E-r01–E-r05) |

A *window measurement* runs one Handoff session to its end, uninterrupted, and
times when it fixes what, when it checkpoints, and what each checkpoint says.
It is how the stop time for each task was chosen, and how each task was found
wanting before any pairs were spent on it.

Recovery time to full acceptance, Handoff minus Baseline, in the pairs where
both arms recovered:

| Pair | Stop | Package at the stop | Handoff − Baseline |
|---|---:|---|---:|
| D-r04 | 30 s | not recorded² | +40.1 s |
| D-r05 | 30 s | task restated | −27.1 s |
| D-r06 | 30 s | nothing | −18.7 s |
| D-r08 | 45 s | substantive plan | +8.0 s |
| E-r01 | 100 s | pre-edit plan | +118.5 s |
| E-r02 | 100 s | pre-edit plan | +46.5 s¹ |
| E-r03 | 100 s | narrowing | −8.9 s |
| E-r04 | 120 s | narrowing | +23.7 s |
| E-r05 | 120 s | pre-edit plan | +38.9 s |

¹ E-baseline-r02 is recorded as failing its final acceptance check, although
its recovery had passed acceptance in the session, confirmed it with the public
check and exited, and its saved patch passes when re-evaluated; its time is
taken from the session. The harness now repeats a failing final check and
keeps the evaluator's output.

² Records kept only a hash of the package until D-r04 showed that this left no
way to tell what recovery had been handed; they have kept its text since.

All recoveries ran within budget, in scope and uncontaminated.

## Findings

### 1. A package can only help in a window, and on most tasks there is none

A package carries something a successor lacks only between the moment a
session records something substantive and the moment that knowledge reaches
the disk as an edit — after which the diff carries it. On task D that window
was the time to read four short files, which a successor has to re-read to edit
them anyway. On task E both causes were plain from one run of the debug tool:
the first checkpoint came 1.2 to 4.8 seconds before the first edit. Task E v2's
investigation of the second cause took up to a minute, but every check ran in
under a second, so a successor could re-derive whatever had been narrowed down
by running it again.

**A task has a usable window only if establishing what the session learned is
expensive and what it learned is not visible on disk.** Task E v3 has one —
88 to 142 seconds of investigation between fixing the offset and acceptance,
across its eight window measurements — because every experiment costs ten
seconds.

### 2. Agents write down the decisive finding at the moment they act on it

The skill was revised four times to move checkpoints earlier:

1. a substantive checkpoint before the first edit, not a restatement of the
   task;
2. a checkpoint before every edit that follows new findings;
3. a finding recorded when it is established, not when it is acted on;
4. a result recorded only after its output has been read, as the output
   states it.

The first three moved the *intermediate* checkpoints: after (3), sessions
record the narrowing — two days left, by how much, the offset ruled out for
them — within seconds of the check that shows it. None moved the *decisive*
one. In the 16 sessions on tasks E v2 and v3 in which it could be timed, the
re-imported receipts were recorded 4 to 17 seconds before the fix, or only
after it — in all but one, E v2's W10, which found both causes before its
first edit and recorded them 75 seconds before making either change.
Once a session sees the cause, the fix is a few lines and it makes it at once;
to the agent, the finding and the fix are one step. A package can therefore
hold the narrowing in a usable window, and almost never the answer.

### 3. On slow checks, sessions recorded results they had not seen

With the ten-second checker of task E v3, two of four sessions wrote
checkpoints saying `check_report.py` passed and the fix was verified while it
still reported two mismatches; both then investigated for another 100 seconds.
A successor handed either package would have been told the work was done. On
fast fixtures the check returns before the checkpoint is written and the
problem is invisible; on a slow one the claim and the result come apart.

Revision (4) fixed it: in the four sessions after it, every checkpoint written
before acceptance that stated a result quoted the checker's output, and no
claim of success appeared before acceptance. **This is the pilot's clearest product finding**, and it is
independent of speed: a package that can state an unverified pass is worse
than none.

### 4. Taking over a package costs more than the narrowing saves

On task E v3, from the Codex session logs of the ten recoveries:

| | E-r01 | E-r02 | E-r03 | E-r04 | E-r05 |
|---|---:|---:|---:|---:|---:|
| Handoff: calls that read the ledger | 14 | 7 | 6 | 9 | 10 |
| Baseline: calls that read the ledger | 8 | 8 | 8 | 7 | 7 |
| Handoff: `handoff` calls (wall time) | 7 (31.0 s) | 5 (7.8 s) | 5 (7.4 s) | 5 (9.3 s) | 4 (6.4 s) |
| Handoff: all tool calls | 21 | 12 | 11 | 14 | 15 |
| Baseline: all tool calls | 9 | 8 | 8 | 9 | 9 |

A recovery handed the narrowing still ran the checker before going on, as the
resume instructions tell it to — "verify the current repository state before
making further changes" — and so paid the ten seconds the narrowing was meant
to save. It then spent four to seven calls on the package itself: status,
verification, reading it, and keeping the checkpoint current. Each costs the
command's time and a model turn.

Verifying first is the right behavior: the package is written by another
session and the pilot itself found it can be wrong (finding 3). But it means
the narrowing's value is spent by the first verification, and what remains is
the cost of the package.

### 5. Three in five packages at the stop held only the plan

E-r01 and E-r02 fixed the offset late and were stopped at 100 seconds before
their next checkpoint; moving the stop to 120 did not end it. A Handoff source
could still reach the stop with nothing recorded since its pre-edit plan: E-r05's fixed the offset at 50.7
seconds and ran 70 more without a checkpoint. The skill's rules are followed
most of the time, not always, and a package is only as current as the last
time they were.

### 6. The instrument works, after fixes that the pilot itself forced

Isolation (skills and memories off, preflight-checked prompt input),
contamination scanning, a fixed-time stop that ends the whole process tree,
packing at the stop exactly as the product does, and records that keep what
recovery received and left all held in practice. Running the pilot found and
fixed:

- a window watcher that stopped looking at the checkpoint once progress
  passed, so every checkpoint written in the window — the stretch the
  measurement exists for — was recorded as written at exit (task E v2
  W01–W08 cannot say when they checkpointed);
- edits that went unrecorded when they fell in the same probe round as the
  progress they caused;
- a single failing final check that marked a passing recovery as failed
  (E-baseline-r02), now repeated and its output kept;
- a calibration failure that said only that it had failed.

## Limits

- **One agent, one model, one effort level.** Nothing here says how another
  agent uses a package.
- **Few pairs.** Five on task E v3, under two stop times. The direction is
  consistent with finding 4, which is the stronger evidence because it shows
  the mechanism; the times alone could not carry a conclusion.
- **Arm order is confounded.** All five task E v3 pairs drew Handoff first:
  the seeds were fixed in sequence before their orders were known, and
  20261021–20261026 happen all to shuffle that way.
- **Short tasks.** The longest uninterrupted session ran under four minutes.
  The case a handoff tool is for — a session whose context is large and was
  built over a long time — was not tested, because no fixture here produced
  one.

## What would have to change for a package to pay

1. **It has to carry what verification does not re-derive.** A successor that
   verifies before acting — as it should — recovers "two days are left" in its
   first check. What it does not recover in passing is the reasoning behind a
   long investigation: causes tried and ruled out by experiments that are
   expensive to repeat, design decisions and why the alternatives were
   rejected. That needs tasks long enough for such reasoning to accumulate.
2. **Taking it over has to be cheap.** Four to seven calls is a fixed cost
   paid on every recovery. One command that reports status, verifies the
   package and prints it would cut most of them; so would resume instructions
   that ask for one verification rather than several.
3. **What it says has to be trustworthy enough to act on.** Revision (4) makes
   recorded results quote their output. A successor could then skip re-running
   a check whose recorded output still matches the current diff — which
   requires the package to bind each result to the state it was taken in.

Until a task exists that rewards the first and the product does the second,
more pairs on these fixtures would spend quota without being able to change
the answer.

## Since the pilot

The first two changes have been made. `handoff tests` records the code a
result ran on, and `HANDOFF.md`, `handoff status` and `handoff verify` say
whether it is current, stale or unknown; `handoff resume` verifies and prints
the package against the live repository in one read-only step. One pair,
E-r06, checked them: the takeover took one call instead of four to seven,
and a pre-fix result was correctly marked stale and re-run. But the source
had recorded nothing after its fix, so no current result arose to skip, and
the recovery spent five calls keeping the package current — two test
records, two checkpoints, a final pack — that paid nothing because it was
not interrupted. The skill now asks for the first check after a fix to be
recorded at once, for a result and its checkpoint to go in one command, and
for no pack unless work is being handed over. Details are in
[the pilot log](recovery-benchmark-v04-pilot.md#e-r06-handoff-resume-and-test-result-freshness).

## What the pilot leaves behind

- **The skill** now asks for a substantive checkpoint before the first edit, a
  checkpoint whenever a run or reading settles something, and results recorded
  only from their output. The last is a correctness fix worth keeping whatever
  the speed.
- **The harness** (`benchmarks/recovery_pilot.py`) supports any fixture under
  `benchmarks/fixtures/` with a declared public check, scope and generated
  data, pre-registered plans with frozen stop rules (schemas 1–6 still read
  back as what they ran under), window measurements, and the fixes above.
- **Four fixtures** — task D v0.5 and task E, E v2 and E v3 — each calibrated
  so that the initial state fails, the partial state passes progress only, and
  the reference passes everything, with evaluators that reject the wrong fixes
  tried against them.
