"""The classifier, checked against what the real CLIs actually print.

Every string in OBSERVED was found verbatim in the shipped binaries of the
agents named, on 2026-09-08:

    Claude Code 2.1.263   ~/.local/bin/claude.exe
    Codex CLI   0.153.4   .../OpenAI/Codex/bin/codex.exe

They are not invented examples. The first version of FAILURE_PATTERNS was
written from plausible-sounding prose and misclassified 7 of these, because
real errors arrive as identifiers (`rate_limit_error`, `InternalServerError`,
`QuotaExceeded`) at least as often as they arrive as sentences. A later round
against a real exhaustion added another: Codex says "usage limit" where Claude
Code says "session limit", and only the first was covered.

When an agent's error vocabulary changes, this is the file to update, and the
one that says where the strings came from.
"""

import pytest

from agent_handoff.runner import classify_failure

#: (agent, message observed in that agent's binary, reason it must classify as)
OBSERVED = [
    # Observed in a real Claude Code 2.1.263 session that ran out:
    # {"isApiErrorMessage": true, "apiErrorStatus": 429, "error": "rate_limit",
    #  "message": {"content": [{"type": "text", "text": <this>}]}}
    (
        "claude-code",
        "You've hit your session limit \u00b7 resets 12:20am (America/Los_Angeles)",
        "quota_exhausted",
    ),
    ("claude-code", "Usage limit reached", "quota_exhausted"),
    ("claude-code", "you have reached your weekly usage limit", "quota_exhausted"),
    ("claude-code", "your usage limit resets", "quota_exhausted"),
    ("claude-code", "The quota has been exceeded.", "quota_exhausted"),
    ("codex", "You've hit your usage limit.", "quota_exhausted"),
    (
        "codex",
        "You've hit your usage limit. Upgrade to Plus to continue using Codex "
        "(https://chatgpt.com/explore/plus)",
        "quota_exhausted",
    ),
    ("codex", "quota exceeded", "quota_exhausted"),
    ("codex", "QuotaExceeded", "quota_exhausted"),
    ("claude-code", '{"type":"rate_limit_error"}', "rate_limited"),
    ("claude-code", '429: "Too Many Requests"', "rate_limited"),
    ("claude-code", "Rate limited (429). Polling too frequently.", "rate_limited"),
    ("claude-code", '{"type":"overloaded_error"}', "provider_error"),
    ("codex", "InternalServerError", "provider_error"),
    ("codex", "ConnectionFailed", "network_failure"),
    ("codex", "context window exceeded", "context_exhausted"),
    ("claude-code", "The model has reached its context window limit.", "context_exhausted"),
]


@pytest.mark.parametrize("agent,message,reason", OBSERVED, ids=[o[1][:40] for o in OBSERVED])
def test_real_error_messages_are_classified(agent, message, reason):
    assert classify_failure(message, "", 1) == reason


@pytest.mark.parametrize("agent,message,reason", OBSERVED, ids=[o[1][:40] for o in OBSERVED])
def test_the_same_messages_on_stderr(agent, message, reason):
    # Codex writes its whole session transcript to stderr and only the final
    # answer to stdout, so both streams have to be classified.
    assert classify_failure("", message, 1) == reason


#: Machine-level failures that read like provider failures but are not. Handing
#: the task to another agent on this machine would not help.
NOT_A_PROVIDER_FAILURE = [
    "error: Your computer ran out of file descriptors (SystemFdQuotaExceeded)",
    "ProcessFdQuotaExceeded",
    "disk quota exceeded",
]


@pytest.mark.parametrize("message", NOT_A_PROVIDER_FAILURE)
def test_local_resource_exhaustion_is_not_a_provider_quota(message):
    assert classify_failure(message, "", 1) == "process_crashed"


def test_a_real_transcript_that_merely_mentions_limits(capsys):
    # An agent that finished may well have been reading rate-limit code.
    transcript = (
        "Reviewing rateLimit.ts\n"
        "The 429 handler adds a Retry-After header\n"
        "3 passed, 0 failed\n"
    )
    assert classify_failure(transcript, "", 0) is None


# -- the failure mode that produces no error text at all --------------------
#
# Probing Claude Code against an unreachable endpoint produced *nothing*: no
# stdout, no stderr, no exit, for 90 seconds until it was killed. A supervisor
# that only reacts to exit codes waits forever on that, which is exactly the
# lost time this tool exists to prevent.

import sys as _sys

from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.models import Task
from agent_handoff.runner import run
from agent_handoff.store import Store


class _Script(AgentAdapter):
    executable = _sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-u", "-c", self.script]


SILENT_FOREVER = "import time; time.sleep(300)"
CHATTY = "import time\nfor _ in range(6):\n    print('working'); time.sleep(0.3)"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="Fix it", source_agent="codex", fallback_agent="claude-code"))
    return s


def test_a_silent_agent_is_handed_off_instead_of_waited_on(store):
    code = run(
        store,
        _Script(store.root, "codex", SILENT_FOREVER),
        [_Script(store.root, "claude-code", "print('took over')")],
        stall_timeout=2,
    )
    assert code == 0
    task = store.read_task()
    assert task.status == "done"
    assert task.source_agent == "claude-code"
    failures = [e for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_unavailable"]
    assert failures[0]["reason"] == "agent_stalled"


def test_an_agent_that_keeps_talking_is_left_alone(store):
    # Output resets the clock: a working agent is never killed for being slow.
    assert (
        run(
            store,
            _Script(store.root, "codex", CHATTY),
            [_Script(store.root, "claude-code", "print('unused')")],
            stall_timeout=1,
        )
        == 0
    )
    task = store.read_task()
    assert task.status == "done"
    assert task.source_agent == "codex"  # no handoff happened
    assert task.attempted == []


def test_no_timeout_means_wait(store):
    # The old behaviour is still available, deliberately chosen.
    assert (
        run(
            store,
            _Script(store.root, "codex", "print('quick')"),
            [_Script(store.root, "claude-code", "print('unused')")],
            stall_timeout=None,
        )
        == 0
    )
