# Failover Drill

What happens when the agent doing the work runs out of quota partway
through, with real agents end to end: does `handoff run` notice, hand the
task on, and does the next agent continue from where the first stopped? The
offline tests rehearse this with scripted agents; this drill runs it with
Codex and Claude Code. It was run twice on 2026-11-13, against agent-handoff
0.10.0 and then with the change it led to, using Codex CLI 0.166.0 and
Claude Code 2.1.317.

## How it is run

The task is `benchmarks/failover_drill/project`: two small modules,
`stats.py` and `report.py`, with five bugs between them and nine unit tests,
of which seven fail and one errors. The prompt asks for the tests to pass
without changing them.

Waiting for a real quota exhaustion is not practical, so the primary is a
wrapper, `benchmarks/failover_drill/drill_agent.py`. It starts the real
agent exactly as the built-in adapter does, with the project's done check
allowed, and passes its output through; once the agent first changes
`stats.py` or `report.py` it waits `--grace` seconds (eight by default),
kills the agent's whole process tree, prints the usage-limit message that
agent itself prints, and exits 1. To the supervisor that is a real
exhaustion.

```text
cp -r benchmarks/failover_drill/project <scratch>/drill && cd <scratch>/drill
git init -q && git add . && git commit -qm "drill"
handoff init "Fix the bugs in stats.py and report.py so that python -m unittest passes. Do not change the test files."
```

and in `.agent-handoff/config.toml`:

```toml
primary = "codex-drill"
fallback = ["claude-code"]
health = false            # the drill agent is not real; keep it out of the health record
done_check = "python -m unittest -q"

[agents.codex-drill]
executable = "python"
exec = ["<checkout>/benchmarks/failover_drill/drill_agent.py", "codex", "{prompt}"]
```

For the reverse direction, `primary = "claude-drill"` and
`fallback = ["codex"]`, with
`exec = [".../drill_agent.py", "claude-code", "--grace", "0", "{prompt}"]`.

then `handoff run`. The drill spends a little of both agents' quota.

## First run: 0.10.0

| Time | |
|---:|---|
| 0 s | `handoff run` adopts `codex-drill`, the configured primary, over the default `init` had recorded |
| 34.7 s | Codex runs the tests (7 failures, 1 error), records the result and a checkpoint |
| 39 s | Codex fixes all five bugs in one patch |
| 42.6 s | Codex runs the tests again: all nine pass. It is about to record that |
| 47.7 s | The wrapper stops Codex with a usage-limit message |
| 48 s | The supervisor classifies it as `quota_exhausted`, writes the package, starts Claude Code |
| 97 s | Claude Code exits; the done check passes; the task is `done` after one handoff |

What held:

- **The failure was recognised and handed on**, and Codex's uncommitted
  fixes reached Claude Code intact: the final diff is exactly Codex's.
- **Claude Code began with `handoff resume`**, as the resume instructions
  now say, and ran it through PowerShell — so allowing the handoff CLI
  through PowerShell as well as Bash was necessary.
- **Freshness was right.** Codex was stopped before recording the passing
  run, so the latest result in the package was the failing one from before
  the fix, and it was marked stale; Claude Code's checkpoint said so.
- **Claude Code could checkpoint**, which it could not before 0.10.0.
- **The done check verified the work.**

What did not: **Claude Code could not run the tests.** Its command line
allows the handoff CLI and nothing else, so it tried `python -m unittest`
three times, was refused each time, and could only check its predecessor's
work by reading it. It recorded "run the tests" as its next step. The done
check made the outcome right, but had the task been handed on again, the
next agent would have inherited a conclusion nobody had verified.

## Second run: the done check allowed and named

With a `done_check` configured, an agent whose command line lists the
commands it may run — Claude Code's `--allowedTools` — is now also allowed
that one command, through either shell, and every agent in the chain is
told it is the command that decides whether the task is done. Naming it
matters: in the first run Claude Code tried `python -m unittest` where the
check was `python -m unittest -q`, and a permission for one does not cover
the other.

The same drill again: Codex fixed everything by 38.4 seconds and was stopped
at 47.1. Claude Code then made five calls and no others — `handoff resume`,
`python -m unittest -q`, `handoff tests --help`, recording the passing
result, and a checkpoint — none refused, and finished in 31 seconds from
the handoff against 49 in the first run. The task ended `done` after one
handoff, with the successor's own verified result as the latest test
record.

## Claude Code to Codex, stopped mid-change

The first two runs left the successor nothing to continue: Codex fixes all
five bugs in one patch, so by the time it was stopped the work was done.
Claude Code edits one file at a time, so with it as the primary and the
wrapper stopping it on its first edit (`--grace 0`), the work is genuinely
half-done. Codex takes over. Run twice on 2026-11-15, the second time with
the change the first led to.

**First run.** Claude Code read the four files, fixed the median in
`stats.py` at 18.4 seconds, and was stopped at 19.0, with four of the five
bugs still in place. Codex took over, kept that fix, made the other four,
ran the done check itself, recorded the passing result and checkpointed; the
task ended `done` after one handoff, 67 seconds after the handoff.

But the package's checkpoint read "None recorded": Claude Code had edited
before checkpointing, and never ran a `handoff` command at all. It has no
agent-handoff skill installed on this machine — Codex does — so all it had
was the checkpoint protocol `handoff run` appends to the task, and that
still said to checkpoint "when you complete a meaningful milestone". The
rules the recovery pilot had arrived at — checkpoint before the first edit,
record findings when they are settled, record results from their output —
were in the skill and nowhere else. Codex recovered anyway, because one
reading of the code was enough here; on a task where the first agent's
reading is expensive, the successor would have had to repeat it.

**Second run.** The protocol now carries those rules. Claude Code read the
files and, at 28 seconds, before touching anything, checkpointed what it had
found and meant to do: all five fixes, each with its reason, and "apply the
5 fixes, run python -m unittest -q" as the next step. It edited `stats.py`
at 31.8 seconds and was stopped at 32.3. Codex took over with that plan,
completed it and verified it, and the task ended `done` 47 seconds after
the handoff.

| | First run | Second run |
|---|---|---|
| Checkpoint before Claude Code's first edit | none | diagnosis and plan for all five fixes |
| Claude Code stopped at | 19.0 s, one fix made | 32.3 s, one fix made |
| Codex kept the fix and made the rest | yes | yes |
| Handoff to acceptance | 67 s | 47 s |

## What the drills do not show

They are one small task, run once or twice in each direction; the times
are indications, not measurements. In every run one reading of the code
was enough for the successor to finish, so they show that the path works
and that the package carries what it should, not how much it saves on work
whose findings are expensive to repeat — the question the recovery pilot
studied, with the answer in its [findings](recovery-pilot-findings.md).
