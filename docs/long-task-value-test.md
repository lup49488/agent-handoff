# Long-Task Value Test: Plan

The recovery pilot answered "no" for short tasks: a successor re-derived in
its first check whatever the package said, and taking the package over cost
more than that saved ([findings](recovery-pilot-findings.md)). Its limits
named the case it could not test: a session whose context is large and was
built over a long time. This plan is for that test. Nothing in it has been
run; the phases that spend quota each need a go-ahead.

## The question

> When a long coding task is interrupted after its session has learned things
> that are expensive to learn again and that the repository does not show,
> does the package make the task cheaper to finish?

"Cheaper" is time to full acceptance, and tokens, for the session that
finishes the task. The package must pay for what it costs the session that
writes it, so that cost is measured too (see *Full accounting*).

## What the task has to reward

The pilot found what a package can carry that a successor's own first check
does not recover:

1. **Causes ruled out by experiments that are slow to repeat.** "The
   timezone conversion is not it: switching it changed nothing" saves a
   successor the runs that established it, if it trusts the statement.
2. **Attempts that were tried and reverted.** A reverted change leaves
   nothing in the diff. If the obvious fix for one fault breaks something
   fixed earlier, and only the slow check shows it, a successor without the
   package is likely to try that fix again.
3. **Where a long sequence stands.** Which of several faults are fixed and
   verified, which remain, in what order they surfaced.

The third is mostly on disk, as the diff and the recorded test results. The
first two are not, and they are what the task is built around. A task where
neither occurs repeats the pilot.

## The fixture: task F

A new fixture under `benchmarks/fixtures/task-f/`, in the harness's existing
format (prompt, project, partial, reference, public check, scope, generated
data, acceptance), with these properties:

- **A slow check.** The public check, run on generated data, takes 20–30
  seconds, so every experiment costs something. Task E v3 showed that ten
  seconds was enough to open a window; longer makes each avoided run count.
- **Faults that surface in sequence.** Four faults, each hidden until the one
  before it is fixed (the check reports the first class of mismatch it
  finds). This makes the session long without padding, and gives the stop
  rule a stage on disk to key on.
- **A plausible decoy for each of at least two faults.** The symptom points
  first at a cause that one or two slow runs rule out. The skill already asks
  for "a cause confirmed or ruled out" to be checkpointed with its evidence.
- **One fix that has to be reverted.** The direct fix for the third fault
  changes a shared helper and breaks the first fault's fix. Only the full
  check shows it. The working fix is local. A session that tried the direct
  fix will have reverted it before the stop.
- **A target length of 20–40 minutes** uninterrupted for Codex at medium
  effort. This cannot be known before running it, which is what phase 1 is
  for.

Calibration, as for the earlier fixtures, must show that the initial state
fails, each partial stage passes exactly its stages, the reference passes
everything, each decoy fix and the direct (wrong) fix for the third fault are
rejected, and no fix outside the scope can pass.

## Design: one snapshot, two recoveries

The pilot ran a separate source session in each arm and stopped both at the
same time. This plan changes that. One Handoff source runs, and at the
pre-registered stop point the harness copies the repository, packs the
package as the product would at a stop, and lets the source run on to the
end. From each snapshot two recoveries start:

| Recovery | Repository | Package | Skill |
|---|---|---|---|
| Handoff | the snapshot | the package from the stop | on |
| Baseline | the snapshot, with `.agent-handoff/` and `HANDOFF.md` removed | none | off |

Why:

- **It compares what the question asks:** the same disk, with and without the
  package. Separate sources differ in what they got done by the stop, and the
  pilot's pairs were noisy for that reason.
- **It halves the cost.** Each source is a long session; one source serves
  both arms, and also counts as a window measurement, because it is not
  stopped.
- **A copy is a fair stand-in for a stop.** What a successor receives after a
  stop is what was on disk and in the package at that moment. The one
  difference is a write in flight, which the snapshot can catch half-done
  just as a kill can.

What it gives up: a Baseline *source* would not have spent time on
checkpoints, so its disk at the same moment might be a little further on.
That cost is measured separately (*Full accounting*) rather than folded into
the pair.

Arm order and recovery seeds are drawn before each snapshot and recorded.
The pilot's five E v3 pairs all drew Handoff first; this design runs the two
recoveries of a snapshot in random order, from identical copies.

## Measures

**Primary:** time to full acceptance of each recovery, paired by snapshot.

**Mechanism**, from the session logs. The pilot found this was stronger
evidence than the times:

- slow-check runs by each recovery;
- decoy experiments repeated by a recovery after the package had recorded
  them as ruled out (Baseline: experiments on a cause the source had already
  ruled out);
- whether the Baseline recovery retried the reverted fix;
- the Handoff recovery's calls spent on the package (`handoff` commands and
  reading it).

**Tokens:** input and output tokens per recovery, from the Codex session log
(Claude Code: from `stream-json`).

**Guardrails**, as in the pilot: in-scope changes only, no contamination
(the Baseline recovery never touches `handoff`), and the final acceptance
check repeated on failure with its output kept.

**Package quality at the stop:** whether the package holds the ruled-out
decoys and the reverted attempt, judged against the source's log, not
against what the recovery did.

## Full accounting

The value of the package to a team is the recovery saving minus what writing
it cost the source. From the source sessions (all of which run to the end):

- wall time and tokens spent in `handoff` calls and the turns that wrote
  checkpoints;
- compared against two uninterrupted sessions with the skill off, for total
  time to acceptance.

The result reports the saving per recovery, the cost per source, and the
break-even interruption rate: how often a session would have to be stopped
before the package pays overall.

## Phases and gates

**Phase 0: build and calibrate the fixture (no quota).**
Write task F and its evaluators. Calibrate with scripted solutions (each
stage, each decoy fix, the reverted fix, the reference). Extend
`benchmarks/recovery_pilot.py` with:

- a snapshot-at-stop mode that copies the repository and packs without
  stopping the source;
- recoveries from a shared snapshot, with the Baseline copy stripped of the
  package;
- log counters for slow-check runs and decoy experiments;
- budgets and stall timeouts sized for sessions of 40 minutes.

Unit tests cover the new harness code offline. Gate: calibration passes.

**Phase 1: is there a window? (quota: about 2 source sessions)**
Run two uninterrupted Codex Handoff sources. Gate, all of:

- each runs 15 minutes or more;
- in at least one, a decoy is explored and ruled out, or the direct fix is
  tried and reverted;
- the package records it before the next stage's fix, with at least 60
  seconds of slow-check work between the record and that fix.

If the gate fails, revise the fixture once and repeat. If it fails twice,
stop and write it up: two honest attempts at a task that rewards a package
would themselves be a finding about how agents work.

**Phase 2: freeze the stop rule (quota: about 2 more sources).**
Two more uninterrupted sources. From the four, freeze the stop rule before
any recovery runs: a stage on disk plus a delay (for example "the second
stage first passes, plus *D* seconds"), chosen so that the stop falls after
the ruled-out work is recorded and before the next fix in most sessions.
Freeze, in the same commit, the decision rule below.

**Phase 3: paired recoveries (quota: 5 sources, 10 recoveries).**
Five Codex sources with snapshots under the frozen rule; two Codex
recoveries from each snapshot.

Pre-registered decision rule: **the package pays on this task** if both

- the Handoff recovery is faster in at least 4 of 5 snapshots, with a median
  saving of at least 20%; and
- in at least 3 of 5, the Baseline recovery repeats a slow experiment or the
  reverted fix that the Handoff recovery skips.

Otherwise it does not, and the result is reported as the strongest case the
project could build.

**Phase 4, optional: the other agent (quota: 10 Claude Code recoveries,
2 no-skill sources).**
The product's own scenario is a hand-off between agents. Run Claude Code
recoveries, Handoff and Baseline, from the same five snapshots, and the two
no-skill sources for the full accounting (these can run in phase 3).

## Cost

All figures are estimates until phase 1 measures the real length.

| Phase | Sessions | Agent time |
|---|---|---|
| 1 | 2 sources | about 1 hour |
| 2 | 2 sources | about 1 hour |
| 3 | 5 sources, 10 recoveries | about 5 hours |
| 4 | 10 recoveries, 2 sources | about 4 hours |

A Codex usage window resets after five hours, so phase 3 will span more than
one window, and probably more than one day. Each phase stops at its gate
before the next is asked for.

## Isolation

The pilot's isolation carries over: neutral trial directories outside the
repository, Codex with skills and memories off for the Baseline arm,
preflight-checked prompt input, and a log scan that invalidates a Baseline
recovery that touches `handoff`.

Claude Code needs the same before phase 4. The skill is installed globally
at `~/.claude/skills/agent-handoff/`, and a Baseline recovery must not see
it. A mechanism has not been chosen. The candidates are a separate config
directory and disabling the skill for that run, both untested, and whichever
is chosen has to be checked with a preflight run that lists the skills the
session sees.

## Rules for the duration

- **The skill and the product do not change** between phase 2 and the end of
  phase 3. A change found necessary is recorded and made afterwards. The
  pilot revised the skill four times mid-run, which left its pairs measuring
  different things.
- **Every plan is registered before it runs**, with its seeds, its stop rule
  and its decision rule, as the pilot's were.
- **Results go in a log beside the pilot's**, and a findings document at the
  end, whatever the answer.

## Not covered

- Human interaction: clarifications given to the source in conversation,
  which no repository holds, are a strong case for a package but cannot be
  run unattended.
- Multi-day work: sessions resumed after hours, by a person who has also
  forgotten. That needs a different design.
- More than one fixture. A positive result on task F shows a package can pay
  where a task is built to reward it; how often real work looks like task F
  is a separate question.
