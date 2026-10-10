import json
import os
import subprocess
import tempfile
import time
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import Mock, patch

from benchmarks import recovery_pilot as pilot
from agent_handoff.models import Task
from agent_handoff.store import Store
from agent_handoff.handoff import write_package


class RecoveryPilotCalibrationTests(unittest.TestCase):
    def test_fixture_states_separate_progress_from_completion(self):
        result = pilot.calibrate()
        self.assertTrue(result["calibration_passed"], result)
        self.assertFalse(result["live_agents_launched"])
        self.assertEqual(set(result["evaluator_sha256"]), {"progress", "acceptance"})
        variants = result["variants"]
        self.assertFalse(variants["project"]["progress"])
        self.assertTrue(variants["partial"]["progress"])
        self.assertFalse(variants["partial"]["acceptance"])
        self.assertTrue(variants["reference"]["acceptance"])
        self.assertTrue(variants["reference"]["public_tests"])

    def test_launch_requires_an_explicit_plan(self):
        with self.assertRaises(SystemExit) as exit_info:
            pilot.main(["--launch"])
        self.assertEqual(exit_info.exception.code, 2)


class RecoveryPilotBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recovery-pilot-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_isolation_disables_all_user_skills_except_handoff_in_treatment(self):
        skills = [
            (Path("C:/skills/agent-handoff/SKILL.md"), "agent-handoff", "handoff help"),
            (Path("C:/skills/other/SKILL.md"), "other", "other help"),
        ]
        baseline = pilot.codex_isolation_args("baseline", skills)
        treatment = pilot.codex_isolation_args("handoff", skills)
        self.assertIn("features.memories=false", baseline)
        self.assertEqual(baseline[-1].count("enabled=false"), 2)
        self.assertEqual(treatment[-1].count("enabled=false"), 1)
        self.assertEqual(treatment[-1].count("enabled=true"), 1)

    def test_skill_preflight_requires_exact_catalogue_not_bare_name(self):
        skills = [(Path("skill/SKILL.md"), "agent-handoff", "do checkpoint work")]
        rendered = json.dumps([{
            "role": "developer",
            "content": [{"type": "input_text", "text": "Skills: agent-handoff - do checkpoint work"}],
        }])
        self.assertEqual(pilot.visible_user_skills(rendered, skills), ["agent-handoff"])
        bare_name = json.dumps([{
            "role": "developer", "content": [{"type": "input_text", "text": "agent-handoff"}],
        }])
        self.assertEqual(pilot.visible_user_skills(bare_name, skills), [])
        self.assertIsNone(pilot.visible_user_skills("not json", skills))

    def test_interrupt_outcomes_cover_early_completion_and_no_progress(self):
        self.assertEqual(pilot.interrupt_outcome(False, True), "completed_before_interruption")
        self.assertEqual(pilot.interrupt_outcome(True, False), "progress_at_interruption")
        self.assertEqual(pilot.interrupt_outcome(False, False), "no_progress_at_interruption")

    def test_early_completion_skips_recovery(self):
        invoke = Mock(return_value={"exit_code": 0, "stdout": "private log", "stderr": ""})
        result = pilot.execute_arm(
            self.root, "same task", invoke, progress_check=lambda _: True,
            completion_check=lambda _: True,
            package_check=lambda _: {"present": False, "valid": False},
        )
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(invoke.call_args.args[0], "source")
        self.assertEqual(result["interruption"]["outcome"], "completed_before_interruption")
        self.assertTrue(result["recovery_skipped"])
        self.assertNotIn("stdout", result["source"])

    def test_missing_checkpoint_and_no_progress_still_run_fresh_recovery(self):
        invoke = Mock(side_effect=[
            {"exit_code": -9, "timed_out": True, "stdout": "do not forward", "stderr": ""},
            {"exit_code": 0, "timed_out": False, "stdout": "recovery", "stderr": ""},
        ])
        completion = iter((False, True))
        result = pilot.execute_arm(
            self.root, "original request", invoke, progress_check=lambda _: False,
            completion_check=lambda _: next(completion),
            package_check=lambda _: {"present": False, "valid": False, "errors": ["missing"]},
        )
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(invoke.call_args_list[0].args, ("source", self.root, "original request", pilot.SOURCE_SECONDS))
        self.assertEqual(invoke.call_args_list[1].args, ("recovery", self.root, "original request", pilot.RECOVERY_SECONDS))
        self.assertEqual(result["interruption"]["outcome"], "no_progress_at_interruption")
        self.assertFalse(result["interruption"]["checkpoint"]["present"])
        self.assertNotIn("stdout", result["recovery"])
        self.assertTrue(result["recovery"]["primary_success"])

    def test_recovery_preflight_failure_preserves_source_and_boundary_evidence(self):
        calls = []

        def invoke(phase, project, prompt, budget):
            calls.append(phase)
            if phase == "recovery":
                raise RuntimeError("skill preflight failed")
            return {"exit_code": -9, "timed_out": True, "elapsed_seconds": budget}

        result = pilot.execute_arm(
            self.root, "request", invoke, progress_check=lambda _: True,
            completion_check=lambda _: False,
            package_check=lambda _: {"present": False, "valid": False, "errors": ["missing"]},
        )
        self.assertEqual(calls, ["source", "recovery"])
        self.assertEqual(result["source"]["elapsed_seconds"], pilot.SOURCE_SECONDS)
        self.assertEqual(result["interruption"]["outcome"], "progress_at_interruption")
        self.assertEqual(result["recovery"]["error"], "skill preflight failed")

    def test_invalid_checkpoint_is_recorded_not_repaired_or_used_to_skip_recovery(self):
        (self.root / "HANDOFF.md").write_text("truncated", encoding="utf-8")
        evidence = pilot.checkpoint_evidence(self.root)
        self.assertTrue(evidence["present"])
        self.assertFalse(evidence["valid"])
        self.assertTrue(evidence["errors"])
        calls = []
        result = pilot.execute_arm(
            self.root, "task", lambda *args: calls.append(args) or {"exit_code": 0},
            progress_check=lambda _: False, completion_check=lambda _: False,
            package_check=lambda _: evidence,
        )
        self.assertEqual([call[0] for call in calls], ["source", "recovery"])
        self.assertFalse(result["interruption"]["checkpoint"]["valid"])
        self.assertFalse((self.root / ".agent-handoff" / "task.json").exists())

    def test_timeout_kills_process_tree_then_collects_output(self):
        class FakeProcess:
            returncode = None
            calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("codex", timeout)
                self.returncode = -9
                return "partial out", "partial err"

        process = FakeProcess()
        killed = []
        result = pilot.run_bounded_process(
            ["fake-codex"], self.root, 1,
            popen=lambda *args, **kwargs: process,
            kill_tree=lambda proc: killed.append(proc),
        )
        self.assertEqual(killed, [process])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["exit_code"], -9)
        self.assertEqual(result["stdout"], "partial out")

    def test_phase_monitor_records_progress_and_completion(self):
        class DoneProcess:
            returncode = 0

            def communicate(self, timeout=None):
                time.sleep(0.04)
                return "", ""

        result = pilot.run_bounded_process(
            ["local-synthetic-process"], self.root, 5,
            progress_probe=lambda: True,
            completion_probe=lambda: True,
            probe_interval=0.005,
            popen=lambda *args, **kwargs: DoneProcess(),
        )
        self.assertFalse(result["timed_out"])
        self.assertIsNotNone(result["started_at"])
        self.assertIsNotNone(result["finished_at"])
        self.assertIsNotNone(result["first_verified_progress_seconds"])
        self.assertIsNotNone(result["first_verified_completion_seconds"])

    def test_keyboard_interrupt_also_kills_process_tree(self):
        class FakeProcess:
            def communicate(self, timeout=None):
                raise KeyboardInterrupt()

        killed = []
        with self.assertRaises(KeyboardInterrupt):
            pilot.run_bounded_process(
                ["fake-codex"], self.root, 1,
                popen=lambda *args, **kwargs: FakeProcess(),
                kill_tree=lambda proc: killed.append(proc),
            )
        self.assertEqual(len(killed), 1)

    def test_valid_checkpoint_is_hashed_without_harness_writes(self):
        store = Store(self.root)
        store.init(Task(original_request="synthetic", source_agent="codex", fallback_agent="claude-code"))
        write_package(store)
        before = store.handoff_file.read_bytes()
        evidence = pilot.checkpoint_evidence(self.root)
        after = store.handoff_file.read_bytes()
        self.assertTrue(evidence["present"])
        self.assertTrue(evidence["valid"], evidence)
        self.assertEqual(evidence["sha256"], pilot._sha256(store.handoff_file))
        self.assertEqual(before, after)

    def test_contamination_rules_allow_treatment_skill_but_not_cross_arm_leaks(self):
        treatment_text = "Read C:/Users/test/.codex/skills/agent-handoff/SKILL.md"
        self.assertEqual(pilot.contamination_findings("handoff", self.root, treatment_text, ""), [])
        self.assertTrue(pilot.contamination_findings("baseline", self.root, treatment_text, ""))
        memory_text = "Read C:/Users/test/.codex/memories/preferences.md"
        self.assertTrue(pilot.contamination_findings("handoff", self.root, memory_text, ""))

    def test_registration_and_status_are_model_free(self):
        home = self.root / "codex-home"
        skill = home / "skills" / "agent-handoff" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: agent-handoff\ndescription: Preserve coding state.\n---\nbody\n", encoding="utf-8")
        path = self.root / "registered.json"
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            plan = pilot.register_plan(path, 42, "test-model", "codex-cli test")
        self.assertEqual({row["state"] for row in plan["rows"]}, {"pending"})
        self.assertEqual({row["arm"] for row in plan["rows"]}, set(pilot.ARMS))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(pilot.main(["--plan", str(path), "--status"]), 0)
        self.assertIn("pending", output.getvalue())
        plan["model"] = "silently-edited-model"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            pilot._read_plan(path)

    def test_record_validator_rejects_false_early_completion_skip(self):
        record = {
            "schema_version": 1,
            "arm": "baseline",
            "preflights": [{"visible_user_skills": []}],
            "evaluator_sha256": {"progress": "a", "acceptance": "b"},
            "contamination": {"source": []},
            "result": {
                "source": {"exit_code": 0},
                "interruption": {"completion_passed": True},
                "recovery": None,
                "recovery_skipped": True,
            },
        }
        self.assertEqual(pilot.validate_record(record), [])
        record["result"]["interruption"]["completion_passed"] = False
        self.assertTrue(pilot.validate_record(record))

    def test_contaminated_trial_cannot_be_primary_success(self):
        record = {
            "schema_version": 1,
            "arm": "baseline",
            "preflights": [{"visible_user_skills": []}, {"visible_user_skills": []}],
            "evaluator_sha256": {"progress": "a", "acceptance": "b"},
            "contamination": {"source": ["unexpected skill"], "recovery": []},
            "result": {
                "source": {"exit_code": -9},
                "interruption": {"completion_passed": False},
                "recovery_skipped": False,
                "recovery": {
                    "primary_success": True,
                    "acceptance_passed": True,
                    "scope_violations": [],
                    "within_budget": True,
                },
            },
        }
        self.assertTrue(any("contaminated" in item for item in pilot.validate_record(record)))

    def test_preflight_mismatch_fails_before_any_agent_launch(self):
        skills = [(Path("skill/SKILL.md"), "agent-handoff", "do checkpoint work")]
        wrong = json.dumps([{
            "role": "developer", "content": [{"type": "input_text", "text": "agent-handoff"}],
        }])
        completed = subprocess.CompletedProcess(["codex"], 0, stdout=wrong, stderr="")
        with patch.object(pilot.subprocess, "run", return_value=completed) as called:
            with self.assertRaises(RuntimeError):
                pilot._preflight("task", self.root, "handoff", skills, "codex", "test", "medium")
        self.assertEqual(called.call_count, 1)
        self.assertIn("debug", called.call_args.args[0])

    def _plan_path(self):
        data = pilot.build_plan(31, "test-model", "codex-cli test", {
            "name": "agent-handoff", "path": "skill/SKILL.md", "sha256": "f" * 64,
        })
        path = self.root / "pilot.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_plan_claim_is_serial_and_dead_claim_is_invalidated_not_recycled(self):
        path = self._plan_path()
        row = pilot.claim_next(path)
        self.assertEqual(row["state"], "running")
        with self.assertRaises(RuntimeError):
            pilot.claim_next(path)
        with self.assertRaises(RuntimeError):
            pilot.invalidate_abandoned(path, row["trial_id"], alive=lambda _: True)
        pilot.set_active_child(path, row["trial_id"], 12345)
        with self.assertRaisesRegex(RuntimeError, "Codex child is still alive"):
            pilot.invalidate_abandoned(path, row["trial_id"], alive=lambda pid: pid == 12345)
        pilot.invalidate_abandoned(path, row["trial_id"], alive=lambda _: False)
        data = pilot._read_plan(path)
        self.assertEqual(data["rows"][0]["state"], "invalid")
        next_row = pilot.claim_next(path)
        self.assertNotEqual(next_row["trial_id"], row["trial_id"])

    def test_scope_audit_includes_out_of_scope_target_commits(self):
        project = self.root / "repo"
        project.mkdir()
        env = pilot._git_safe_env(project)
        subprocess.run(["git", "init", "--quiet"], cwd=project, env=env, check=True)
        (project / "cli.py").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, env=env, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "base"], cwd=project, env=env, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, env=env, capture_output=True, text=True, check=True).stdout.strip()
        (project / "private.txt").write_text("out of scope\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, env=env, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "bad"], cwd=project, env=env, check=True)
        self.assertEqual(pilot.scope_violations(project, head), ["private.txt"])
        diff = pilot.diff_evidence(project, head)
        self.assertIn("private.txt", diff["changed_files"])
        self.assertTrue(diff["tracked_patch"])
        self.assertTrue(diff["tracked_patch_sha256"])


if __name__ == "__main__":
    unittest.main()
