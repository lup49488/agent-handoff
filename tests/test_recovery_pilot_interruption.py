"""The pilot interrupts mid-task, and the Handoff arm is handed over as the product would.

Four source sessions under the v0.4/v0.5 wall-clock rule all finished long
before the 300-second cutoff, so no recovery was ever measured; in all four,
progress and completion were first seen in the same probe round. And when a
session is stopped mid-task, the agent has usually not rendered HANDOFF.md
itself, though `handoff run` and `handoff switch` both do at a handoff.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from benchmarks import recovery_pilot as pilot

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def counting(results):
    """A probe returning each of `results` in turn, then the last forever."""
    calls = []

    def probe():
        calls.append(1)
        return results[min(len(calls), len(results)) - 1]

    return probe


# -- the trigger ---------------------------------------------------------------


def test_the_session_is_stopped_once_progress_passes(tmp_path):
    started = time.monotonic()
    result = pilot.run_bounded_process(
        SLEEPER, tmp_path, 60,
        progress_probe=counting([False, False, True]),
        completion_probe=lambda: False,
        probe_interval=0.1, stop_after_progress=0,
    )

    assert result["interrupted_by_trigger"] is True
    assert result["timed_out"] is False
    assert result["first_verified_progress_seconds"] is not None
    assert time.monotonic() - started < 30  # nowhere near the 60-second budget


def test_a_session_that_completes_in_the_same_round_is_not_cut_off(tmp_path):
    """Completion is checked before the trigger fires, in the same round."""
    finishes = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    result = pilot.run_bounded_process(
        finishes, tmp_path, 60,
        progress_probe=lambda: True, completion_probe=lambda: True,
        probe_interval=0.1, stop_after_progress=0,
    )

    assert result["interrupted_by_trigger"] is False
    assert result["first_verified_completion_seconds"] is not None
    assert result["exit_code"] == 0


def test_without_a_trigger_progress_does_not_stop_the_session(tmp_path):
    finishes = [sys.executable, "-c", "import time; time.sleep(1)"]
    result = pilot.run_bounded_process(
        finishes, tmp_path, 60,
        progress_probe=lambda: True, completion_probe=lambda: False, probe_interval=0.1,
    )

    assert result["interrupted_by_trigger"] is False
    assert result["exit_code"] == 0


def test_the_trigger_is_recorded_as_the_termination_reason(tmp_path):
    def invoke(phase, project, prompt, budget):
        return {"elapsed_seconds": 12.0, "timed_out": False, "interrupted_by_trigger": phase == "source",
                "first_verified_progress_seconds": 12.0, "first_verified_completion_seconds": None}

    states = iter([False, True])  # not complete at the stop; complete after recovery
    result = pilot.execute_arm(
        tmp_path, "task", invoke,
        progress_check=lambda p: True,
        completion_check=lambda p: next(states, True),
        package_check=lambda p: {"present": False, "valid": False, "sha256": None, "errors": []},
    )

    assert result["interruption"]["termination_reason"] == "progress_trigger"
    assert result["recovery"] is not None


# -- the handoff at the interruption ------------------------------------------


def stopped(tmp_path):
    """A source session the trigger stopped after five seconds."""
    def invoke(phase, project, prompt, budget):
        return {"elapsed_seconds": 5.0, "timed_out": False, "interrupted_by_trigger": phase == "source",
                "first_verified_progress_seconds": 5.0, "first_verified_completion_seconds": None}
    return invoke


def test_the_handoff_arm_is_packed_and_both_states_are_recorded(tmp_path):
    seen = []

    def package_check(project):
        return {"present": bool(seen), "valid": bool(seen), "sha256": None, "errors": []}

    def pack(project):
        seen.append(project)
        return {"ran": True, "exit_code": 0}

    result = pilot.execute_arm(
        tmp_path, "task", stopped(tmp_path),
        progress_check=lambda p: True, completion_check=lambda p: False,
        package_check=package_check, pack=pack,
    )
    boundary = result["interruption"]

    assert boundary["checkpoint_at_stop"]["present"] is False   # what the agent left
    assert boundary["checkpoint"]["present"] is True            # what recovery receives
    assert boundary["pack"] == {"ran": True, "exit_code": 0}


def test_a_session_that_completed_is_not_packed(tmp_path):
    packed = []
    result = pilot.execute_arm(
        tmp_path, "task", stopped(tmp_path),
        progress_check=lambda p: True, completion_check=lambda p: True,
        package_check=lambda p: {"present": False, "valid": False, "sha256": None, "errors": []},
        pack=lambda p: packed.append(p) or {"ran": True},
    )

    assert packed == []
    assert result["interruption"]["pack"] is None


def run_cli(project, *args):
    env = dict(os.environ, PYTHONPATH=str(pilot.ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "agent_handoff", *args], cwd=str(project),
                          env=env, capture_output=True, text=True, check=True)


def test_pack_renders_the_package_the_agent_had_not(tmp_path):
    """The real CLI, on state an agent left mid-task: tracked, checkpointed, never packed."""
    project = tmp_path / "project"
    head = pilot._initialize_project(project)
    assert head
    run_cli(project, "init", "Extend the export command")
    run_cli(project, "checkpoint", "--objective", "CSV projection", "--next", "null rendering")
    assert not (project / "HANDOFF.md").exists()

    result = pilot.pack_at_interruption(project)

    assert result["ran"] is True and result["exit_code"] == 0, result
    assert pilot.checkpoint_evidence(project)["valid"] is True
    assert "null rendering" in (project / "HANDOFF.md").read_text(encoding="utf-8")


def test_nothing_is_created_for_a_session_that_never_started_tracking(tmp_path):
    project = tmp_path / "project"
    pilot._initialize_project(project)

    result = pilot.pack_at_interruption(project)

    assert result["ran"] is False
    assert not (project / ".agent-handoff").exists()
    assert not (project / "HANDOFF.md").exists()


def test_the_baseline_arm_never_receives_a_harness_package():
    record = {
        "schema_version": 1, "arm": "baseline",
        "evaluator_sha256": {"progress": "a", "acceptance": "b"},
        "preflights": [{"phase": "source", "visible_user_skills": []}],
        "result": {
            "source": {},
            "interruption": {"pack": {"ran": True}, "completion_passed": False,
                             "termination_reason": "progress_trigger"},
            "recovery": {"primary_success": False},
        },
    }

    assert "the baseline arm must not receive a harness-rendered package" in pilot.validate_record(record)


# -- the plan --------------------------------------------------------------------


def skill():
    return {"name": "agent-handoff", "path": "skill/SKILL.md", "sha256": "a" * 64}


def test_new_plans_freeze_the_interruption_policy(tmp_path):
    plan = pilot.build_plan(7, "m", "codex-cli test", skill(), replicate="r03")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    assert plan["schema_version"] == 2
    assert pilot.interruption_policy(pilot._read_plan(path)) == pilot.INTERRUPTION_POLICY

    plan["interruption"] = dict(plan["interruption"], grace_seconds=30)
    plan["manifest_sha256"] = pilot._plan_manifest_hash(plan)
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="interruption policy differs"):
        pilot._read_plan(path)


def test_a_schema_1_plan_reads_back_as_the_wall_clock_rule_it_ran_under(tmp_path):
    """D-r01 and D-r02 were run without a trigger and without a pack."""
    legacy = pilot.build_plan(7, "m", "codex-cli test", skill(), replicate="r02")
    legacy["schema_version"] = 1
    legacy.pop("interruption")
    legacy["manifest_sha256"] = pilot._plan_manifest_hash(legacy)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    policy = pilot.interruption_policy(pilot._read_plan(path))

    assert policy["trigger"] == "source_budget"
    assert policy["pack_handoff_arm"] is False


# -- the scope a correct solution is held to -----------------------------------


def test_every_file_the_fixture_changes_is_in_scope():
    """v0.5's reference edits schema.py, which a hand-written list left out."""
    assert "schema.py" in pilot.ALLOWED_CHANGES
    assert "test_public.py" not in pilot.ALLOWED_CHANGES


def test_calibration_holds_the_reference_to_the_scope_audit():
    variants = pilot.calibrate()["variants"]

    assert variants["reference"]["scope_violations"] == []
    assert variants["partial"]["scope_violations"] == []


# -- a stop that cannot reach the whole process tree ---------------------------


def kill_direct_child_only(proc):
    """As in a sandbox that refuses taskkill: the child goes, its worker stays."""
    proc.kill()
    return False


def test_a_trigger_stop_is_not_waited_out_or_called_a_timeout(tmp_path, monkeypatch):
    """A worker holding the pipes kept the harness waiting for the whole budget,
    and the triggered session was then recorded as having run out of time."""
    monkeypatch.setattr(pilot, "PIPE_DRAIN_SECONDS", 1.0)
    leaves_worker = [sys.executable, "-c",
                     "import subprocess, sys, time; "
                     "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); "
                     "time.sleep(60)"]
    started = time.monotonic()
    result = pilot.run_bounded_process(
        leaves_worker, tmp_path, 4,
        progress_probe=counting([False, True]), completion_probe=lambda: False,
        probe_interval=0.2, stop_after_progress=0, kill_tree=kill_direct_child_only,
    )

    assert time.monotonic() - started < 15
    assert result["interrupted_by_trigger"] is True
    assert result["timed_out"] is False
    assert result["process_tree_terminated"] is False


def test_an_invalid_trial_cannot_be_primary_success():
    record = {
        "schema_version": 1, "arm": "handoff",
        "evaluator_sha256": {"progress": "a", "acceptance": "b"},
        "preflights": [{"phase": "source", "visible_user_skills": ["agent-handoff"]}],
        "result": {
            "source": {"process_tree_terminated": False},
            "invalid_reason": "process_tree_not_terminated",
            "interruption": {"completion_passed": False, "termination_reason": "progress_trigger"},
            "recovery": {"primary_success": True, "acceptance_passed": True,
                         "scope_violations": [], "within_budget": True},
        },
    }

    assert "an invalid trial cannot be primary success" in pilot.validate_record(record)


def test_a_pack_that_errors_is_recorded_not_raised(tmp_path, monkeypatch):
    """By the time of the pack, the source session has already spent its quota."""
    (tmp_path / ".agent-handoff").mkdir()

    def times_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("handoff pack", 120)

    monkeypatch.setattr(pilot.subprocess, "run", times_out)

    result = pilot.pack_at_interruption(tmp_path)

    assert result["ran"] is True
    assert result["exit_code"] is None
    assert "TimeoutExpired" in result["error"]
