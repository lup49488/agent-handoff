"""AgentAdapter -- the seam that keeps the core from being hard-wired to Codex.

An adapter knows how to start one agent and nothing else. Failure
classification lives in the runner, since it is about output patterns rather
than about any one agent; an adapter that needs its own rules can override
`detect_failure`.
"""

from __future__ import annotations

import abc
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence


class AdapterError(RuntimeError):
    pass


class AgentAdapter(abc.ABC):
    #: identifier used on the CLI and in task.json
    name: str = "agent"
    #: aliases accepted on the CLI, e.g. "claude" for "claude-code"
    aliases: Sequence[str] = ()
    #: executable looked up on PATH
    executable: str = ""

    def __init__(self, root: Path, executable: Optional[str] = None):
        self.root = Path(root)
        if executable:
            self.executable = executable

    def is_available(self) -> bool:
        """Is this agent's CLI installed on PATH?"""
        return shutil.which(self.executable) is not None

    @abc.abstractmethod
    def resume_argv(self, instruction: str) -> List[str]:
        """Command line that starts the agent with `instruction` as its first turn."""

    def interactive_argv(self, prompt: str) -> List[str]:
        """How to start the agent for a user who is watching."""
        return self.resume_argv(prompt)

    def exec_argv(self, prompt: str) -> List[str]:
        """How to start the agent with its stdio piped.

        A CLI that drives a terminal UI usually refuses to run that way, so
        agents that have a non-interactive mode name it here.
        """
        return self.interactive_argv(prompt)

    def argv(self, prompt: str, *, capture_output: bool = False) -> List[str]:
        return self.exec_argv(prompt) if capture_output else self.interactive_argv(prompt)

    def start(
        self, prompt: str, *, capture_output: bool = False
    ) -> subprocess.Popen:
        """Start an agent in the tracked repository and return its process."""
        if not self.is_available():
            raise AdapterError(
                self.executable + " not found on PATH -- install it or use --dry-run"
            )
        try:
            options = {}
            if os.name == "nt":
                # Lets the supervisor terminate the whole CLI process tree.
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            return subprocess.Popen(
                self.argv(prompt, capture_output=capture_output),
                cwd=str(self.root),
                # A supervised agent gets its task from argv and must never
                # wait on standard input. Codex reads stdin to append to the
                # prompt, so an inherited handle that never reaches EOF hangs
                # it indefinitely -- silently, until the stall timeout fires
                # a quarter of an hour later. An interactive launch keeps the
                # terminal, because a person is there to type into it.
                stdin=subprocess.DEVNULL if capture_output else None,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                **options,
            )
        except OSError as exc:
            raise AdapterError("failed to start " + self.name + ": " + str(exc)) from exc

    def resume(self, instruction: str, dry_run: bool = False) -> int:
        """Launch an interactive continuation and return its exit code."""
        if dry_run:
            return 0
        return self.start(instruction).wait()

    def detect_failure(self, stdout: str, stderr: str, exit_code: int) -> Optional[str]:
        """Agent-specific failure classification, layered over the runner's.

        Returning None means "no opinion" -- the runner's shared patterns
        decide. Override this only for a provider whose errors the generic
        patterns get wrong.
        """
        return None
