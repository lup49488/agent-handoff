"""The agents that ship with the tool.

Some of these command lines have been checked against an installed CLI; the
rest are best-effort defaults read off each project's documented interface.
The `verified` field says which is which, and `handoff agents --verbose`
prints it, so nobody has to guess whether a default was ever run. Any of it
can be corrected in config without changing code:

    [agents.gemini]
    exec = ["--yolo", "-p", "{prompt}"]

Adding an agent that is not listed here works the same way -- a config table
is a complete adapter.

A note on privilege. A supervised agent has to be able to edit the repository,
or there is no work to hand off -- but both CLIs default to *not* being able
to, since nobody is there to approve anything. The `exec` forms below ask for
the least that still works: edits confined to the working directory
(`--sandbox workspace-write`, `--permission-mode acceptEdits`). Neither is the
"bypass everything" setting each CLI also offers, and neither is used for the
interactive form, where a person is present to decide.
"""

from __future__ import annotations

from agent_handoff.adapters.spec import PROMPT, AgentSpec

BUILTIN_SPECS = (
    AgentSpec(
        name="codex",
        executable="codex",
        # `codex <prompt>` opens the terminal UI and refuses piped stdio with
        # "stdin is not a terminal", which is exactly how a supervised run
        # starts it -- hence the separate non-interactive subcommand.
        # `codex exec` defaults to --sandbox read-only, where the agent can
        # read and reason but never change a file -- a supervised run would
        # produce nothing to hand on.
        exec_args=("exec", "--sandbox", "workspace-write", PROMPT),
        interactive_args=(PROMPT,),
        aliases=("codex-cli",),
        # `codex --help`: "codex [OPTIONS] [PROMPT]" for the interactive UI,
        # and "exec  Run Codex non-interactively".
        verified="codex-cli 0.153.4, 2026-09-08",
    ),
    AgentSpec(
        name="claude-code",
        executable="claude",
        # Print mode cannot ask permission, so without this the agent
        # reports what it would have done instead of doing it.
        exec_args=("-p", "--permission-mode", "acceptEdits", PROMPT),
        interactive_args=(PROMPT,),
        aliases=("claude",),
        # `claude --help`: "starts an interactive session by default, use
        # -p/--print for non-interactive output".
        verified="Claude Code 2.1.263, 2026-09-08",
    ),
    AgentSpec(
        name="gemini",
        executable="gemini",
        exec_args=("-p", PROMPT),
        interactive_args=("-i", PROMPT),
        aliases=("gemini-cli",),
        note="not installed here, so this command line is unverified",
    ),
    AgentSpec(
        name="opencode",
        executable="opencode",
        exec_args=("run", PROMPT),
        interactive_args=(PROMPT,),
        note="not installed here, so this command line is unverified",
    ),
    AgentSpec(
        name="pi",
        executable="pi",
        exec_args=(PROMPT,),
        interactive_args=(PROMPT,),
        note="not installed here; the prompt is passed positionally as a guess",
    ),
)
