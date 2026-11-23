"""Telling a user which agents lack the agent-handoff skill.

In a failover drill on a machine where Codex had the skill and Claude Code
did not, Claude Code edited before checkpointing and handed over a diff and
nothing else; its owner had no way to know the skill was missing.
"""

import json

import pytest

from agent_handoff import skillcheck
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.cli import main


@pytest.fixture
def homes(tmp_path, monkeypatch):
    codex = tmp_path / "codex-home"
    claude = tmp_path / "claude-home"
    codex.mkdir()
    claude.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return codex, claude, project


def install(directory, text="# skill\n"):
    (directory / "skills" / "agent-handoff").mkdir(parents=True)
    (directory / "skills" / "agent-handoff" / "SKILL.md").write_text(text, encoding="utf-8")


def test_a_skill_in_the_users_skills_directory_is_found(homes, monkeypatch):
    codex, claude, project = homes
    monkeypatch.setattr(skillcheck, "checkout_skill", lambda: None)
    install(codex)
    install(claude)

    assert skillcheck.status("codex", project)[0] == skillcheck.INSTALLED
    assert skillcheck.status("claude-code", project)[0] == skillcheck.INSTALLED


def test_a_skill_in_the_project_is_found(homes, monkeypatch):
    _, _, project = homes
    monkeypatch.setattr(skillcheck, "checkout_skill", lambda: None)
    install(project / ".claude")

    assert skillcheck.status("claude-code", project)[0] == skillcheck.INSTALLED


def test_an_enabled_claude_code_plugin_counts(homes):
    _, claude, project = homes
    (claude / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"agent-handoff@agent-handoff": True}}), encoding="utf-8")

    state, detail = skillcheck.status("claude-code", project)
    assert state == skillcheck.INSTALLED and "plugin" in detail


def test_a_disabled_plugin_does_not(homes):
    _, claude, project = homes
    (claude / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"agent-handoff@agent-handoff": False}}), encoding="utf-8")

    assert skillcheck.status("claude-code", project)[0] == skillcheck.MISSING


def test_a_missing_skill_comes_with_how_to_install_it(homes):
    _, _, project = homes
    state, detail = skillcheck.status("claude-code", project)
    assert state == skillcheck.MISSING
    assert "/plugin install agent-handoff@agent-handoff" in detail


def test_a_skill_that_differs_from_the_checkout_is_outdated(homes, monkeypatch, tmp_path):
    codex, _, project = homes
    current = tmp_path / "checkout-SKILL.md"
    current.write_text("# skill, current\n", encoding="utf-8")
    monkeypatch.setattr(skillcheck, "checkout_skill", lambda: current)
    install(codex, "# skill, from an older release\n")

    state, detail = skillcheck.status("codex", project)
    assert state == skillcheck.OUTDATED and "reinstall" in detail


def test_line_endings_alone_do_not_make_a_skill_outdated(homes, monkeypatch, tmp_path):
    codex, _, project = homes
    current = tmp_path / "checkout-SKILL.md"
    current.write_bytes(b"# skill\n")
    monkeypatch.setattr(skillcheck, "checkout_skill", lambda: current)
    (codex / "skills" / "agent-handoff").mkdir(parents=True)
    (codex / "skills" / "agent-handoff" / "SKILL.md").write_bytes(b"# skill\r\n")

    assert skillcheck.status("codex", project)[0] == skillcheck.INSTALLED


def test_an_agent_with_no_known_skill_location_is_unknown_not_missing(homes):
    assert skillcheck.status("gemini", homes[2])[0] == skillcheck.UNKNOWN


def test_agents_and_init_name_an_installed_agent_without_the_skill(homes, monkeypatch, capsys):
    codex, _, _ = homes
    monkeypatch.setattr(skillcheck, "checkout_skill", lambda: None)
    monkeypatch.setattr(AgentAdapter, "is_available",
                        lambda self: self.name in ("codex", "claude-code"))
    install(codex)

    assert main(["agents"]) == 0
    out = capsys.readouterr().out
    assert "note: claude-code has no agent-handoff skill installed" in out
    assert "note: codex" not in out

    assert main(["init", "fix the totals"]) == 0
    out = capsys.readouterr().out
    assert "note: claude-code has no agent-handoff skill installed" in out
    assert "note: codex" not in out


def test_an_agent_that_is_not_installed_is_not_nagged_about(homes, monkeypatch, capsys):
    monkeypatch.setattr(AgentAdapter, "is_available", lambda self: False)
    assert main(["agents"]) == 0
    assert "no agent-handoff skill" not in capsys.readouterr().out
