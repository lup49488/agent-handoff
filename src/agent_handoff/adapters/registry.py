"""Adapter lookup.

Built-in specs are fixed; a config's `[agents.*]` tables are an overlay on top
of them, which can correct a built-in agent's command line or introduce an
agent this package has never heard of.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from agent_handoff.adapters.base import AdapterError, AgentAdapter
from agent_handoff.adapters.builtins import BUILTIN_SPECS
from agent_handoff.adapters.spec import AgentSpec, merged

_BUILTIN: Dict[str, AgentSpec] = {spec.name: spec for spec in BUILTIN_SPECS}

#: Config-supplied specs. Replaced wholesale by apply_config, never accumulated.
_OVERLAY: Dict[str, AgentSpec] = {}


class SpecAdapter(AgentAdapter):
    """An adapter built from a spec. Every agent is one of these."""

    def __init__(self, spec: AgentSpec, root: Path):
        super().__init__(root)
        self.spec = spec
        self.name = spec.name
        self.aliases = spec.aliases
        self.executable = spec.executable

    def interactive_argv(self, prompt: str) -> List[str]:
        return self.spec.argv(prompt, interactive=True)

    def exec_argv(self, prompt: str) -> List[str]:
        return self.spec.argv(prompt, interactive=False)

    def resume_argv(self, instruction: str) -> List[str]:
        return self.interactive_argv(instruction)


def specs() -> Dict[str, AgentSpec]:
    """Every known agent, config overlay applied."""
    table = dict(_BUILTIN)
    table.update(_OVERLAY)
    return table


def apply_config(agents: Optional[Dict[str, dict]]) -> None:
    """Install the `[agents.*]` overlay, replacing any previous one."""
    _OVERLAY.clear()
    for name, data in (agents or {}).items():
        _OVERLAY[name] = merged(_BUILTIN.get(name), name, data)


def reset() -> None:
    _OVERLAY.clear()


def _index() -> Dict[str, AgentSpec]:
    table: Dict[str, AgentSpec] = {}
    for spec in specs().values():
        table[spec.name] = spec
        for alias in spec.aliases:
            table.setdefault(alias, spec)
    return table


def known_names() -> List[str]:
    return sorted(_index())


def agent_names() -> List[str]:
    """Canonical names only, no aliases."""
    return sorted(specs())


def find(name: str) -> AgentSpec:
    spec = _index().get(str(name).strip().lower())
    if spec is None:
        raise AdapterError(
            "unknown agent '" + str(name) + "' -- known: " + ", ".join(known_names())
            + " (or define it in config under [agents." + str(name).strip().lower() + "])"
        )
    return spec


def canonical_name(name: str) -> str:
    return find(name).name


def get(name: str, root: Path) -> AgentAdapter:
    return SpecAdapter(find(name), root)
