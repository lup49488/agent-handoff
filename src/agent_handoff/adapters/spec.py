"""Phase 4 -- agents described as data rather than as code.

An adapter's whole job is knowing how to start one agent with a prompt. That
is a command line, so it can be a spec rather than a class, which means a new
agent can be added from config without touching this package.

Two command lines, not one: a supervised run pipes the agent's stdio, and a
CLI that drives a terminal UI refuses to start that way ("stdin is not a
terminal"). So each agent declares how to be run non-interactively (`exec`)
and how to be handed to a watching user (`interactive`).
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional, Sequence, Tuple

#: Placeholder replaced by the prompt. Matched whole, never formatted, so a
#: prompt containing braces or a stray placeholder is passed through as text.
PROMPT = "{prompt}"


@dataclasses.dataclass(frozen=True)
class AgentSpec:
    """Everything the layer needs to know about one agent."""

    name: str
    executable: str
    #: argv after the executable, non-interactive (supervised runs)
    exec_args: Tuple[str, ...] = (PROMPT,)
    #: argv after the executable, interactive (a human is watching)
    interactive_args: Tuple[str, ...] = (PROMPT,)
    aliases: Tuple[str, ...] = ()
    note: str = ""
    #: Which release of the agent's CLI this command line was checked against.
    #: Empty means nobody has run it -- see builtins.py.
    verified: str = ""
    #: "built-in" or "config"
    source: str = "built-in"

    def argv(self, prompt: str, interactive: bool) -> List[str]:
        args = self.interactive_args if interactive else self.exec_args
        return [self.executable, *[prompt if part == PROMPT else part for part in args]]

    def display(self, interactive: bool = False) -> str:
        """The command line as a human would type it."""
        return " ".join(self.argv('"<prompt>"', interactive))


def spec_from_dict(name: str, data: dict, source: str = "config") -> AgentSpec:
    """Build a spec from a config table, refusing anything it cannot honour."""
    from agent_handoff.adapters.base import AdapterError

    known = {"executable", "exec", "interactive", "aliases", "note"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise AdapterError(
            "agent '" + name + "': unknown key(s) " + ", ".join(unknown)
            + " -- known: " + ", ".join(sorted(known))
        )

    def _argv(key: str, default: Sequence[str]) -> Tuple[str, ...]:
        value = data.get(key)
        if value is None:
            return tuple(default)
        if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
            raise AdapterError("agent '" + name + "': " + key + " must be a list of strings")
        if PROMPT not in value:
            raise AdapterError(
                "agent '" + name + "': " + key + " must contain " + PROMPT
                + " -- otherwise the agent is started with no task"
            )
        return tuple(value)

    executable = data.get("executable", name)
    if not isinstance(executable, str) or not executable:
        raise AdapterError("agent '" + name + "': executable must be a non-empty string")

    aliases = data.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(i, str) for i in aliases):
        raise AdapterError("agent '" + name + "': aliases must be a list of strings")

    exec_args = _argv("exec", (PROMPT,))
    return AgentSpec(
        name=name,
        executable=executable,
        exec_args=exec_args,
        interactive_args=_argv("interactive", exec_args),
        aliases=tuple(aliases),
        note=str(data.get("note", "")),
        source=source,
    )


def merged(base: Optional[AgentSpec], name: str, data: dict) -> AgentSpec:
    """Overlay a config table on a built-in spec, keeping what it omits."""
    if base is None:
        return spec_from_dict(name, data)
    combined = {
        "executable": base.executable,
        "exec": list(base.exec_args),
        "interactive": list(base.interactive_args),
        "aliases": list(base.aliases),
        "note": base.note,
    }
    combined.update(data)
    spec = spec_from_dict(name, combined)
    # Whatever was verified upstream was verified for the built-in command
    # line, not for the one this config just changed.
    return dataclasses.replace(spec, source="config", verified="")
