# v0.3 post-recovery cohort: CLI token audit

> **These trials do not compare handoff against no handoff. Do not cite them
> as evidence for or against it.**
>
> Every target ran with its developer's own Codex setup. A globally installed
> agent-handoff skill and persistent memories reached both arms: every
> Baseline trial from r02 on read the skill, initialised handoff tracking
> itself and ran the `handoff` CLI; every trial from r02 on, in both arms,
> read the skill, and most read memories that describe the Baseline/Handoff
> design. Each target's working directory was also named after its arm. The
> cells below therefore compare "skill + prepared package" against "skill, no
> package", run by agents that could know they were being measured.
>
> The r01 round has a separate, independent cause: three of its four logs
> show 12 stream disconnects and 17–18 reconnects each, with 502/503
> responses. It is excluded as a provider outage — not, as the text below
> once put it, because a Baseline trial ran over budget, which is an outcome
> and not a reason to exclude.
>
> All 24 records are in `v03-records/`; `v03-exclusions.json` gives each one's
> reason, produced by re-running the runner's contamination scan over its own
> logs. Raw logs are not published: they contain the developer's memory files.
> The runner now launches targets in a neutral temporary directory with
> memories off and every user skill disabled, checks before each launch that
> no disabled skill reaches the model's input, and excludes any trial whose
> logs show otherwise. One trial under those conditions (A/60 Baseline) ran
> clean: no skill, memory or `handoff` reference anywhere in its logs.
>
> The measurements below are accurate for the condition that was actually
> run, and are kept as the record of it.

This audit supplements, but does not modify, the six immutable schema-v3 trial
records in `.trials/v03-post-recovery.json`. Counts below were extracted from
each trial's `target.stderr.log`, where Codex CLI printed `tokens used` followed
by a count. They are CLI-reported totals, not a verified billing measure or an
input/output/cached-token breakdown. The structured `provider_tokens` fields
remain null because the runner does not yet parse this CLI telemetry.

| Task/snapshot | Arm | Trial | CLI-reported tokens | Completion (s) | Process exit (s) |
|---|---|---|---:|---:|---:|
| A/60 | Baseline | `A-60-baseline-r02` | 32,277 | 40.141 | 47.688 |
| A/60 | Handoff | `A-60-handoff-r02` | 20,922 | 25.078 | 46.875 |
| B/60 | Baseline | `B-60-baseline-r02` | 21,606 | 20.094 | 32.704 |
| B/60 | Handoff | `B-60-handoff-r02` | 21,998 | 75.250 | 127.203 |
| C/60 | Baseline | `C-60-baseline-r02` | 23,592 | 40.125 | 63.344 |
| C/60 | Handoff | `C-60-handoff-r02` | 24,135 | 30.125 | 60.687 |

## Interpretation

- B/60 Handoff reported 392 more tokens than Baseline (+1.8%), while its
  acceptance time was 55.156 seconds longer. This count alone does not explain
  the elapsed-time gap; provider latency, tool work, and post-acceptance CLI
  work were not separately measured.
- Both B/60 stderr logs contain the same Codex MCP shutdown warning: refresh
  authorization for `cloudflare-api` was rejected with `invalid_grant`. It was
  logged near process exit, so shutdown/setup behavior is a possible timing
  confound, not a proven explanation for the difference. The integration was
  left unchanged to keep the follow-up configuration consistent.
- After acceptance, B/60 Handoff ran for another 51.953 seconds before process
  exit; Baseline's post-acceptance interval was 12.610 seconds.
- Each task/arm cell has only one valid post-recovery observation here. Treat
  these as exploratory measurements, not a stable comparative estimate.
- B/60 `r01` is excluded from this post-recovery comparison: its Baseline
  exceeded the budget during the earlier service-incident period.

## B/60 follow-up: r03–r05

The pre-registered plan is `.trials/v03-b60-followup.json` (seed `20261008`).
All six trials were accepted, completed, validated, within the 600-second cap,
and had no scope violations.

| Replicate | Arm | CLI-reported tokens | Completion (s) | Process exit (s) |
|---|---|---:|---:|---:|
| r03 | Handoff | 37,402 | 45.188 | 98.531 |
| r03 | Baseline | 29,212 | 30.140 | 36.156 |
| r04 | Handoff | 28,572 | 50.078 | 117.937 |
| r04 | Baseline | 22,040 | 45.157 | 79.969 |
| r05 | Handoff | 23,358 | 60.140 | 87.203 |
| r05 | Baseline | 31,028 | 65.156 | 86.250 |

Across these three additional pairs, the per-arm median CLI-reported token
counts are 28,572 (Handoff) and 29,212 (Baseline); median completion times are
50.078 s and 45.157 s, respectively. This tiny cohort does not establish a
stable performance difference. Notably, randomized order happened to put
Handoff first in all three pairs, so arm order is confounded with run order in
this follow-up.

## B/60 balanced-order follow-up: r06–r09

The pre-registered plan is `.trials/v03-b60-balanced.json` (seed `20261009`).
The first arm was balanced as planned: Baseline first in r06/r07, Handoff first
in r08/r09. All eight trials were accepted and completed within budget, passed
validation, and had no scope violations.

| Replicate | First arm | Arm | CLI-reported tokens | Completion (s) | Process exit (s) |
|---|---|---|---:|---:|---:|
| r06 | Baseline | Baseline | 21,743 | 30.110 | 40.250 |
| r06 | Baseline | Handoff | 29,362 | 45.093 | 93.968 |
| r07 | Baseline | Baseline | 21,888 | 75.234 | 110.156 |
| r07 | Baseline | Handoff | 38,273 | 55.203 | 91.266 |
| r08 | Handoff | Handoff | 24,964 | 20.094 | 37.172 |
| r08 | Handoff | Baseline | 23,212 | 55.125 | 87.282 |
| r09 | Handoff | Handoff | 23,002 | 30.141 | 66.094 |
| r09 | Handoff | Baseline | 21,093 | 25.078 | 33.609 |

For these four balanced pairs, median completion time was 37.617 s for Handoff
and 42.618 s for Baseline. The approximately five-second difference is at the
runner's five-second probe resolution, so it should not be treated as a
reliable speed difference. Median CLI-reported token counts were 27,163 for
Handoff and 21,816 for Baseline; these remain CLI-reported totals rather than
provider billing or input/output breakdowns. Four pairs are still exploratory
and do not support a general effectiveness claim.
