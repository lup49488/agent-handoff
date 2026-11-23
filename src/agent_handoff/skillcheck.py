"""Whether an agent has the agent-handoff skill installed.

The skill is what tells an agent working on its own -- not under `handoff
run` -- to checkpoint before its first edit and record its test results.
Without it the agent never runs `handoff checkpoint`, and if it runs out of
quota the package holds a diff and nothing about what it found. In a
failover drill on a machine where Codex had the skill and Claude Code did
not, Claude Code edited first and handed over exactly that; its owner had no
way to know the skill was missing.

Only Codex and Claude Code are known here, and only where their own
documentation puts skills. A look that finds nothing is reported as
missing; an agent this module does not know is reported as unknown, never
as missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple

SKILL = "agent-handoff"

INSTALLED = "installed"
OUTDATED = "outdated"
MISSING = "missing"
UNKNOWN = "unknown"

#: How to install the skill for each agent, from a checkout of this repository.
INSTALL_HINTS = {
    "codex": "cp -r skills/agent-handoff ~/.codex/skills/agent-handoff",
    "claude-code": "/plugin install agent-handoff@agent-handoff, "
                   "or cp -r skills/agent-handoff ~/.claude/skills/agent-handoff",
}


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _skill_files(agent: str, root: Path) -> List[Path]:
    if agent == "codex":
        return [_codex_home() / "skills" / SKILL / "SKILL.md", root / ".codex" / "skills" / SKILL / "SKILL.md"]
    if agent == "claude-code":
        return [_claude_home() / "skills" / SKILL / "SKILL.md", root / ".claude" / "skills" / SKILL / "SKILL.md"]
    return []


def checkout_skill() -> Optional[Path]:
    """The skill in the source checkout this package runs from, if it runs from one.

    An editable install sits in `src/agent_handoff/` of a checkout whose
    `skills/` holds the current skill; a wheel has no such directory.
    """
    candidate = Path(__file__).resolve().parents[2] / "skills" / SKILL / "SKILL.md"
    return candidate if candidate.is_file() else None


def _differs_from_checkout(installed: Path) -> bool:
    """True when a copied skill no longer matches the checkout's (a release changed it)."""
    current = checkout_skill()
    if current is None:
        return False
    try:
        return installed.read_bytes().replace(b"\r\n", b"\n") != current.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return False


def _claude_plugin(root: Path) -> Optional[str]:
    """Where Claude Code has the agent-handoff plugin enabled, if anywhere."""
    for settings in (_claude_home() / "settings.json", root / ".claude" / "settings.json",
                     root / ".claude" / "settings.local.json"):
        try:
            enabled = json.loads(settings.read_text(encoding="utf-8")).get("enabledPlugins") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(enabled, dict) and any(
                key.split("@", 1)[0] == SKILL and value for key, value in enabled.items()):
            return "plugin, enabled in " + str(settings)
    return None


def status(agent: str, root: Path) -> Tuple[str, str]:
    """`(state, detail)`: where the skill was found, or how to install it."""
    files = _skill_files(agent, Path(root))
    if not files:
        return UNKNOWN, "no known skill location for this agent"
    for path in files:
        if path.is_file():
            if _differs_from_checkout(path):
                return OUTDATED, (str(path.parent) + " differs from this checkout's skill; "
                                  "reinstall: " + INSTALL_HINTS[agent])
            return INSTALLED, str(path.parent)
    if agent == "claude-code":
        plugin = _claude_plugin(Path(root))
        if plugin:
            return INSTALLED, plugin
    return MISSING, INSTALL_HINTS[agent]
