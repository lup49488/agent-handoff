"""Phase 3 -- the fallback chain, read from config.

    primary = "codex"
    fallback = ["claude-code", "pi"]

`fallback` is an ordered chain, not a single agent: when the primary dies the
first available agent takes over, and if that one dies too the next takes over
from *its* state. Nothing in the chain is privileged, which is what makes the
handoff bidirectional -- `primary = "claude-code"` with Codex in the fallback
list is the same machinery pointed the other way.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_handoff.adapters import AdapterError
from agent_handoff.adapters import registry
from agent_handoff.health import DEFAULT_COOLDOWNS

CONFIG_NAMES = ("config.toml", "handoff.toml")

#: Keys understood at the top level or under a [handoff] table.
KNOWN_KEYS = (
    "primary",
    "fallback",
    "checkpoint_protocol",
    "stall_timeout_seconds",
    "health",
    "cooldowns",
    "read_agent_sessions",
    "agents",
)

#: The pair the design doc names: Codex <-> Claude Code, either direction.
DEFAULT_CHAIN = ("codex", "claude-code")
DEFAULT_PRIMARY = DEFAULT_CHAIN[0]
DEFAULT_FALLBACK = DEFAULT_CHAIN[1:]


def default_fallback(primary: str) -> List[str]:
    """The rest of the default chain, so `primary` is never its own fallback."""
    return [name for name in DEFAULT_CHAIN if name != primary]

STARTER = """# agent-handoff configuration
#
# The primary agent does the work; the fallback chain continues it, in order,
# as each agent becomes unavailable.

primary = "__PRIMARY__"
fallback = [
    "__FALLBACK__",
]

# Ask the working agent to maintain .agent-handoff/state.md at milestones.
checkpoint_protocol = true

# Skip agents that recently ran out, instead of launching them to find out.
health = true

# How long each kind of failure keeps an agent out of the running, in seconds.
# Only used when the agent does not publish its own reset time; both Codex and
# Claude Code do, and a measured time always wins.
# [cooldowns]
# quota_exhausted = 18000   # five hours, the window length actually observed
# rate_limited = 300

# Seconds of total silence before a supervised agent counts as unavailable.
# An agent retrying against an unreachable endpoint prints nothing and never
# exits; waiting forever for that is the delay this tool exists to avoid.
# 0 waits forever.
stall_timeout_seconds = 900

# Adapters can be corrected -- or invented -- here. `handoff agents` prints the
# command line each agent will actually be started with.
#
# [agents.opencode]
# exec = ["run", "{prompt}"]
#
# [agents.my-agent]
# executable = "my-agent"
# exec = ["--non-interactive", "{prompt}"]
# interactive = ["{prompt}"]
"""


class ConfigError(RuntimeError):
    pass


# -- a very small TOML reader ----------------------------------------------
# Used only on Python 3.9/3.10, which have no tomllib. It covers the subset
# this file needs -- strings, booleans, integers and string arrays, inline or
# spread over lines -- and refuses anything else rather than guessing.


#: Escapes TOML defines for basic (double-quoted) strings. Literal strings
#: quoted with ' have none, by design.
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "/": "/",
    "e": "\x1b",
}


def _scan(text, on_comma=None):
    """Walk a line, tracking quoting, so quoted commas and # are not special.

    Returns (kept_text, pieces) where pieces is filled only when `on_comma`
    splitting was asked for.
    """
    out = []
    pieces = []
    quote = None
    escaped = False
    for char in text:
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
        elif char == "#" and on_comma is None:
            break  # a comment, since it is outside any string
        elif char == "," and on_comma is not None:
            pieces.append("".join(out))
            out = []
        else:
            out.append(char)
    pieces.append("".join(out))
    return "".join(out) if on_comma is None else "", pieces


def _strip_comment(line: str) -> str:
    kept, _ = _scan(line)
    return kept.strip()


def _outside_strings(text: str) -> str:
    """The line with every quoted run blanked out, for counting brackets."""
    out = []
    quote = None
    escaped = False
    for char in text:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        out.append(char)
    return "".join(out)


def _unescape(text: str) -> str:
    """Resolve the escapes a TOML basic string may contain."""
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= len(text):
            raise ConfigError("string ends in a backslash")
        marker = text[i]
        if marker in _ESCAPES:
            out.append(_ESCAPES[marker])
            i += 1
        elif marker in ("u", "U"):
            width = 4 if marker == "u" else 8
            digits = text[i + 1 : i + 1 + width]
            if len(digits) != width:
                raise ConfigError("truncated unicode escape: " + text[i:])
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                raise ConfigError("bad unicode escape: " + digits) from None
            i += 1 + width
        else:
            raise ConfigError("unknown escape: " + chr(92) + marker)
    return "".join(out)


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        # Only basic strings interpret escapes; 'literal ones' never do.
        return _unescape(inner) if text[0] == '"' else inner
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        raise ConfigError("cannot read value: " + raw.strip()) from None


def _parse_array(raw: str) -> List[Any]:
    inner = raw.strip()[1:-1]
    # Commas inside a string are part of the string: a command line such as
    # ["-c", "print('a', 'b')"] must not be split into four pieces.
    _, pieces = _scan(inner, on_comma=True)
    return [_parse_scalar(item) for item in pieces if item.strip()]


def parse_toml(text: str) -> Dict[str, Any]:
    """Minimal TOML subset -> nested dict."""
    data: Dict[str, Any] = {}
    table = data
    pending_key: Optional[str] = None
    buffer = ""

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line)
        if not line:
            continue

        if pending_key is not None:
            buffer += " " + line
            outside = _outside_strings(buffer)
            if outside.count("[") <= outside.count("]"):
                table[pending_key] = _parse_array(buffer)
                pending_key, buffer = None, ""
            continue

        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            parts = [part.strip() for part in name.split(".")]
            if not name or any(not part for part in parts):
                raise ConfigError("empty table header")
            # TOML's `[agents.helper]` means nested dictionaries, not a
            # literal top-level key named `agents.helper`.
            table = data
            for part in parts:
                existing = table.setdefault(part, {})
                if not isinstance(existing, dict):
                    raise ConfigError("table conflicts with value: " + name)
                table = existing
            continue

        if "=" not in line:
            raise ConfigError("cannot read line: " + line)
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        outside = _outside_strings(value)
        if value.startswith("[") and outside.count("[") > outside.count("]"):
            pending_key, buffer = key, value  # array continues on later lines
            continue
        table[key] = _parse_array(value) if value.startswith("[") else _parse_scalar(value)

    if pending_key is not None:
        raise ConfigError("unterminated array for key: " + pending_key)
    return data


def _read_toml(text: str) -> Dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return parse_toml(text)
    try:
        return tomllib.loads(text)
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise ConfigError("invalid TOML: " + str(exc)) from exc


# -- the config itself ------------------------------------------------------


@dataclasses.dataclass
class Config:
    primary: str = DEFAULT_PRIMARY
    fallback: List[str] = dataclasses.field(default_factory=lambda: list(DEFAULT_FALLBACK))
    checkpoint_protocol: bool = True
    #: Seconds of total silence before a supervised agent counts as stalled.
    #: 0 waits forever.
    stall_timeout_seconds: float = 900.0
    #: Skip agents believed to be out of action. Advice only -- see health.py.
    health: bool = True
    #: `[cooldowns]` overrides, in seconds, keyed by failure reason.
    cooldowns: Dict[str, int] = dataclasses.field(default_factory=dict)
    #: Read an agent's own session log to learn why it stopped. See sessions.py
    #: for exactly which two fields are read, and nothing else.
    read_agent_sessions: bool = True
    #: `[agents.<name>]` tables -- adapters defined or corrected in config.
    agents: Dict[str, dict] = dataclasses.field(default_factory=dict)
    path: Optional[Path] = None

    @property
    def chain(self) -> List[str]:
        """Every agent, in the order they take over."""
        return [self.primary, *self.fallback]

    def next_after(self, name: str, exclude: Optional[List[str]] = None) -> Optional[str]:
        """The agent that continues after `name` runs out."""
        skip = set(exclude or [])
        chain = self.chain
        start = chain.index(name) + 1 if name in chain else 0
        for candidate in chain[start:]:
            if candidate not in skip and candidate != name:
                return candidate
        return None

    def describe(self) -> str:
        source = str(self.path) if self.path else "defaults (no config file)"
        return "  ".join(
            [
                "chain: " + " -> ".join(self.chain),
                "checkpoint_protocol: " + str(self.checkpoint_protocol).lower(),
                "stall timeout: " + (
                    str(int(self.stall_timeout_seconds)) + "s"
                    if self.stall_timeout_seconds else "none"
                ),
                "from: " + source,
            ]
        )


def _canonical(name: Any, field: str) -> str:
    if not isinstance(name, str):
        raise ConfigError(field + " must be an agent name, got: " + repr(name))
    try:
        return registry.canonical_name(name)
    except AdapterError as exc:
        raise ConfigError(field + ": " + str(exc)) from exc


def from_dict(data: Dict[str, Any], path: Optional[Path] = None) -> Config:
    table = dict(data)
    nested = table.pop("handoff", None)
    if isinstance(nested, dict):
        table.update(nested)

    unknown = [k for k in table if k not in KNOWN_KEYS]
    if unknown:
        # A typo here would silently change which agent picks up the work.
        raise ConfigError(
            "unknown key(s): " + ", ".join(sorted(unknown)) + " -- known: " + ", ".join(KNOWN_KEYS)
        )

    agents = table.get("agents", {})
    if not isinstance(agents, dict):
        raise ConfigError("agents must be a table, e.g. [agents.pi]")
    for name, spec in agents.items():
        if not isinstance(spec, dict):
            raise ConfigError("agents." + str(name) + " must be a table")
    try:
        registry.apply_config(agents)
    except AdapterError as exc:
        raise ConfigError(str(exc)) from exc

    primary = _canonical(table.get("primary", DEFAULT_PRIMARY), "primary")

    raw_fallback = table.get("fallback", default_fallback(primary))
    if isinstance(raw_fallback, str):
        raw_fallback = [raw_fallback]
    if not isinstance(raw_fallback, list):
        raise ConfigError("fallback must be an agent name or a list of them")

    fallback: List[str] = []
    for item in raw_fallback:
        name = _canonical(item, "fallback")
        if name == primary:
            raise ConfigError("'" + name + "' is both the primary and a fallback")
        if name in fallback:
            raise ConfigError("'" + name + "' appears twice in the fallback chain")
        fallback.append(name)

    protocol = table.get("checkpoint_protocol", True)
    if not isinstance(protocol, bool):
        raise ConfigError("checkpoint_protocol must be true or false")

    read_sessions = table.get("read_agent_sessions", True)
    if not isinstance(read_sessions, bool):
        raise ConfigError("read_agent_sessions must be true or false")

    use_health = table.get("health", True)
    if not isinstance(use_health, bool):
        raise ConfigError("health must be true or false")

    cooldowns = table.get("cooldowns", {})
    if not isinstance(cooldowns, dict):
        raise ConfigError("cooldowns must be a table, e.g. [cooldowns]")
    for reason, seconds in cooldowns.items():
        if reason not in DEFAULT_COOLDOWNS:
            raise ConfigError(
                "cooldowns." + str(reason) + " is not a failure reason -- known: "
                + ", ".join(sorted(DEFAULT_COOLDOWNS))
            )
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ConfigError("cooldowns." + str(reason) + " must be a number of seconds")

    stall = table.get("stall_timeout_seconds", 900)
    if isinstance(stall, bool) or not isinstance(stall, (int, float)) or stall < 0:
        raise ConfigError("stall_timeout_seconds must be a number of seconds (0 waits forever)")

    return Config(
        primary=primary,
        fallback=fallback,
        checkpoint_protocol=protocol,
        stall_timeout_seconds=float(stall),
        health=use_health,
        cooldowns={k: int(v) for k, v in cooldowns.items()},
        read_agent_sessions=read_sessions,
        agents=agents,
        path=path,
    )


def config_path(root: Path) -> Optional[Path]:
    from agent_handoff import HANDOFF_DIR

    for candidate in (Path(root) / HANDOFF_DIR / CONFIG_NAMES[0], Path(root) / CONFIG_NAMES[1]):
        if candidate.is_file():
            return candidate
    return None


def load(root: Path) -> Config:
    """Read the config for a project, falling back to built-in defaults.

    Loading also installs the config's adapter overlay, so every later name
    lookup sees the agents this project defines.
    """
    path = config_path(root)
    if path is None:
        registry.apply_config({})
        return Config()
    return from_dict(_read_toml(path.read_text(encoding="utf-8")), path=path)


def write_starter(root: Path, primary: str = DEFAULT_PRIMARY, fallback: str = DEFAULT_FALLBACK[0]) -> Path:
    from agent_handoff import HANDOFF_DIR
    from agent_handoff.store import atomic_write

    # Inside the store when there is one, otherwise at the project root --
    # a starter config should not require a task to exist first.
    store_dir = Path(root) / HANDOFF_DIR
    path = (
        store_dir / CONFIG_NAMES[0] if store_dir.is_dir() else Path(root) / CONFIG_NAMES[1]
    )
    # Plain substitution, not str.format: the template contains {prompt}.
    text = STARTER.replace("__PRIMARY__", primary).replace("__FALLBACK__", fallback)
    atomic_write(path, text)
    return path
