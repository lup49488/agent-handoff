"""Phase 6 -- health-aware agent selection.

The doc's own example: Codex is out of quota, so ask whether Claude is
available before launching it, and fall further down the chain if not.
"""

import datetime as dt
import json
import sys

import pytest

from agent_handoff import config as cfg
from agent_handoff import health as health_mod
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.runner import run
from agent_handoff.store import Store


@pytest.fixture
def book(tmp_path):
    return health_mod.Health(tmp_path / "health.json")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="Fix it", source_agent="codex", fallback_agent="claude-code"))
    return s


# -- the record -------------------------------------------------------------


def test_an_unknown_agent_is_ready(book):
    assert not book.cooling("codex")
    assert book.get("codex").describe() == "ready"


def test_a_quota_failure_puts_an_agent_out_of_the_running(book):
    from agent_handoff.health import DEFAULT_COOLDOWNS

    expected = DEFAULT_COOLDOWNS["quota_exhausted"]
    book.record_failure("codex", "quota_exhausted")
    assert book.cooling("codex")
    assert expected - 100 < book.get("codex").seconds_left() <= expected
    assert "quota_exhausted" in book.get("codex").describe()


def test_cooldowns_differ_by_reason(book):
    book.record_failure("codex", "rate_limited")
    book.record_failure("claude-code", "quota_exhausted")
    assert book.get("codex").seconds_left() < book.get("claude-code").seconds_left()


def test_a_full_context_is_not_an_agent_health_problem(book):
    # The session filled up, not the agent. A fresh session starts empty.
    book.record_failure("codex", "context_exhausted")
    assert not book.cooling("codex")


def test_a_crash_does_not_condemn_the_agent(book):
    # It may well have been about this one task.
    book.record_failure("codex", "process_crashed")
    assert not book.cooling("codex")


def test_success_clears_a_previous_failure(book):
    book.record_failure("codex", "quota_exhausted")
    book.record_success("codex")
    assert not book.cooling("codex")
    assert book.get("codex").failures == 0
    assert book.get("codex").describe() == "ready"


def test_failures_accumulate(book):
    book.record_failure("codex", "rate_limited")
    book.record_failure("codex", "rate_limited")
    assert book.get("codex").failures == 2


def test_config_can_override_a_cooldown(book):
    book.record_failure("codex", "quota_exhausted", {"quota_exhausted": 10})
    assert book.get("codex").seconds_left() <= 10


# -- persistence ------------------------------------------------------------


def test_health_survives_the_process(book, tmp_path):
    book.record_failure("codex", "quota_exhausted")
    assert health_mod.Health(tmp_path / "health.json").cooling("codex")


def test_stale_health_instances_merge_updates_under_the_user_lock(tmp_path):
    """Two projects can update the shared file without losing each other."""
    path = tmp_path / "health.json"
    first = health_mod.Health(path)
    second = health_mod.Health(path)
    first.record_failure("codex", "quota_exhausted")
    second.record_failure("claude-code", "rate_limited")
    fresh = health_mod.Health(path)
    assert fresh.cooling("codex")
    assert fresh.cooling("claude-code")


def test_disabled_health_never_persists_or_deprioritises_agents():
    book = health_mod.DisabledHealth()
    book.record_failure("codex", "quota_exhausted")
    assert not book.cooling("codex")
    assert book.order(["codex", "claude-code"]) == ["codex", "claude-code"]


def test_health_is_per_user_not_per_project(tmp_path, monkeypatch):
    # An agent out of quota is out of quota in every repository.
    monkeypatch.setenv(health_mod.ENV_HOME, str(tmp_path / "home"))
    health_mod.Health.open().record_failure("codex", "quota_exhausted")
    assert health_mod.Health.open().cooling("codex")
    assert (tmp_path / "home" / "health.json").is_file()


def test_a_corrupt_health_file_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "health.json"
    path.write_text("{ truncated", encoding="utf-8")
    assert health_mod.Health(path).get("codex").describe() == "ready"


def test_unknown_fields_in_the_file_are_dropped(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps({"agents": {"codex": {"reason": "rate_limited", "future_field": 1}}}),
        encoding="utf-8",
    )
    assert health_mod.Health(path).get("codex").reason == "rate_limited"


# -- selection --------------------------------------------------------------


def test_a_cooling_agent_goes_to_the_back(book):
    book.record_failure("claude-code", "quota_exhausted")
    assert book.order(["codex", "claude-code", "pi"]) == ["codex", "pi", "claude-code"]


def test_nothing_is_ever_dropped(book):
    # A cooldown is a guess; it must not be able to strand a task.
    for agent in ("codex", "claude-code"):
        book.record_failure(agent, "quota_exhausted")
    assert set(book.order(["codex", "claude-code"])) == {"codex", "claude-code"}


def test_among_cooling_agents_the_closest_to_recovery_comes_first(book):
    book.record_failure("codex", "quota_exhausted")  # an hour
    book.record_failure("claude-code", "rate_limited")  # five minutes
    assert book.order(["codex", "claude-code"]) == ["claude-code", "codex"]


def test_ready_agents_keep_their_chain_order(book):
    assert book.order(["codex", "claude-code", "pi"]) == ["codex", "claude-code", "pi"]


# -- the runner -------------------------------------------------------------


class Script(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-c", self.script]


DIES = "import sys; print('You\\'ve hit your usage limit.', file=sys.stderr); raise SystemExit(1)"
WORKS = "print('finished')"


def test_a_failure_is_remembered_for_next_time(store, book):
    run(
        store,
        Script(store.root, "codex", DIES),
        [Script(store.root, "claude-code", WORKS)],
        health=book,
    )
    assert book.get("codex").reason == "quota_exhausted"
    assert book.cooling("codex")
    assert not book.cooling("claude-code")  # it succeeded


def test_the_chain_skips_an_agent_known_to_be_out(store, book):
    """Codex out of quota -> is claude available? No -> go on to pi."""
    book.record_failure("claude-code", "quota_exhausted")
    run(
        store,
        Script(store.root, "codex", DIES),
        [Script(store.root, "claude-code", WORKS), Script(store.root, "pi", WORKS)],
        health=book,
    )
    task = store.read_task()
    assert task.source_agent == "pi"  # claude-code was passed over
    assert task.attempted == ["codex"]  # and never launched
    events = [e for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_deprioritised"]
    assert events and events[0]["agent"] == "claude-code"


def test_a_cooling_agent_is_still_used_when_it_is_all_there_is(store, book):
    book.record_failure("claude-code", "quota_exhausted")
    assert (
        run(
            store,
            Script(store.root, "codex", DIES),
            [Script(store.root, "claude-code", WORKS)],
            health=book,
        )
        == 0
    )
    assert store.read_task().source_agent == "claude-code"


def test_success_after_a_cooldown_clears_it(store, book):
    book.record_failure("codex", "rate_limited")
    run(
        store,
        Script(store.root, "codex", WORKS),
        [Script(store.root, "claude-code", WORKS)],
        health=book,
    )
    assert not book.cooling("codex")


def test_cli_run_uses_a_healthy_fallback_before_launching_a_cooling_primary(
    store, tmp_path, monkeypatch
):
    """Health routing prevents the wasted first launch on a new task."""
    monkeypatch.setenv(health_mod.ENV_HOME, str(tmp_path / "home"))
    health_mod.Health.open().record_failure("codex", "quota_exhausted")

    adapters = {
        "codex": Script(store.root, "codex", WORKS),
        "claude-code": Script(store.root, "claude-code", WORKS),
    }
    import agent_handoff.cli as cli

    monkeypatch.setattr(cli.registry, "get", lambda name, root: adapters[name])
    assert cli.main(["run"]) == 0
    assert store.read_task().source_agent == "claude-code"
    events = list(store.iter_jsonl(store.events_file))
    assert any(e["event"] == "agent_selected" and e["agent"] == "claude-code" for e in events)


# -- config -----------------------------------------------------------------


def test_config_reads_health_settings():
    config = cfg.from_dict({"health": False, "cooldowns": {"quota_exhausted": 60}})
    assert config.health is False
    assert config.cooldowns == {"quota_exhausted": 60}


def test_an_unknown_cooldown_reason_is_rejected():
    # A typo would silently never apply.
    with pytest.raises(cfg.ConfigError, match="not a failure reason"):
        cfg.from_dict({"cooldowns": {"quota_exceeded": 60}})


def test_a_nonsense_cooldown_is_rejected():
    with pytest.raises(cfg.ConfigError, match="number of seconds"):
        cfg.from_dict({"cooldowns": {"rate_limited": -5}})
    with pytest.raises(cfg.ConfigError, match="must be true or false"):
        cfg.from_dict({"health": "yes"})


def test_config_file_round_trip(store, tmp_path):
    (tmp_path / "handoff.toml").write_text(
        "health = false\n\n[cooldowns]\nquota_exhausted = 120\n", encoding="utf-8"
    )
    config = cfg.load(tmp_path)
    assert config.health is False
    assert config.cooldowns["quota_exhausted"] == 120


# -- CLI --------------------------------------------------------------------


def test_cli_health_lists_every_agent(store, capsys):
    assert main(["health"]) == 0
    out = capsys.readouterr().out
    assert "codex" in out and "claude-code" in out and "pi" in out
    assert "ready" in out
    assert "Cooldowns are guesses" in out


def test_cli_health_shows_a_cooldown(store, capsys):
    health_mod.Health.open().record_failure("codex", "quota_exhausted")
    assert main(["health"]) == 0
    out = capsys.readouterr().out
    assert "cooling down after quota_exhausted" in out


def test_cli_health_clear_one_agent(store, capsys):
    book = health_mod.Health.open()
    book.record_failure("codex", "quota_exhausted")
    book.record_failure("claude-code", "quota_exhausted")
    assert main(["health", "--clear", "codex"]) == 0
    fresh = health_mod.Health.open()
    assert not fresh.cooling("codex")
    assert fresh.cooling("claude-code")


def test_cli_health_clear_everything(store, capsys):
    health_mod.Health.open().record_failure("codex", "quota_exhausted")
    assert main(["health", "--clear"]) == 0
    assert not health_mod.Health.open().cooling("codex")
    assert "every agent" in capsys.readouterr().out


def test_cli_health_needs_no_project(tmp_path, monkeypatch, capsys):
    # Health is per-user: it is answerable from anywhere.
    monkeypatch.chdir(tmp_path)
    assert main(["health"]) == 0


def test_cli_health_is_not_blocked_by_a_busy_project(store, capsys):
    from agent_handoff import lock as lock_mod

    with lock_mod.Lock(store.lock_file, "someone-else"):
        assert main(["health"]) == 0
