"""Phase 4 -- the universal agent adapter."""

import subprocess
import sys

import pytest

from agent_handoff import config as cfg
from agent_handoff.adapters import AdapterError
from agent_handoff.adapters import registry
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.adapters.spec import PROMPT, AgentSpec, spec_from_dict
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.store import Store


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def init(root, source="codex", fallback="claude-code"):
    store = Store(root)
    store.init(
        Task(original_request="Fix the auth bug", source_agent=source, fallback_agent=fallback)
    )
    return store


# -- the shipped agents -----------------------------------------------------


def test_the_documented_agents_all_exist():
    # Phase 4 names Pi, Gemini CLI and OpenCode alongside the original pair.
    assert set(registry.agent_names()) >= {"codex", "claude-code", "gemini", "opencode", "pi"}


def test_aliases_resolve():
    assert registry.canonical_name("claude") == "claude-code"
    assert registry.canonical_name("gemini-cli") == "gemini"
    assert registry.canonical_name("CODEX") == "codex"


def test_unknown_agent_points_at_the_config_escape_hatch():
    with pytest.raises(AdapterError, match=r"\[agents.whatever\]"):
        registry.canonical_name("whatever")


# -- interactive vs piped ---------------------------------------------------


def test_supervised_and_watched_runs_use_different_command_lines(project):
    # `codex <prompt>` drives a terminal UI and refuses piped stdio, so a
    # supervised run has to use the non-interactive subcommand instead.
    codex = registry.get("codex", project)
    assert codex.exec_argv("do it") == ["codex", "exec", "--sandbox", "workspace-write", "do it"]
    assert codex.interactive_argv("do it") == ["codex", "do it"]
    assert codex.argv("do it", capture_output=True) == codex.exec_argv("do it")
    assert codex.argv("do it", capture_output=False) == codex.interactive_argv("do it")


def test_an_agent_without_a_separate_mode_uses_one_command_line(project):
    pi = registry.get("pi", project)
    assert pi.exec_argv("do it") == pi.interactive_argv("do it") == ["pi", "do it"]


def test_a_plain_adapter_still_only_needs_resume_argv(project):
    # Subclasses written before Phase 4 keep working unchanged.
    class Old(AgentAdapter):
        name = "old"
        executable = "old"

        def resume_argv(self, instruction):
            return ["old", "--go", instruction]

    old = Old(project)
    assert old.exec_argv("x") == old.interactive_argv("x") == ["old", "--go", "x"]


def test_the_prompt_is_substituted_not_formatted(project):
    # A prompt full of braces is data, not a template.
    prompt = "fix {this} and {prompt} in dict{}"
    assert registry.get("codex", project).exec_argv(prompt) == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        prompt,
    ]


# -- specs from config ------------------------------------------------------


def test_config_can_correct_a_builtin(project):
    cfg.from_dict({"agents": {"gemini": {"exec": ["--yolo", "-p", PROMPT]}}})
    spec = registry.find("gemini")
    assert spec.argv("go", interactive=False) == ["gemini", "--yolo", "-p", "go"]
    assert spec.executable == "gemini"  # untouched keys survive the overlay
    assert spec.aliases == ("gemini-cli",)
    assert spec.source == "config"


def test_config_can_invent_an_agent_the_package_never_heard_of(project):
    cfg.from_dict(
        {
            "primary": "my-agent",
            "fallback": ["codex"],
            "agents": {
                "my-agent": {
                    "executable": "my-cli",
                    "exec": ["--batch", PROMPT],
                    "interactive": [PROMPT],
                    "aliases": ["mine"],
                }
            },
        }
    )
    adapter = registry.get("mine", project)
    assert adapter.name == "my-agent"
    assert adapter.exec_argv("go") == ["my-cli", "--batch", "go"]
    assert adapter.interactive_argv("go") == ["my-cli", "go"]


def test_interactive_defaults_to_the_exec_command(project):
    spec = spec_from_dict("solo", {"exec": ["run", PROMPT]})
    assert spec.interactive_args == ("run", PROMPT)


def test_a_command_line_without_the_placeholder_is_refused():
    # Otherwise the agent starts with no task at all.
    with pytest.raises(AdapterError, match="must contain"):
        spec_from_dict("broken", {"exec": ["--resume"]})


def test_spec_keys_are_validated():
    with pytest.raises(AdapterError, match="unknown key"):
        spec_from_dict("broken", {"command": ["x"]})
    with pytest.raises(AdapterError, match="must be a list of strings"):
        spec_from_dict("broken", {"exec": "run {prompt}"})
    with pytest.raises(AdapterError, match="non-empty string"):
        spec_from_dict("broken", {"executable": ""})


def test_a_bad_agent_table_surfaces_as_a_config_error():
    with pytest.raises(cfg.ConfigError, match="must contain"):
        cfg.from_dict({"agents": {"pi": {"exec": ["--go"]}}})
    with pytest.raises(cfg.ConfigError, match="must be a table"):
        cfg.from_dict({"agents": {"pi": "pi --go"}})


def test_the_overlay_does_not_leak_between_loads(project):
    cfg.from_dict({"agents": {"pi": {"executable": "pi2"}}})
    assert registry.find("pi").executable == "pi2"
    cfg.from_dict({})  # a config without agents restores the built-ins
    assert registry.find("pi").executable == "pi"
    assert registry.find("pi").source == "built-in"


def test_config_file_round_trip(project):
    init(project)
    (project / "handoff.toml").write_text(
        'primary = "opencode"\n'
        'fallback = ["gemini"]\n'
        "\n"
        "[agents.opencode]\n"
        'exec = ["run", "--quiet", "{prompt}"]\n',
        encoding="utf-8",
    )
    config = cfg.load(project)
    assert config.chain == ["opencode", "gemini"]
    assert registry.get("opencode", project).exec_argv("go") == [
        "opencode",
        "run",
        "--quiet",
        "go",
    ]


# -- the chain, end to end --------------------------------------------------


def test_a_config_defined_agent_can_take_over_a_handoff(project):
    """A brand-new agent, defined only in config, finishing another's work."""
    store = init(project, source="codex", fallback="helper")
    script = "import sys; print('resumed:', sys.argv[-1][:20])"
    (project / "handoff.toml").write_text(
        'primary = "codex"\n'
        'fallback = ["helper"]\n'
        "\n"
        "[agents.helper]\n"
        'executable = "' + sys.executable.replace("\\", "\\\\") + '"\n'
        'exec = ["-c", "' + script + '", "{prompt}"]\n',
        encoding="utf-8",
    )
    cfg.load(project)

    from agent_handoff.adapters.base import AgentAdapter as Base
    from agent_handoff.runner import run

    class Dies(Base):
        name = "codex"
        executable = sys.executable

        def resume_argv(self, instruction):
            return [sys.executable, "-c", "import sys; sys.exit(1)"]

    assert run(store, Dies(project), [registry.get("helper", project)]) == 0
    task = store.read_task()
    assert task.status == "done"
    assert task.source_agent == "helper"
    assert "resumed:" in (store.logs_dir / "stdout.log").read_text(encoding="utf-8")


# -- CLI --------------------------------------------------------------------


def test_cli_agents_shows_the_actual_command_lines(project, capsys):
    init(project)
    assert main(["agents"]) == 0
    out = capsys.readouterr().out
    assert 'codex exec --sandbox workspace-write "<prompt>"' in out
    assert 'claude -p --permission-mode acceptEdits "<prompt>"' in out
    assert 'opencode run "<prompt>"' in out
    assert "1. codex" in out  # chain position
    assert "[agents.<name>]" in out


def test_cli_agents_verbose_shows_source_and_aliases(project, capsys):
    init(project)
    assert main(["agents", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "interactive: codex " in out
    assert "aliases: claude" in out
    assert "built-in" in out


def test_cli_agents_reflects_a_config_override(project, capsys):
    init(project)
    (project / "handoff.toml").write_text(
        '[agents.pi]\nexecutable = "pi-cli"\nexec = ["--go", "{prompt}"]\n', encoding="utf-8"
    )
    assert main(["agents", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert 'pi-cli --go "<prompt>"' in out
    assert "config" in out


def test_cli_agents_survives_a_broken_config(project, capsys):
    init(project)
    (project / "handoff.toml").write_text('primary = "no-such-agent"\n', encoding="utf-8")
    assert main(["agents"]) == 0
    out = capsys.readouterr().out
    assert "config not usable" in out
    assert "codex" in out  # still lists the built-ins


def test_cli_init_accepts_a_config_defined_agent(project):
    (project / "handoff.toml").write_text(
        'primary = "my-agent"\nfallback = ["codex"]\n\n[agents.my-agent]\nexecutable = "mine"\n',
        encoding="utf-8",
    )
    assert main(["init", "Fix the auth bug"]) == 0
    assert Store(project).read_task().source_agent == "my-agent"


def test_cli_switch_to_a_new_agent(project, capsys):
    init(project)
    (project / "handoff.toml").write_text(
        '[agents.my-agent]\nexecutable = "mine"\nexec = ["--go", "{prompt}"]\n', encoding="utf-8"
    )
    assert main(["switch", "--to", "my-agent", "--dry-run"]) == 0
    out = capsys.readouterr().out
    # A dry run shows the interactive form, which defaults to the exec one.
    assert "mine --go" in out
