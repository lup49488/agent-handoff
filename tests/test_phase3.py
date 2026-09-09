"""Phase 3 -- bidirectional handoff and the fallback chain."""

import sys

import pytest

from agent_handoff import config as cfg
from agent_handoff.adapters.base import AdapterError, AgentAdapter
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.runner import run
from agent_handoff.store import Store


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def init(root, source="codex", fallback="claude-code"):
    store = Store(root)
    store.init(Task(original_request="Fix the auth bug", source_agent=source, fallback_agent=fallback))
    return store


# -- the minimal TOML reader ------------------------------------------------
# Exercised directly, because on 3.11+ the real tomllib is what load() uses.


def test_mini_toml_reads_the_documented_shape():
    data = cfg.parse_toml('primary = "codex"\nfallback = ["claude-code"]\n')
    assert data == {"primary": "codex", "fallback": ["claude-code"]}


def test_mini_toml_reads_a_multiline_array():
    data = cfg.parse_toml('fallback = [\n    "claude-code",\n    "codex",\n]\n')
    assert data["fallback"] == ["claude-code", "codex"]


def test_mini_toml_handles_comments_and_types():
    data = cfg.parse_toml(
        '# a comment\nprimary = "codex"  # trailing\ncheckpoint_protocol = false\ncount = 3\n'
    )
    assert data == {"primary": "codex", "checkpoint_protocol": False, "count": 3}


def test_mini_toml_keeps_a_hash_inside_a_string():
    assert cfg.parse_toml('primary = "co#dex"')["primary"] == "co#dex"


def test_mini_toml_reads_tables():
    data = cfg.parse_toml('[handoff]\nprimary = "codex"\n')
    assert data == {"handoff": {"primary": "codex"}}


def test_mini_toml_reads_documented_nested_agent_tables():
    data = cfg.parse_toml(
        '[agents.helper]\nexecutable = "helper-cli"\nexec = ["--run", "{prompt}"]\n'
    )
    assert data["agents"]["helper"]["executable"] == "helper-cli"
    assert cfg.from_dict(data).agents["helper"]["exec"] == ["--run", "{prompt}"]


def test_mini_toml_refuses_what_it_cannot_read():
    with pytest.raises(cfg.ConfigError):
        cfg.parse_toml("primary = codex")  # unquoted string
    with pytest.raises(cfg.ConfigError):
        cfg.parse_toml('fallback = [\n    "claude-code",\n')  # unterminated


# -- config semantics -------------------------------------------------------


def test_defaults_without_a_config_file(project):
    config = cfg.load(project)
    assert config.chain == ["codex", "claude-code"]
    assert config.path is None


def test_load_reads_the_config_file(project):
    init(project)
    (project / ".agent-handoff" / "config.toml").write_text(
        'primary = "claude"\nfallback = ["codex"]\n', encoding="utf-8"
    )
    config = cfg.load(project)
    assert config.chain == ["claude-code", "codex"]  # aliases resolved
    assert config.path is not None


def test_root_level_config_is_also_read(project):
    (project / "handoff.toml").write_text('primary = "claude-code"\n', encoding="utf-8")
    assert cfg.load(project).primary == "claude-code"


def test_a_bare_string_fallback_is_accepted(project):
    assert cfg.from_dict({"fallback": "claude"}).fallback == ["claude-code"]


def test_unknown_keys_are_rejected():
    # A typo here would silently change which agent picks up the work.
    with pytest.raises(cfg.ConfigError, match="unknown key"):
        cfg.from_dict({"primary": "codex", "fallbacks": ["claude-code"]})


def test_unknown_agent_is_rejected():
    with pytest.raises(cfg.ConfigError, match="unknown agent"):
        cfg.from_dict({"primary": "no-such-agent"})


def test_an_agent_cannot_be_its_own_fallback():
    with pytest.raises(cfg.ConfigError, match="both the primary and a fallback"):
        cfg.from_dict({"primary": "codex", "fallback": ["codex"]})


def test_duplicate_fallbacks_are_rejected():
    with pytest.raises(cfg.ConfigError, match="twice"):
        cfg.from_dict({"primary": "codex", "fallback": ["claude", "claude-code"]})


def test_next_after_walks_the_chain():
    config = cfg.from_dict({"primary": "codex", "fallback": ["claude-code"]})
    assert config.next_after("codex") == "claude-code"
    assert config.next_after("claude-code") is None
    assert config.next_after("codex", exclude=["claude-code"]) is None


def test_the_chain_points_both_ways():
    forward = cfg.from_dict({"primary": "codex", "fallback": ["claude-code"]})
    backward = cfg.from_dict({"primary": "claude-code", "fallback": ["codex"]})
    assert forward.next_after("codex") == "claude-code"
    assert backward.next_after("claude-code") == "codex"


# -- chain failover ---------------------------------------------------------


class ScriptAdapter(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-c", self.script]


DIES = "import sys; print('quota exhausted', file=sys.stderr); raise SystemExit(1)"
WORKS = "print('finished the task')"


class WontStart(ScriptAdapter):
    def start(self, prompt, *, capture_output=False):
        raise AdapterError(self.name + " could not start")


def test_work_walks_down_the_whole_chain(project):
    store = init(project)
    primary = ScriptAdapter(project, "codex", DIES)
    second = ScriptAdapter(project, "claude-code", DIES)
    third = ScriptAdapter(project, "pi", WORKS)

    assert run(store, primary, [second, third]) == 0
    task = store.read_task()
    assert task.status == "done"
    assert task.attempted == ["codex", "claude-code"]
    assert task.handoff_count == 2  # two real handoffs
    assert task.source_agent == "pi"


def test_each_hop_writes_a_fresh_package(project):
    store = init(project)
    run(
        store,
        ScriptAdapter(project, "codex", DIES),
        [ScriptAdapter(project, "claude-code", DIES), ScriptAdapter(project, "pi", WORKS)],
    )
    events = [e for e in store.iter_jsonl(store.events_file) if e["event"] == "handoff_started"]
    assert [(e["from_agent"], e["to"]) for e in events] == [
        ("codex", "claude-code"),
        ("claude-code", "pi"),
    ]


def test_an_agent_that_will_not_start_is_skipped(project):
    store = init(project)
    assert (
        run(
            store,
            ScriptAdapter(project, "codex", DIES),
            [WontStart(project, "claude-code", ""), ScriptAdapter(project, "pi", WORKS)],
        )
        == 0
    )
    task = store.read_task()
    assert task.status == "done"
    assert task.source_agent == "pi"
    assert "claude-code" in task.attempted


def test_an_exhausted_chain_stops_at_handoff_prepared(project):
    store = init(project)
    assert (
        run(
            store,
            ScriptAdapter(project, "codex", DIES),
            [ScriptAdapter(project, "claude-code", DIES)],
        )
        == 1
    )
    task = store.read_task()
    assert task.status == "handoff_prepared"
    assert task.attempted == ["codex", "claude-code"]
    assert any(e["event"] == "handoff_exhausted" for e in store.iter_jsonl(store.events_file))


def test_the_package_is_addressed_to_the_next_agent(project):
    store = init(project)
    run(
        store,
        ScriptAdapter(project, "codex", DIES),
        [ScriptAdapter(project, "claude-code", DIES), ScriptAdapter(project, "pi", WORKS)],
    )
    # The last package written is the one handing claude-code's work to pi.
    assert "continuing agent: `pi`" in store.handoff_file.read_text(encoding="utf-8")


def test_an_adapter_can_override_the_shared_classifier(project):
    class Opinionated(ScriptAdapter):
        def detect_failure(self, stdout, stderr, exit_code):
            return "context_exhausted" if exit_code else None

    store = init(project)
    run(store, Opinionated(project, "codex", DIES), [ScriptAdapter(project, "claude-code", WORKS)])
    failures = [
        e for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_unavailable"
    ]
    assert failures[0]["reason"] == "context_exhausted"  # not quota_exhausted


def test_an_agent_cannot_be_its_own_fallback_at_runtime(project):
    store = init(project)
    with pytest.raises(RuntimeError, match="cannot be its own fallback"):
        run(store, ScriptAdapter(project, "codex", WORKS), [ScriptAdapter(project, "codex", WORKS)])


def test_an_empty_chain_is_refused(project):
    store = init(project)
    with pytest.raises(RuntimeError, match="no fallback agents"):
        run(store, ScriptAdapter(project, "codex", WORKS), [])


# -- CLI --------------------------------------------------------------------


def test_cli_config_init_and_show(project, capsys):
    init(project)
    assert main(["config", "--init"]) == 0
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "chain: codex -> claude-code" in out
    assert "config.toml" in out


def test_cli_config_init_does_not_clobber(project, capsys):
    init(project)
    main(["config", "--init"])
    path = project / ".agent-handoff" / "config.toml"
    path.write_text('primary = "claude-code"\n', encoding="utf-8")
    assert main(["config", "--init"]) == 1
    assert path.read_text(encoding="utf-8") == 'primary = "claude-code"\n'
    assert main(["config", "--init", "--force"]) == 0


def test_cli_init_takes_its_agents_from_the_config(project, capsys):
    (project / "handoff.toml").write_text(
        'primary = "claude-code"\nfallback = ["codex"]\n', encoding="utf-8"
    )
    assert main(["init", "Fix the auth bug"]) == 0
    task = Store(project).read_task()
    assert task.source_agent == "claude-code"  # the chain points the other way
    assert task.fallback_agent == "codex"


def test_cli_flags_still_win_over_the_config(project):
    (project / "handoff.toml").write_text('primary = "claude-code"\n', encoding="utf-8")
    assert main(["init", "Fix it", "--from", "codex", "--to", "claude"]) == 0
    assert Store(project).read_task().source_agent == "codex"


def test_cli_switch_without_a_target_follows_the_chain(project, capsys):
    init(project)
    assert main(["switch", "--dry-run"]) == 0
    assert "claude" in capsys.readouterr().out


def test_cli_switch_backwards_is_just_another_switch(project, capsys):
    init(project, source="claude-code", fallback="codex")
    (project / "handoff.toml").write_text(
        'primary = "claude-code"\nfallback = ["codex"]\n', encoding="utf-8"
    )
    assert main(["switch", "--dry-run"]) == 0
    assert "codex" in capsys.readouterr().out


def test_cli_switch_reports_an_exhausted_chain(project, capsys):
    store = init(project)
    task = store.read_task()
    task.attempted = ["claude-code"]
    store.write_task(task)
    assert main(["switch"]) == 2
    assert "no agent left in the chain" in capsys.readouterr().out


def test_cli_switch_warns_before_handing_back_to_a_failed_agent(project, capsys):
    store = init(project)
    task = store.read_task()
    task.attempted = ["claude-code"]
    store.write_task(task)
    assert main(["switch", "--to", "claude", "--dry-run"]) == 0
    assert "already ran out on this task once" in capsys.readouterr().out


def test_cli_run_reports_a_missing_primary(project, capsys):
    init(project)
    assert main(["run", "codex", "--fallback", "claude"]) == 1
    assert "not found on PATH" in capsys.readouterr().out


def test_cli_run_skips_uninstalled_agents_in_the_chain(project, capsys, monkeypatch):
    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: self.name == "codex")
    # (the conftest guard already hides every real agent from PATH)
    init(project)
    assert main(["run", "codex", "--fallback", "claude"]) == 1
    out = capsys.readouterr().out
    assert "claude-code is not installed" in out
    assert "no fallback agent is installed" in out


def test_default_fallback_follows_the_primary():
    assert cfg.from_dict({"primary": "claude-code"}).fallback == ["codex"]
    assert cfg.from_dict({"primary": "codex"}).fallback == ["claude-code"]


def test_dry_run_leaves_no_trace(project, capsys):
    store = init(project)
    before = list(store.iter_jsonl(store.events_file))
    assert main(["switch", "--to", "claude", "--dry-run"]) == 0
    # No package written, no invented failure in the journal.
    assert not store.handoff_file.exists()
    assert list(store.iter_jsonl(store.events_file)) == before
    assert store.read_task().status == "running"
    assert "dry run" in capsys.readouterr().out


def test_config_is_readable_before_any_task_exists(tmp_path, monkeypatch, capsys):
    # Config belongs to the project, not to a task: `handoff config` must work
    # in a repo where nothing has been initialized yet.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "handoff.toml").write_text('primary = "claude-code"\n', encoding="utf-8")
    assert main(["config"]) == 0
    assert "claude-code -> codex" in capsys.readouterr().out


def test_config_init_before_any_task_writes_to_the_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["config", "--init"]) == 0
    assert (tmp_path / "handoff.toml").is_file()
