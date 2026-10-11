"""Task D's offline calibration and opt-in two-session-per-arm recovery pilot.

All preparation and tests are model-free. A real session can start only via
``--launch`` against an explicit, pre-registered plan.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from agent_handoff import __version__ as _handoff_version
from agent_handoff.adapters import registry
from agent_handoff.integrity import errors as integrity_errors
from agent_handoff.integrity import verify_package
from agent_handoff.lock import Lock, process_alive
from agent_handoff.store import Store, atomic_write
from agent_handoff.runner import PIPE_DRAIN_SECONDS
from benchmarks.run_trial import TREE_LAUNCH, _codex_home, _kill_process_tree, _remove_tree, _skill_frontmatter_fields
from benchmarks.run_trial import _contamination as _shared_contamination

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "fixtures" / "task-d-v05"
ARMS = ("baseline", "handoff")
SOURCE_SECONDS = 300
RECOVERY_SECONDS = 600

#: The interruption rule, frozen into every schema-2 plan.
#:
#: Stop the source session the moment the progress check first passes, unless
#: full acceptance passes in the same round. In D-r01 and D-r02 all four source
#: sessions finished long before the 300-second cutoff, so no recovery was ever
#: measured — and in all four, progress and completion were first seen in the
#: same five-second round. Agents write the solution in a burst, so a grace
#: period would mostly let them finish and a coarse probe would miss the
#: window: hence no grace and a one-second probe. The 300 seconds remain the
#: hard stop. The Handoff arm's package is then rendered as the product
#: renders it at a handoff (`pack_at_interruption`).
INTERRUPTION_POLICY = {
    "trigger": "first_progress",
    "grace_seconds": 0,
    "probe_seconds": 1.0,
    "pack_handoff_arm": True,
}

#: What schema-1 plans (D-r01, D-r02) ran under; read back for them so their
#: records keep meaning what they meant.
LEGACY_INTERRUPTION_POLICY = {
    "trigger": "source_budget",
    "grace_seconds": None,
    "probe_seconds": 5.0,
    "pack_handoff_arm": False,
}


def interruption_policy(plan: Dict[str, object]) -> Dict[str, object]:
    return dict(plan.get("interruption") or LEGACY_INTERRUPTION_POLICY)
def _allowed_changes() -> set:
    """Every file a correct solution may change, derived from the fixture.

    A hand-written list left out `schema.py`, which the v0.5 prompt tells the
    target to edit and the reference solution does edit: every correct
    solution, and the partial state at interruption, failed the scope audit.
    The same mistake disqualified task B in the snapshot runner. Deriving the
    set from what the fixture's own partial and reference variants change
    means it cannot fall behind the fixture again. `HANDOFF.md` is the
    package the Handoff arm is meant to produce.
    """
    project = FIXTURE / "project"
    changed = {"HANDOFF.md"}
    for variant in ("partial", "reference"):
        for path in (FIXTURE / variant).rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(FIXTURE / variant).as_posix()
            original = project / name
            if not original.is_file() or original.read_bytes() != path.read_bytes():
                changed.add(name)
    return changed


ALLOWED_CHANGES = _allowed_changes()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_catalog(codex_home: Path) -> List[Tuple[Path, str, str]]:
    skills_dir = codex_home / "skills"
    found = []
    if not skills_dir.is_dir():
        return found
    for directory in sorted(skills_dir.iterdir()):
        skill_file = directory / "SKILL.md"
        if not directory.is_dir() or directory.name.startswith(".") or not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---", text, re.DOTALL)
        if not match:
            raise ValueError("invalid skill frontmatter: " + str(skill_file))
        fields = _skill_frontmatter_fields(match.group(1))
        name, description = fields.get("name"), fields.get("description")
        if not name or not description:
            raise ValueError("skill is missing name/description: " + str(skill_file))
        found.append((skill_file, name, description))
    return found


def codex_isolation_args(arm: str, skills: Sequence[Tuple[Path, str, str]]) -> List[str]:
    """Disable memory and all user Skills, allowing only handoff in treatment."""
    if arm not in ARMS:
        raise ValueError("arm must be baseline or handoff")
    if arm == "handoff" and sum(name == "agent-handoff" for _path, name, _description in skills) != 1:
        raise ValueError("treatment requires exactly one user Skill named agent-handoff")
    entries = []
    for path, name, _description in skills:
        enabled = arm == "handoff" and name == "agent-handoff"
        entries.append("{path=" + json.dumps(str(path)) + ",enabled=" + str(enabled).lower() + "}")
    return [
        "-c", "features.memories=false",
        "-c", "memories.use_memories=false",
        "-c", "memories.generate_memories=false",
        "-c", "skills.config=[" + ",".join(entries) + "]",
    ]


def visible_user_skills(rendered: str, skills: Sequence[Tuple[Path, str, str]]) -> Optional[List[str]]:
    """Return user-skill names present in Codex developer input, or None if unparseable."""
    try:
        messages = json.loads(rendered)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(messages, list):
        return None
    chunks = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "developer":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "input_text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                return None
            chunks.append(" ".join(text.casefold().split()))
    if skills and not chunks:
        return None
    developer = " ".join(chunks)
    return sorted(
        name for _path, name, description in skills
        if " ".join(name.casefold().split()) in developer
        and " ".join(description.casefold().split()) in developer
    )


def interrupt_outcome(progress_passed: bool, completion_passed: bool) -> str:
    if completion_passed:
        return "completed_before_interruption"
    return "progress_at_interruption" if progress_passed else "no_progress_at_interruption"


def contamination_findings(arm: str, project: Path, stdout: str, stderr: str) -> List[str]:
    """Detect visible cross-arm Skill, memory, or handoff-state leakage.

    Delegates to the snapshot runner's scan, granting the Handoff arm the one
    skill its treatment consists of. A second copy of the patterns had already
    stopped recognising `python -m agent_handoff`.
    """
    permitted = ("agent-handoff",) if arm == "handoff" else ()
    return _shared_contamination(arm, project, stdout, stderr, permitted_skills=permitted)


def checkpoint_evidence(project: Path) -> Dict[str, object]:
    """Inspect, but never create or repair, source-phase handoff state."""
    store = Store(project)
    package = store.handoff_file
    package_present = package.is_file()
    findings = verify_package(store)
    valid = package_present and not integrity_errors(findings)
    return {
        "present": package_present,
        "valid": valid,
        "sha256": _sha256(package) if package_present else None,
        "errors": [finding.render() for finding in integrity_errors(findings)],
    }


def pack_at_interruption(project: Path) -> Dict[str, object]:
    """Render the handoff package, as the product does at a handoff.

    A supervised `handoff run` writes HANDOFF.md when its agent fails, and
    `handoff switch` writes it when a user hands over; an agent stopped
    mid-task has usually not run `handoff pack` itself. Leaving the package
    unrendered would measure the skill without the step the product performs
    at exactly this moment. Only rendering happens here: if the source session
    never started tracking, there is nothing to render and nothing is created
    on its behalf — that is the treatment's own outcome.
    """
    if not (project / ".agent-handoff").is_dir():
        return {"ran": False, "reason": "the source session never initialised handoff tracking"}
    env = _git_safe_env(project)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    evidence: Dict[str, object] = {
        "ran": True,
        "command": "handoff pack --to codex",
        "handoff_version": _handoff_version,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agent_handoff", "pack", "--to", "codex"],
            cwd=str(project), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Recorded, not raised: by now the source session has run and spent
        # its quota, and a failed pack is part of what recovery starts from.
        evidence.update(exit_code=None, error="%s: %s" % (type(exc).__name__, exc))
        return evidence
    evidence.update(exit_code=result.returncode, output_tail=(result.stdout + result.stderr)[-400:])
    return evidence


def run_bounded_process(
    argv: Sequence[str], cwd: Path, timeout_seconds: int,
    *, env: Optional[Dict[str, str]] = None, progress_probe: Optional[Callable[[], bool]] = None,
    completion_probe: Optional[Callable[[], bool]] = None, probe_interval: float = 5.0,
    stop_after_progress: Optional[float] = None,
    on_start: Optional[Callable[[int], None]] = None,
    on_finish: Optional[Callable[[], None]] = None,
    popen: Callable = subprocess.Popen,
    kill_tree: Callable = _kill_process_tree,
) -> Dict[str, object]:
    """Run one session, hard-stop its process tree at the budget, and collect output.

    With `stop_after_progress`, the session is also stopped that many seconds
    after the progress check first passes, unless full acceptance passes
    first. That is the interruption trigger: the same rule for both arms,
    fixed before launch, and it lands where meaningful work exists but is not
    finished — which a wall-clock cutoff the agent always beat never did.
    """
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    started = time.monotonic()
    proc = popen(
        list(argv), cwd=str(cwd), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", env=env, **TREE_LAUNCH,
    )
    if on_start is not None:
        try:
            on_start(int(proc.pid))
        except BaseException:
            try:
                kill_tree(proc)
            finally:
                try:
                    proc.communicate()
                except BaseException:
                    pass
            raise
    timed_out = False
    stop_probe = threading.Event()
    first_progress = [None]
    first_completion = [None]
    triggered = [None]
    # False once a kill could reach only the direct child: what it started
    # may still be running and still changing the project after the stop.
    tree_ended = [True]

    def monitor_progress():
        stop_at = None
        while not stop_probe.wait(probe_interval):
            elapsed = round(time.monotonic() - started, 3)
            try:
                if progress_probe is not None and first_progress[0] is None and progress_probe():
                    first_progress[0] = elapsed
                    if stop_after_progress is not None:
                        stop_at = elapsed + stop_after_progress
            except Exception:
                # A probe failure is not evidence of task progress. The
                # immutable final evaluator still determines acceptance.
                pass
            try:
                if completion_probe is not None and first_completion[0] is None and completion_probe():
                    first_completion[0] = elapsed
                    return
            except Exception:
                pass
            # Completion is checked first in the same round, so a session that
            # finished is never cut off for having made progress.
            if stop_at is not None and time.monotonic() - started >= stop_at:
                triggered[0] = round(time.monotonic() - started, 3)
                tree_ended[0] = kill_tree(proc) is not False
                return

    monitor = None
    if progress_probe is not None:
        monitor = threading.Thread(target=monitor_progress, daemon=True)
        monitor.start()
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            # A session the trigger already stopped is not over budget: it
            # only looks that way when something it started kept the pipes
            # open until the budget ran out.
            timed_out = triggered[0] is None
            if kill_tree(proc) is False:
                tree_ended[0] = False
            try:
                stdout, stderr = proc.communicate(timeout=PIPE_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                # Never wait on descendants the kill could not reach.
                tree_ended[0] = False
                stdout, stderr = "", ""
    except BaseException:
        # Ctrl-C or an unexpected supervisor exception must not orphan Codex.
        try:
            kill_tree(proc)
        finally:
            try:
                proc.communicate()
            except BaseException:
                pass
        raise
    finally:
        stop_probe.set()
        if monitor is not None:
            monitor.join(timeout=12)
        if on_finish is not None and proc.poll() is not None:
            on_finish()
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "started_at": started_at,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stdout": stdout or "",
        "stderr": stderr or "",
        "first_verified_progress_seconds": first_progress[0],
        "first_verified_completion_seconds": first_completion[0],
        "interrupted_by_trigger": triggered[0] is not None,
        "trigger_seconds": triggered[0],
        "process_tree_terminated": tree_ended[0],
    }


def _run_check(project: Path, evaluator: str) -> bool:
    result = _run(
        project, "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1], run_name='__main__')",
        str(FIXTURE / (evaluator + ".py")),
    )
    return result.returncode == 0


def scope_violations(project: Path, baseline_head: str) -> List[str]:
    env = _git_safe_env(project)
    tracked = subprocess.run(
        ["git", "diff", "--name-only", baseline_head, "HEAD"], cwd=str(project),
        env=env, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    unstaged = subprocess.run(
        ["git", "diff", "--name-only", baseline_head], cwd=str(project),
        env=env, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=str(project),
        env=env, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    return sorted({path for path in tracked + unstaged + untracked if path not in ALLOWED_CHANGES})


def diff_evidence(project: Path, baseline_head: str) -> Dict[str, object]:
    env = _git_safe_env(project)
    patch = subprocess.run(
        ["git", "diff", "--binary", baseline_head], cwd=str(project), env=env,
        capture_output=True, check=True,
    ).stdout
    tracked = subprocess.run(
        ["git", "diff", "--name-only", baseline_head], cwd=str(project), env=env,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=str(project), env=env,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    untracked_hashes = {}
    project_root = Path(project).resolve()
    for name in untracked:
        candidate = project_root / name
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if project_root not in candidate.resolve().parents:
            continue
        untracked_hashes[name] = _sha256(candidate)
    return {
        "changed_files": sorted(set(tracked + untracked)),
        "tracked_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "tracked_patch": patch.decode("utf-8", errors="replace"),
        "untracked_sha256": untracked_hashes,
    }


def execute_arm(
    project: Path,
    prompt: str,
    invoke: Callable[[str, Path, str, int], Dict[str, object]],
    *, source_budget: int = SOURCE_SECONDS,
    recovery_budget: int = RECOVERY_SECONDS,
    progress_check: Callable[[Path], bool] = lambda p: _run_check(p, "progress"),
    completion_check: Callable[[Path], bool] = lambda p: _run_check(p, "acceptance"),
    package_check: Callable[[Path], Dict[str, object]] = checkpoint_evidence,
    baseline_head: Optional[str] = None,
    pack: Optional[Callable[[Path], Dict[str, object]]] = None,
) -> Dict[str, object]:
    """Two fresh CLI processes in one evolving worktree; no transcript is passed.

    `pack`, given only for the Handoff arm, runs at the interruption what the
    product runs at a handoff. What the agent itself left is recorded as
    `checkpoint_at_stop`; what the recovery session receives, as `checkpoint`.
    """
    source = invoke("source", project, prompt, source_budget)
    progress = progress_check(project)
    complete = completion_check(project)
    checkpoint_at_stop = package_check(project)
    packed = pack(project) if pack is not None and not complete else None
    checkpoint = package_check(project) if packed is not None else checkpoint_at_stop
    boundary = interrupt_outcome(progress, complete)
    if progress and source.get("first_verified_progress_seconds") is None:
        source["first_verified_progress_seconds"] = source.get("elapsed_seconds")
    if complete and source.get("first_verified_completion_seconds") is None:
        source["first_verified_completion_seconds"] = source.get("elapsed_seconds")
    result: Dict[str, object] = {
        "source": {key: value for key, value in source.items() if key not in ("stdout", "stderr")},
        "interruption": {
            "outcome": boundary,
            "termination_reason": (
                "task_completed_before_stop" if complete else
                "progress_trigger" if source.get("interrupted_by_trigger") else
                "source_budget_hard_stop" if source.get("timed_out") else
                "source_process_exited_early"
            ),
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "progress_passed": progress,
            "completion_passed": complete,
            "checkpoint_at_stop": checkpoint_at_stop,
            "pack": packed,
            "checkpoint": checkpoint,
        },
        "recovery": None,
    }
    if complete:
        result["recovery_skipped"] = True
        return result
    try:
        recovery = invoke("recovery", project, prompt, recovery_budget)
    except Exception as exc:
        result["recovery"] = {
            "started": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "acceptance_passed": False,
            "scope_violations": [],
            "within_budget": False,
            "primary_success": False,
        }
        result["recovery_error"] = True
        result["recovery_skipped"] = False
        return result
    final_complete = completion_check(project)
    if progress:
        recovery["first_verified_progress_seconds"] = 0.0
        recovery["progress_present_at_recovery_start"] = True
    elif progress_check(project) and recovery.get("first_verified_progress_seconds") is None:
        recovery["first_verified_progress_seconds"] = recovery.get("elapsed_seconds")
    if final_complete and recovery.get("first_verified_completion_seconds") is None:
        recovery["first_verified_completion_seconds"] = recovery.get("elapsed_seconds")
    violations = scope_violations(project, baseline_head) if baseline_head else []
    diff = diff_evidence(project, baseline_head) if baseline_head else {
        "changed_files": [], "tracked_patch_sha256": None, "untracked_sha256": {},
    }
    result["recovery"] = {
        key: value for key, value in recovery.items() if key not in ("stdout", "stderr")
    }
    result["recovery"].update({
        "acceptance_passed": final_complete,
        "scope_violations": violations,
        "final_diff": diff,
        "within_budget": not bool(recovery.get("timed_out")),
        "primary_success": final_complete and not violations and not bool(recovery.get("timed_out")),
    })
    result["recovery_skipped"] = False
    return result


def _target_argv(prompt: str, model: str, effort: str, arm: str, skills) -> List[str]:
    adapter = registry.get("codex", ROOT)
    argv = adapter.exec_argv(prompt)
    pinned = ["--model", model, "-c", 'model_reasoning_effort="' + effort + '"']
    return argv[:-1] + pinned + codex_isolation_args(arm, skills) + argv[-1:]


def _preflight(prompt: str, project: Path, arm: str, skills, executable: str, model: str, effort: str) -> List[str]:
    expected = ["agent-handoff"] if arm == "handoff" else []
    rendered = subprocess.run(
        [executable, "debug", "prompt-input", "-c", "model=" + json.dumps(model),
         "-c", 'model_reasoning_effort="' + effort + '"',
         *codex_isolation_args(arm, skills), prompt],
        cwd=str(project), env=_git_safe_env(project), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, check=True,
    ).stdout
    actual = visible_user_skills(rendered, skills)
    if actual is None or actual != expected:
        raise RuntimeError("Codex Skill preflight mismatch: expected %r, got %r" % (expected, actual))
    return actual


def _fixture_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted((FIXTURE / "project").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(FIXTURE).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_safe_env(project: Path) -> Dict[str, str]:
    """Pass a per-process safe.directory exception without changing user config."""
    env = os.environ.copy()
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        raise RuntimeError("GIT_CONFIG_COUNT is not an integer")
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env["GIT_CONFIG_KEY_" + str(count)] = "safe.directory"
    env["GIT_CONFIG_VALUE_" + str(count)] = str(Path(project).resolve())
    return env


def build_plan(
    seed: int, model: str, cli_version: str, skill: Dict[str, str],
    effort: str = "medium", replicate: str = "r01",
) -> Dict[str, object]:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(replicate, str) or re.fullmatch(r"r[0-9]{2,}", replicate) is None:
        raise ValueError("replicate must look like r01")
    if not model.strip() or not cli_version.strip() or effort != "medium":
        raise ValueError("pin a model and CLI version; Task D effort is medium")
    if skill.get("name") != "agent-handoff" or not skill.get("path") or not skill.get("sha256"):
        raise ValueError("a pinned agent-handoff Skill path and hash are required")
    arms = list(ARMS)
    random.Random("task-d-v05|%d" % seed).shuffle(arms)
    data = {
        "schema_version": 2,
        "interruption": dict(INTERRUPTION_POLICY),
        "pair_id": "D-" + replicate,
        "replicate": replicate,
        "seed": seed,
        "model": model.strip(),
        "effort": effort.strip(),
        "target": "codex",
        "cli_version": cli_version.strip(),
        "source_budget_seconds": SOURCE_SECONDS,
        "recovery_budget_seconds": RECOVERY_SECONDS,
        "fixture_sha256": _fixture_digest(),
        "prompt_sha256": _sha256(FIXTURE / "prompt.md"),
        "evaluator_sha256": {
            name: _sha256(FIXTURE / (name + ".py")) for name in ("progress", "acceptance")
        },
        "skill": dict(skill),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows": [
            {"trial_id": "D-%s-%s" % (arm, replicate), "arm": arm, "position": i, "state": "pending"}
            for i, arm in enumerate(arms, 1)
        ],
    }
    data["manifest_sha256"] = _plan_manifest_hash(data)
    return data


def _plan_manifest_hash(data: Dict[str, object]) -> str:
    rows = [
        {key: row[key] for key in ("trial_id", "arm", "position")}
        for row in data.get("rows", [])
    ]
    immutable = {
        key: data.get(key) for key in (
            "schema_version", "pair_id", "seed", "model", "effort", "target",
            "cli_version", "source_budget_seconds", "recovery_budget_seconds",
            "fixture_sha256", "prompt_sha256", "evaluator_sha256", "skill", "created_at",
        )
    }
    for optional in ("replicate", "interruption"):
        if optional in data:
            immutable[optional] = data[optional]
    immutable["rows"] = rows
    encoded = json.dumps(immutable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def register_plan(
    path: Path, seed: int, model: str, cli_version: str, effort: str = "medium",
    replicate: str = "r01",
) -> Dict[str, object]:
    if path.exists():
        raise FileExistsError("pilot plan already exists: " + str(path))
    handoffs = [item for item in _skill_catalog(_codex_home()) if item[1] == "agent-handoff"]
    if len(handoffs) != 1:
        raise ValueError("the installed agent-handoff Skill is required to register this treatment")
    handoff = handoffs[0]
    data = build_plan(seed, model, cli_version, {
        "path": str(handoff[0]), "name": handoff[1], "sha256": _sha256(handoff[0]),
    }, effort, replicate)
    for candidate in Path(path).parent.glob("*.json"):
        if candidate.resolve() == Path(path).resolve():
            continue
        try:
            existing = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if existing.get("pair_id") == data["pair_id"]:
            raise FileExistsError("replicate already has a registered plan: " + str(candidate))
    for row in data["rows"]:
        trial_id = str(row["trial_id"])
        if any((Path(path).parent / (trial_id + suffix)).exists()
               for suffix in (".json", ".failure.json", ".patch")):
            raise FileExistsError("trial artifacts already exist for " + trial_id)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def _read_plan(path: Path) -> Dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    replicate = data.get("replicate")
    if replicate is None and data.get("pair_id") == "D-r01":
        replicate = "r01"  # v0.4 plans predate the explicit replicate field.
    if data.get("schema_version") == 2 and data.get("interruption") != INTERRUPTION_POLICY:
        raise ValueError("pilot plan interruption policy differs from the frozen protocol")
    if data.get("schema_version") == 1 and "interruption" in data:
        raise ValueError("a schema-1 pilot plan cannot carry an interruption policy")
    if (
        data.get("schema_version") not in (1, 2)
        or not isinstance(replicate, str)
        or re.fullmatch(r"r[0-9]{2,}", replicate) is None
        or data.get("pair_id") != "D-" + replicate
    ):
        raise ValueError("unsupported recovery pilot plan")
    rows = data.get("rows")
    if (not isinstance(rows, list) or len(rows) != 2
            or [row.get("arm") for row in rows] not in (list(ARMS), list(reversed(ARMS)))):
        raise ValueError("pilot plan must contain exactly one row per arm")
    if any(
        row.get("trial_id") != "D-%s-%s" % (row.get("arm"), replicate)
        or row.get("position") != index
        for index, row in enumerate(rows, 1)
    ):
        raise ValueError("pilot row identifiers do not match the registered replicate")
    if any(row.get("state") not in ("pending", "running", "recorded", "invalid") for row in rows):
        raise ValueError("pilot plan contains an unknown row state")
    if not data.get("model") or not data.get("cli_version") or data.get("target") != "codex":
        raise ValueError("pilot plan lacks pinned Codex configuration")
    if data.get("source_budget_seconds") != SOURCE_SECONDS or data.get("recovery_budget_seconds") != RECOVERY_SECONDS:
        raise ValueError("pilot plan budgets differ from the frozen protocol")
    if data.get("manifest_sha256") != _plan_manifest_hash(data):
        raise ValueError("pilot plan immutable manifest hash does not match")
    return data


def _save_plan(path: Path, data: Dict[str, object]) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def validate_record(record: Dict[str, object]) -> List[str]:
    problems = []
    if record.get("schema_version") != 1 or record.get("arm") not in ARMS:
        problems.append("unsupported trial record schema or arm")
    result = record.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("source"), dict):
        return problems + ["missing source phase result"]
    if not isinstance(result.get("interruption"), dict):
        problems.append("missing interruption-boundary evidence")
    preflights = record.get("preflights")
    expected_skill = ["agent-handoff"] if record.get("arm") == "handoff" else []
    if not isinstance(preflights, list) or not preflights:
        problems.append("missing phase Skill preflight")
    elif any(item.get("visible_user_skills") != expected_skill for item in preflights):
        problems.append("Skill preflight does not match the assigned arm")
    recovery = result.get("recovery")
    if result.get("recovery_skipped"):
        if not result.get("interruption", {}).get("completion_passed") or recovery is not None:
            problems.append("recovery may be skipped only when completion passed before interruption")
    elif not isinstance(recovery, dict):
        problems.append("missing fresh recovery result")
    elif recovery.get("primary_success") and (
        not recovery.get("acceptance_passed") or recovery.get("scope_violations")
        or not recovery.get("within_budget")
    ):
        problems.append("primary success conflicts with acceptance, scope, or budget evidence")
    if set(record.get("evaluator_sha256", {})) != {"progress", "acceptance"}:
        problems.append("missing evaluator hashes")
    interruption = result.get("interruption") if isinstance(result.get("interruption"), dict) else {}
    # The harness renders a package only as the Handoff arm's treatment; one
    # appearing for the Baseline arm would be the harness handing it the help
    # the comparison is about.
    if result.get("invalid_reason") and isinstance(recovery, dict) and recovery.get("primary_success"):
        problems.append("an invalid trial cannot be primary success")
    if record.get("arm") == "baseline" and interruption.get("pack") is not None:
        problems.append("the baseline arm must not receive a harness-rendered package")
    if interruption.get("termination_reason") == "progress_trigger" and interruption.get("completion_passed"):
        problems.append("a session stopped by the progress trigger cannot have completed first")
    if record.get("contamination") and any(record["contamination"].values()):
        if recovery is not None and recovery.get("primary_success"):
            problems.append("contaminated trial cannot be primary success")
    return problems


def claim_next(path: Path) -> Dict[str, object]:
    """Claim the next row; leave an auditable running claim if this process dies."""
    path = Path(path)
    with Lock(path.with_name(path.name + ".lock"), command="recovery pilot claim"):
        data = _read_plan(path)
        running = next((row for row in data["rows"] if row.get("state") == "running"), None)
        if running:
            claim = running.get("claimed_by", {})
            raise RuntimeError("unresolved running row %s (pid %s); inspect before resolving" % (
                running["trial_id"], claim.get("pid", "unknown"),
            ))
        row = next((row for row in data["rows"] if row.get("state") == "pending"), None)
        if row is None:
            raise RuntimeError("pilot plan has no pending rows")
        if any((path.parent / (str(row["trial_id"]) + suffix)).exists()
               for suffix in (".json", ".failure.json", ".patch")):
            raise FileExistsError("trial artifacts already exist for " + str(row["trial_id"]))
        row["state"] = "running"
        row["claimed_by"] = {
            "pid": os.getpid(), "host": socket.gethostname(),
            "claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _save_plan(path, data)
        return copy_row(row)


def copy_row(row: Dict[str, object]) -> Dict[str, object]:
    return json.loads(json.dumps(row))


def finish_row(path: Path, trial_id: str, state: str) -> None:
    if state not in ("recorded", "invalid"):
        raise ValueError("final row state must be recorded or invalid")
    with Lock(Path(path).with_name(Path(path).name + ".lock"), command="recovery pilot finish"):
        data = _read_plan(path)
        row = next((item for item in data["rows"] if item.get("trial_id") == trial_id), None)
        if row is None or row.get("state") != "running":
            raise RuntimeError("no running row " + trial_id)
        row["state"] = state
        row.pop("claimed_by", None)
        _save_plan(path, data)


def set_active_child(path: Path, trial_id: str, pid: Optional[int]) -> None:
    with Lock(Path(path).with_name(Path(path).name + ".lock"), command="recovery pilot child process"):
        data = _read_plan(path)
        row = next((item for item in data["rows"] if item.get("trial_id") == trial_id), None)
        if row is None or row.get("state") != "running":
            raise RuntimeError("no running row " + trial_id)
        claim = row.setdefault("claimed_by", {})
        if pid is None:
            claim.pop("active_child_pid", None)
        else:
            claim["active_child_pid"] = int(pid)
            claim["child_started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _save_plan(path, data)


def invalidate_abandoned(path: Path, trial_id: str, *, alive: Callable[[int], bool] = process_alive) -> None:
    """Mark a dead local claim invalid; never silently recycle a possibly spent row."""
    with Lock(Path(path).with_name(Path(path).name + ".lock"), command="resolve abandoned recovery pilot"):
        data = _read_plan(path)
        row = next((item for item in data["rows"] if item.get("trial_id") == trial_id), None)
        if row is None or row.get("state") != "running":
            raise RuntimeError("no running row " + trial_id)
        claim = row.get("claimed_by", {})
        if claim.get("host") != socket.gethostname():
            raise RuntimeError("cannot establish liveness for a claim from another host")
        pid = int(claim.get("pid", 0))
        if alive(pid):
            raise RuntimeError("claiming process is still alive")
        child_pid = int(claim.get("active_child_pid", 0) or 0)
        if child_pid and alive(child_pid):
            raise RuntimeError("the claimed Codex child is still alive")
        row["state"] = "invalid"
        row["resolution"] = "claiming process no longer exists; row not rerun"
        row.pop("claimed_by", None)
        _save_plan(path, data)


def _initialize_project(project: Path) -> str:
    shutil.copytree(FIXTURE / "project", project)
    env = _git_safe_env(project)
    subprocess.run(["git", "init", "--quiet"], cwd=str(project), env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=str(project), env=env, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Recovery Pilot Fixture", "-c",
         "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture baseline"],
        cwd=str(project), env=env, check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project), env=env, capture_output=True,
        text=True, check=True,
    ).stdout.strip()


def _run_live_row(plan_path: Path, row: Dict[str, object], data: Dict[str, object], executable: str) -> Dict[str, object]:
    arm = str(row["arm"])
    skills = _skill_catalog(_codex_home())
    expected_skill = data["skill"]
    handoffs = [item for item in skills if item[1] == "agent-handoff"]
    handoff = handoffs[0] if len(handoffs) == 1 else None
    if handoff is None or _sha256(handoff[0]) != expected_skill["sha256"]:
        raise RuntimeError("installed agent-handoff Skill differs from the pre-registered hash")
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, check=True,
    ).stdout.strip()
    if version != data["cli_version"]:
        raise RuntimeError("Codex CLI version mismatch: expected %r, got %r" % (data["cli_version"], version))
    calibration = calibrate()
    if not calibration["calibration_passed"] or calibration["evaluator_sha256"] != data["evaluator_sha256"]:
        raise RuntimeError("fixture/evaluator calibration or hashes no longer match the plan")
    if _fixture_digest() != data["fixture_sha256"] or _sha256(FIXTURE / "prompt.md") != data["prompt_sha256"]:
        raise RuntimeError("fixture or prompt differs from the pre-registered plan")

    # Not TemporaryDirectory: on Windows a directory is locked while any
    # process sits in it, so a descendant the stop could not reach would make
    # the cleanup raise after the record was written, and the row would be
    # failed over a temporary folder.
    temp = tempfile.mkdtemp(prefix="recovery-pilot-")
    try:
        workspace = Path(temp) / "project"
        baseline_head = _initialize_project(workspace)
        prompt = (FIXTURE / "prompt.md").read_text(encoding="utf-8")
        preflights = []
        logs = {}
        contamination = {}
        policy = interruption_policy(data)
        triggered = policy["trigger"] == "first_progress"

        def invoke(phase: str, project: Path, request: str, budget: int) -> Dict[str, object]:
            visible = _preflight(
                request, project, arm, skills, executable,
                str(data["model"]), str(data["effort"]),
            )
            preflights.append({"phase": phase, "visible_user_skills": visible})
            result = run_bounded_process(
                _target_argv(request, str(data["model"]), str(data["effort"]), arm, skills),
                project, budget, env=_git_safe_env(project),
                progress_probe=lambda: _run_check(project, "progress"),
                completion_probe=lambda: _run_check(project, "acceptance"),
                probe_interval=float(policy["probe_seconds"]),
                # Only the source session is interrupted; recovery runs to
                # acceptance or its own budget.
                stop_after_progress=(
                    float(policy["grace_seconds"]) if triggered and phase == "source" else None
                ),
                on_start=lambda pid: set_active_child(plan_path, str(row["trial_id"]), pid),
                on_finish=lambda: set_active_child(plan_path, str(row["trial_id"]), None),
            )
            stdout = str(result.pop("stdout"))
            stderr = str(result.pop("stderr"))
            logs[phase] = {
                "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
                "stdout_chars": len(stdout),
                "stderr_chars": len(stderr),
            }
            contamination[phase] = contamination_findings(arm, project, stdout, stderr)
            return result

        result = execute_arm(
            workspace, prompt, invoke, baseline_head=baseline_head,
            pack=pack_at_interruption if arm == "handoff" and policy["pack_handoff_arm"] else None,
        )
        record = {
            "schema_version": 1,
            "interruption_policy": policy,
            "trial_id": row["trial_id"],
            "arm": arm,
            "position": row["position"],
            "model": data["model"],
            "effort": data["effort"],
            "target": "codex",
            "cli_version": version,
            "budgets_seconds": {"source": SOURCE_SECONDS, "recovery": RECOVERY_SECONDS},
            "skill_sha256": expected_skill["sha256"] if arm == "handoff" else None,
            "fixture_sha256": data["fixture_sha256"],
            "prompt_sha256": data["prompt_sha256"],
            "evaluator_sha256": data["evaluator_sha256"],
            "preflights": preflights,
            "contamination": contamination,
            "result": result,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        patch_text = result.get("recovery", {}).get("final_diff", {}).pop("tracked_patch", "") if result.get("recovery") else ""
        destination = Path(plan_path.parent) / (str(row["trial_id"]) + ".json")
        if destination.exists():
            raise FileExistsError("trial record already exists: " + str(destination))
        if patch_text:
            (Path(plan_path.parent) / (str(row["trial_id"]) + ".patch")).write_text(patch_text, encoding="utf-8")
        record["log_digests"] = logs
        if any(contamination.values()):
            record["result"]["contaminated"] = True
            recovery_result = record["result"].get("recovery")
            if recovery_result is not None:
                recovery_result["primary_success"] = False
        phases = (record["result"].get("source") or {}, record["result"].get("recovery") or {})
        if any(phase.get("process_tree_terminated") is False for phase in phases):
            # Something a session started outlived the stop. It may have kept
            # changing the project after the interruption, so the state
            # recovery began from is not the state the boundary recorded.
            record["result"]["invalid_reason"] = "process_tree_not_terminated"
            recovery_result = record["result"].get("recovery")
            if recovery_result is not None:
                recovery_result["primary_success"] = False
        problems = validate_record(record)
        if problems:
            raise ValueError("trial record failed validation: " + "; ".join(problems))
        atomic_write(destination, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return record
    finally:
        try:
            _remove_tree(Path(temp))
        except OSError as exc:
            print("warning: left pilot workspace %s in place (%s)" % (temp, exc), file=sys.stderr)


def launch_next(path: Path) -> Dict[str, object]:
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not on PATH")
    row = claim_next(path)
    data = _read_plan(path)
    try:
        record = _run_live_row(Path(path), row, data, executable)
    except Exception as exc:
        failure = {
            "trial_id": row["trial_id"], "arm": row["arm"],
            "error_type": type(exc).__name__, "error": str(exc),
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        failure_path = Path(path).parent / (str(row["trial_id"]) + ".failure.json")
        atomic_write(failure_path, json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
        finish_row(path, str(row["trial_id"]), "invalid")
        raise
    finish_row(path, str(row["trial_id"]), "invalid" if record["result"].get("invalid_reason") else "recorded")
    return record


def _run(project: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script, *args], cwd=str(project), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=10,
    )


def _install_variant(project: Path, variant: str) -> None:
    shutil.copytree(FIXTURE / variant, project, dirs_exist_ok=True)
    for shared in ("csv_export.py", "json_export.py"):
        if not (project / shared).exists():
            shutil.copy2(FIXTURE / "project" / shared, project / shared)
    public_tests = FIXTURE / "project" / "test_public.py"
    if not (project / "test_public.py").exists():
        shutil.copy2(public_tests, project / "test_public.py")


def _check(project: Path, evaluator: str) -> bool:
    evaluator_path = str(FIXTURE / (evaluator + ".py"))
    result = _run(
        project,
        "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1], run_name='__main__')",
        evaluator_path,
    )
    return result.returncode == 0


def calibrate() -> Dict[str, object]:
    """Prove evaluator sensitivity using only disposable local copies."""
    report: Dict[str, object] = {
        "live_agents_launched": False,
        "evaluator_sha256": {
            name: hashlib.sha256((FIXTURE / (name + ".py")).read_bytes()).hexdigest()
            for name in ("progress", "acceptance")
        },
        "variants": {},
    }
    with tempfile.TemporaryDirectory(prefix="handoff-task-d-calibration-") as temp:
        base = Path(temp)
        for variant in ("project", "partial", "reference"):
            project = base / variant
            _install_variant(project, variant)
            progress = _check(project, "progress")
            acceptance = _check(project, "acceptance")
            tests = _run(project, "-m", "unittest", "-v")
            report["variants"][variant] = {
                "progress": progress,
                "acceptance": acceptance,
                "public_tests": tests.returncode == 0,
                "test_output_tail": (tests.stdout + tests.stderr)[-1200:],
            }
        variants = report["variants"]
        # Evaluator sensitivity is not enough: the reference and the partial
        # state must also survive the scope audit a real trial is held to.
        # Without this, v0.5 calibrated cleanly while every correct solution
        # would have been scored out of scope.
        for variant in ("partial", "reference"):
            audited = base / ("audit-" + variant)
            head = _initialize_project(audited)
            _install_variant(audited, variant)
            variants[variant]["scope_violations"] = scope_violations(audited, head)
        report["calibration_passed"] = bool(
            not variants["project"]["progress"]
            and not variants["project"]["acceptance"]
            and variants["partial"]["progress"]
            and not variants["partial"]["acceptance"]
            and not variants["partial"]["scope_violations"]
            and variants["reference"]["progress"]
            and variants["reference"]["acceptance"]
            and variants["reference"]["public_tests"]
            and not variants["reference"]["scope_violations"]
        )
    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibrate", action="store_true", help="run offline fixture/evaluator calibration")
    parser.add_argument("--register-plan", type=Path, help="pre-register one randomized Baseline/Handoff pair")
    parser.add_argument("--plan", type=Path, help="inspect or advance a registered pair")
    parser.add_argument("--seed", type=int, help="randomization seed for --register-plan")
    parser.add_argument("--replicate", default="r01", help="unique replicate identifier (for example r02)")
    parser.add_argument("--model", help="exact pinned Codex model name for plan registration")
    parser.add_argument("--cli-version", help="exact output of `codex --version` for plan registration")
    parser.add_argument("--launch", action="store_true", help="launch exactly the next pending arm; consumes model quota")
    parser.add_argument("--status", action="store_true", help="show plan rows without launching")
    parser.add_argument("--resolve-abandoned", metavar="TRIAL_ID", help="mark a dead local running row invalid; never rerun it")
    args = parser.parse_args(argv)
    if args.calibrate:
        if args.launch or args.plan or args.register_plan:
            parser.error("--calibrate cannot be combined with plan or launch actions")
        report = calibrate()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["calibration_passed"] else 1
    if args.register_plan:
        if args.launch or args.plan or args.status or args.resolve_abandoned:
            parser.error("--register-plan is a separate, no-launch action")
        if args.seed is None or not args.model or not args.cli_version:
            parser.error("--register-plan requires --seed, --model, and --cli-version")
        try:
            plan = register_plan(
                args.register_plan, args.seed, args.model, args.cli_version,
                replicate=args.replicate,
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print("registered %s with order %s" % (plan["pair_id"], " -> ".join(row["arm"] for row in plan["rows"])))
        return 0
    if args.plan:
        try:
            if args.launch and (args.status or args.resolve_abandoned):
                parser.error("--launch cannot be combined with --status or --resolve-abandoned")
            if args.status and args.resolve_abandoned:
                parser.error("--status cannot be combined with --resolve-abandoned")
            if args.resolve_abandoned:
                invalidate_abandoned(args.plan, args.resolve_abandoned)
                print("marked abandoned row invalid; it will not be rerun")
                return 0
            if args.launch:
                record = launch_next(args.plan)
                print("recorded " + str(record["trial_id"]))
                return 0
            if not args.status:
                parser.error("use --status to inspect a plan or --launch to start the next arm")
            plan = _read_plan(args.plan)
            print("# %s seed=%s model=%s CLI=%s" % (
                plan["pair_id"], plan["seed"], plan["model"], plan["cli_version"],
            ))
            for row in plan["rows"]:
                print("%s  %-8s  %s" % (row["state"], row["arm"], row["trial_id"]))
            return 0
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            parser.error(str(exc))
    parser.error("select --calibrate, --register-plan, or --plan")


if __name__ == "__main__":
    raise SystemExit(main())
