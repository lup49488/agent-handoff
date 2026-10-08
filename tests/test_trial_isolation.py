"""A trial measures its arm's condition, or it is not counted.

The v0.3 cohort ran each target with its user's own setup. A globally
installed agent-handoff skill made every Baseline agent start handoff
tracking itself, persistent memories described the Baseline/Handoff design to
the agents being measured, and each target's working directory was named
after its arm. These tests hold the three closed.
"""

import json
import sys
from pathlib import Path

import pytest

import benchmarks.run_trial as runner
from benchmarks.plan import plan, write_cohort_plan
from benchmarks.validate_trial import validate

# Taken from the v0.3 B-60-baseline-r06 log, the trial that showed the problem.
SKILL_LINE = (
    '"pwsh.exe" -Command "Get-Content -Raw '
    "'C:\\\\Users\\\\u\\\\.codex\\\\skills\\\\agent-handoff\\\\SKILL.md'\""
)
MEMORY_LINE = "$taskMemory = 'C:\\\\Users\\\\u\\\\.codex\\\\memories\\\\MEMORY.md'; rg -n -i agent-handoff"
HANDOFF_LINE = "handoff --version; handoff status; handoff verify; git status --short"


# -- what the target is launched with ----------------------------------------


def test_codex_runs_without_memories_or_any_user_installed_skill(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    for name in ("agent-handoff", "hatch-pet", ".system"):
        (home / "skills" / name).mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))

    args = runner._isolation_args("codex")
    overrides = [args[i + 1] for i in range(0, len(args), 2) if args[i] == "-c"]

    assert "features.memories=false" in overrides
    assert "memories.use_memories=false" in overrides
    assert "memories.generate_memories=false" in overrides
    skills = next(value for value in overrides if value.startswith("skills.config="))
    assert "agent-handoff" in skills and "hatch-pet" in skills
    assert skills.count("enabled=false") == 2
    # Skills Codex ships with are part of the agent as released.
    assert ".system" not in skills


def test_claude_code_runs_with_skills_disabled_and_a_log_the_scan_can_read():
    args = runner._isolation_args("claude-code")

    assert "--disable-slash-commands" in args
    # Plain print mode logs only the final answer; the scan needs the tool calls.
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args


def test_isolation_arguments_go_before_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    class Adapter:
        def exec_argv(self, prompt):
            return ["codex", "exec", "--sandbox", "workspace-write", prompt]

    argv = runner._target_argv(Adapter(), "the task", "codex", "m", "medium")

    assert argv[-1] == "the task"
    assert "features.memories=false" in argv


# -- reading the target's logs -----------------------------------------------


def test_a_baseline_agent_that_runs_handoff_is_contaminated(tmp_path):
    assert runner._contamination("baseline", tmp_path, "", HANDOFF_LINE) == ["ran the handoff CLI"]


def test_the_handoff_arm_is_meant_to_run_handoff(tmp_path):
    assert runner._contamination("handoff", tmp_path, "", HANDOFF_LINE) == []


@pytest.mark.parametrize("arm", ("baseline", "handoff"))
def test_reading_the_skill_or_memories_contaminates_either_arm(tmp_path, arm):
    found = runner._contamination(arm, tmp_path, "", SKILL_LINE + "\n" + MEMORY_LINE)

    assert "read the agent-handoff skill" in found
    assert "read the agent's persistent memories" in found


def test_a_baseline_project_that_gained_a_package_is_contaminated(tmp_path):
    (tmp_path / ".agent-handoff").mkdir()

    assert runner._contamination("baseline", tmp_path, "", "") == ["created a handoff package"]


def test_ordinary_work_on_the_task_is_not_contamination(tmp_path):
    log = "Implemented `handle_get_tasks` in api.py.\nrg -n handle_get_tasks .\npython acceptance.py"

    assert runner._contamination("baseline", tmp_path, log, log) == []


# -- launching ---------------------------------------------------------------


def fake_target(monkeypatch, script):
    """Launch a short Python program in place of a coding agent."""
    monkeypatch.setattr(runner.registry, "get", lambda target, project: None)
    monkeypatch.setattr(
        runner, "_target_argv", lambda adapter, prompt, target, model, effort: [sys.executable, "-c", script]
    )


def launch(tmp_path, arm="baseline"):
    return runner.run_trial(
        "A", "60", arm, "codex", 1, tmp_path / ".trials" / ("A-60-%s-r01" % arm),
        model="m", target_version="v", launch=True, wall_seconds=60,
    )


def test_the_target_works_in_a_directory_that_names_nothing(tmp_path, monkeypatch):
    """A path like `.trials/B-60-baseline-r06` told the target its arm."""
    fake_target(monkeypatch, "import os; print(os.getcwd())")

    record = launch(tmp_path)
    destination = tmp_path / ".trials" / "A-60-baseline-r01"
    seen = (destination / "target.stdout.log").read_text(encoding="utf-8").strip()

    for leak in ("A-60", "baseline", "r01", ".trials", "agent-handoff"):
        assert leak not in seen
    assert record["isolation"]["workspace"] == "neutral-temporary"
    assert json.loads((destination / "trial.json").read_text(encoding="utf-8")) == record
    assert (destination / "project" / "slug.py").is_file()
    # The temporary workspace does not outlive the trial.
    assert not Path(seen).exists()


def test_a_baseline_target_that_uses_handoff_is_excluded(tmp_path, monkeypatch):
    fake_target(monkeypatch, "print('handoff init \"implement slugify\"')")

    record = launch(tmp_path)

    assert record["accepted"] is False
    assert record["invalid_reason"] == "contaminated"
    assert record["contamination"] == ["ran the handoff CLI"]
    assert validate(record) == []


def test_a_clean_target_is_accepted(tmp_path, monkeypatch):
    fake_target(monkeypatch, "print('done')")

    record = launch(tmp_path)

    assert record["accepted"] is True
    assert record["contamination"] == []
    assert validate(record) == []


# -- the order a balanced plan pre-registered --------------------------------


def test_records_take_their_arm_order_from_a_balanced_plan(tmp_path):
    """Recomputing it with the unbalanced shuffle recorded orders never run.

    Seed 1 is one where the two disagree; the real r06–r09 cohort only
    escaped this by luck.
    """
    path = tmp_path / "balanced.json"
    write_cohort_plan(path, 1, 4, ("B",), ("60",), 1, True)

    for row in plan(1, 4, ("B",), ("60",), 1, True):
        schedule = runner._planned_schedule(row["task"], row["snapshot"], row["arm"], row["replicate"], None, path)
        assert schedule["planned_position"] == row["position"]


# -- the record ---------------------------------------------------------------


def test_contamination_and_its_evidence_travel_together(tmp_path):
    record = runner.run_trial("A", "60", "baseline", "codex", 1, tmp_path / "trial")
    record.update(accepted=False, invalid_reason="contaminated", contamination=[])
    assert "a contaminated trial must say what contaminated it" in validate(record)

    record.update(invalid_reason="target_failed", contamination=["ran the handoff CLI"])
    assert "a trial with contamination evidence must be invalid as contaminated" in validate(record)


# -- the preflight before a launch -------------------------------------------


def test_preflight_fails_when_a_disabled_skill_still_reaches_the_model():
    rendered = '{"skills": [{"name": "agent-handoff", "description": "preserve work"}]}'

    assert runner._preflight_verdict(rendered, ["agent-handoff", "hatch-pet"]) == "failed"
    assert runner._preflight_verdict('{"skills": []}', ["agent-handoff", "hatch-pet"]) == "passed"


def test_preflight_is_unavailable_without_codex_rather_than_failed(tmp_path):
    # The suite hides real agent executables, as a machine without Codex would.
    assert runner._preflight("codex", tmp_path) == "unavailable"
    assert runner._preflight("claude-code", tmp_path) == "unavailable"


def test_a_failed_preflight_refuses_to_launch(tmp_path, monkeypatch):
    fake_target(monkeypatch, "print('should never run')")
    monkeypatch.setattr(runner, "_preflight", lambda target, project: "failed")

    with pytest.raises(RuntimeError, match="isolation preflight failed"):
        launch(tmp_path)
    assert not (tmp_path / ".trials" / "A-60-baseline-r01").exists()


# -- Claude Code's own log ---------------------------------------------------

CLAUDE_MEMORY = r"C:\Users\u\.claude\projects\D--Files-Programs-agent-handoff\memory\MEMORY.md"


def init_event(**fields):
    """The first stream-JSON event, shaped as Claude Code 2.1.287 emits it."""
    event = {
        "type": "system",
        "subtype": "init",
        "cwd": r"C:\Temp\tmpabc\work\project",
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}],
        "memory_paths": {"auto": "C:\\Users\\u\\.claude\\projects\\C--Temp-tmpabc\\memory\\"},
        "skills": [],
        "slash_commands": [],
    }
    event.update(fields)
    return json.dumps(event)


def tool_use(name, **arguments):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": arguments},
    ]}})


def test_a_claude_session_that_loaded_skills_is_contaminated(tmp_path):
    stdout = init_event(skills=["anthropic-skills:docx", "agent-handoff"])

    assert runner._contamination("baseline", tmp_path, stdout, "") == [
        "loaded skills: anthropic-skills:docx, agent-handoff"
    ]


def test_a_claude_session_with_no_skills_and_its_own_empty_memory_is_clean(tmp_path):
    """The init event names the memory directory; naming it is not reading it."""
    assert runner._contamination("baseline", tmp_path, init_event(), "") == []


def test_reading_another_projects_claude_memory_is_contamination(tmp_path):
    stdout = init_event() + "\n" + tool_use("Read", file_path=CLAUDE_MEMORY)

    assert runner._contamination("handoff", tmp_path, stdout, "") == [
        "read the agent's persistent memories"
    ]


def test_a_claude_baseline_running_handoff_through_bash_is_contaminated(tmp_path):
    stdout = init_event() + "\n" + tool_use("Bash", command='handoff init "implement slugify"')

    assert runner._contamination("baseline", tmp_path, stdout, "") == ["ran the handoff CLI"]

