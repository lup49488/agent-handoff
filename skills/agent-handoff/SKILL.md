---
name: agent-handoff
description: Automatically preserve substantive coding work in a local Git repository with the handoff CLI. Use for implementation, debugging, testing, or review work that may outlast one agent; do not use for brief answers, translations, or read-only questions with no repository work.
---

# Agent Handoff

Use the installed `handoff` CLI as the source of durable task state. It records repository evidence, compact reasoning checkpoints, and test results in a project-local, gitignored `.agent-handoff/` directory, then renders `HANDOFF.md` for the next agent.

## Start tracking early

- Confirm the CLI is available with `handoff --version`. If it is not on `PATH`, invoke it from a source checkout with `python -m agent_handoff` after setting `PYTHONPATH` to that checkout's `src` directory. On PowerShell, use `$env:PYTHONPATH = "<checkout>\src"`. If neither works, say so; do not invent a replacement state format.
- For substantive implementation, debugging, testing, or code-review work in a Git repository, start tracking before the first material change. If no package is active, run `handoff init` with a concise, faithful rendering of the user's task. Do this without asking separately unless the user says not to track the task or the work is read-only.
- If a handoff package exists, run `handoff resume`. It checks the package for damage and prints it against the code as it is now—checkpoint, repository state, live diff, and whether the latest test result still holds—so you need not run `handoff status`, `handoff verify`, or read `HANDOFF.md` and the diff separately. The live diff is authoritative when it disagrees with a checkpoint.
- Do not initialize for brief answers, translation, planning without repository work, or purely read-only questions.

## Record progress

At a material milestone—not after every command—automatically write a concise checkpoint. Record only what a successor cannot recover from Git or command output: the objective, what is settled, why it was decided that way, and the next step. Git already shows what changed.

Record a finding when you establish it, not when you act on it. The first checkpoint comes before any edit: as soon as you have read the code the task touches and chosen an approach, checkpoint what you learned—which files and functions matter and what they do now, the approach and why, and the concrete first change you are about to make. After that, checkpoint each time a run or a reading settles something: a cause confirmed or ruled out, a check that still fails after a fix and what its remaining failures have in common, the evidence that located a fault. Write it then, with the evidence and what it rules out, even if the change it calls for is still some way off; an investigation can run for a minute before it reaches an edit, and until something is written down everything it found exists only in your context, so an interruption loses all of it. Afterwards the diff shows what changed but not why. A checkpoint that only restates the task or says inspection has started gives a successor nothing; skip it and write the substantive one instead.

```text
handoff checkpoint --objective "..." --done "..." --decision "..." --next "..."
```

`--done` and `--decision` are repeatable. Later runs patch the existing checkpoint; use `--show` to read it and `--replace` only when a fresh checkpoint is warranted.

- Add `--problem` only when something is genuinely blocking progress.
- After a meaningful test run, automatically record its actual command and exit code, straight after the run and before editing again—the record is tied to the code as it is when you record it. Above all, record the first check after a fix as soon as you have read it: the fix is then on disk, but whether it worked, and what still fails, is not, and a successor would otherwise re-run the check to find out.

```text
handoff tests "python -m pytest -q" --exit-code 0 --summary "291 passed"
```

- When a result also settles something worth a checkpoint, record both in one shell command once you have read the output—`handoff tests ...; handoff checkpoint --done "..."`—rather than in two separate steps.

- Never claim a test passed or a handoff was verified without having run the corresponding command in the current task.
- Record a result only after you have read its output, and state what the output said—"check_report: 2 mismatches, B 09-17 and 09-23", not what you expect it to say. Do not chain `handoff tests` or a checkpoint that states a result onto the command that produces it, and do not write the result into a checkpoint before the command has finished: a slow check that has not returned, or that failed, must not be recorded as passing. A successor trusts a recorded pass and may stop on it.

## Hand work over safely

- For a reviewable package without starting another agent, use `handoff pack --to <agent>` after a current checkpoint and test record. It updates `HANDOFF.md`. Run it only when handing work over or when the user asks for a package: `handoff tests` and `handoff checkpoint` already keep the state current, and `handoff resume` renders it on demand, so packing at the end of finished work costs a step and gives no one anything.
- `handoff switch` and `handoff run` can start another agent and spend the user's quota. Run either only with the user's explicit authorization, and use `--dry-run` or `--no-launch` when they ask only to prepare a handoff.
- If `handoff verify` reports damage or drift, report it before handing over. Use `handoff recover --dry-run` to inspect recovery actions; `--break-lock` needs explicit user authorization because it can disrupt a live process.

## Take over work

Start from the evidence package, then continue from the most advanced valid repository state. Preserve unrelated worktree changes. `HANDOFF.md` says whether the latest test result was taken on the code as it is now: a result marked current already describes it and need not be re-run to know where the work stands; re-run a stale or unknown one before relying on it. After taking over, keep recording as described under Record progress, and no more often: a checkpoint when a finding is settled and a test record per meaningful run, not one after every command.
