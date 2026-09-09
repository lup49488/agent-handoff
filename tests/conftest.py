import shutil

import pytest

from agent_handoff import health
from agent_handoff.adapters import registry

#: Executables the suite must never actually launch. They may well be
#: installed on the machine running these tests, and a test that spawns a real
#: coding agent burns the developer's quota and hangs the run.
REAL_AGENT_EXECUTABLES = {"codex", "claude", "gemini", "opencode", "pi"}


@pytest.fixture(autouse=True)
def never_launch_a_real_agent(monkeypatch):
    real_which = shutil.which

    def guarded(cmd, *args, **kwargs):
        if str(cmd).lower() in REAL_AGENT_EXECUTABLES:
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", guarded)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Agent health is per-user state; tests must never touch the real file."""
    monkeypatch.setenv(health.ENV_HOME, str(tmp_path_factory.mktemp("handoff-home")))


@pytest.fixture(autouse=True)
def clean_registry():
    """The config overlay is global state; no test may leak it into another."""
    registry.reset()
    yield
    registry.reset()
