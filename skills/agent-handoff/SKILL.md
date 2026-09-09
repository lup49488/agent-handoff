---
name: agent-handoff
description: Automatically preserve substantive coding work in a local Git repository with the handoff CLI. Use for implementation, debugging, testing, or review work that may outlast one agent; do not use for brief answers, translations, or read-only questions with no repository work.
---

# Agent Handoff

Use the installed `handoff` CLI as the source of durable task state. It records repository evidence, compact reasoning checkpoints, and test results in a project-local, gitignored `.agent-handoff/` directory, then renders `HANDOFF.md` for the next agent.

## Start tracking early

- Confirm the CLI is available with `handoff --version`. If it is not on `PATH`, invoke it from a source checkout with `python -m agent_handoff` after setting `PYTHONPATH` to that checkout's `src` directory. On PowerShell, use `$env:PYTHONPATH = "<checkout>\src"`. If neither works, say so; do not invent a replacement state format.
- For substantive implementation, debugging, testing, or code-review work in a Git repository, start tracking before the first material change. If no package is active, run `handoff init` with a concise, faithful rendering of the user's task. Do this without asking separately unless the user says not to track the task or the work is read-only.
- If a handoff package exists, run `handoff status` and `handoff verify`, then read `HANDOFF.md` and the current Git status/diff. The live diff is authoritative when it disagrees with a checkpoint.
- Do not initialize for brief answers, translation, planning without repository work, or purely read-only questions.

## Record progress

At a material milestone—not after every command—automatically write a concise checkpoint. Record only what a successor cannot recover from Git or command output: the objective, what is settled, why it was decided that way, and the next step. Git already shows what changed.

```text
handoff checkpoint --objective "..." --done "..." --decision "..." --next "..."
```

`--done` and `--decision` are repeatable. Later runs patch the existing checkpoint; use `--show` to read it and `--replace` only when a fresh checkpoint is warranted.

- Add `--problem` only when something is genuinely blocking progress.
- After a meaningful test run, automatically record its actual command and exit code:

```text
handoff tests "python -m pytest -q" --exit-code 0 --summary "291 passed"
```

- Never claim a test passed or a handoff was verified without having run the corresponding command in the current task.

## Hand work over safely

- For a reviewable package without starting another agent, use `handoff pack --to <agent>` after a current checkpoint and test record. It updates `HANDOFF.md`.
- `handoff switch` and `handoff run` can start another agent and spend the user's quota. Run either only with the user's explicit authorization, and use `--dry-run` or `--no-launch` when they ask only to prepare a handoff.
- If `handoff verify` reports damage or drift, report it before handing over. Use `handoff recover --dry-run` to inspect recovery actions; `--break-lock` needs explicit user authorization because it can disrupt a live process.

## Take over work

Start from the evidence package, then continue from the most advanced valid repository state. Preserve unrelated worktree changes. Update the checkpoint when a new decision, meaningful test result, blocker, or next step emerges.
