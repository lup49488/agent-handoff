"""Run one benchmark trial. Launching a target agent is always opt-in.

The runner measures a target in a disposable fixture.  Its evaluator source
comes from the immutable fixture definition rather than project/acceptance.py,
so a target cannot make a trial pass by changing its in-worktree checker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent_handoff import __version__, gitinfo  # noqa: E402
from agent_handoff.adapters import registry  # noqa: E402
from agent_handoff.runner import _kill_process_tree  # noqa: E402
from benchmarks import plan as plan_mod  # noqa: E402
from benchmarks.plan import arm_order  # noqa: E402
from benchmarks.prepare_trial import ARMS, SNAPSHOTS, TASKS, prepare, snapshot_files  # noqa: E402
from benchmarks.validate_trial import validate  # noqa: E402

TARGETS = ("codex", "claude-code")
PROBE_SECONDS = 5.0
HANDOFF_INSTRUCTION = "\n\nOpen and follow HANDOFF.md before making changes."

# The fixtures pre-register the only source paths a target may change.  The
# acceptance script itself is deliberately excluded: it is evaluator input,
# not a solution artifact.
#
# Every file a fixture's own snapshots change must be listed. Task B once
# allowed only `api.py`, while its source agent had already edited
# `service.py` and its package told the target to sort there: B-60 and B-80
# were disqualified before any target ran, and the Handoff arm was penalised
# for following the package it is being measured on.
ALLOWED_PATHS = {
    "A": {"slug.py"},
    "B": {"api.py", "service.py"},
    "C": {"normalization.py", "report.py", "cli.py"},
}


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _isolation_args(target: str) -> List[str]:
    """Switch off what the target would otherwise bring from its user's setup.

    The v0.3 cohort ran Codex with the developer's own configuration: a
    globally installed agent-handoff skill told every Baseline agent to start
    handoff tracking itself, and persistent memories described the
    Baseline/Handoff design to the agents being measured. Neither arm was the
    condition it claimed to be.

    For Codex, memories are switched off and every user-installed skill is
    disabled for this one invocation; the user's configuration on disk is not
    touched. Skills Codex ships in `skills/.system` stay, since they are part
    of the agent as released. For Claude Code, `--disable-slash-commands`
    disables all skills; its user memory is not switched off here, which the
    contamination scan exists to catch.
    """
    if target != "codex":
        # Stream JSON is what makes the contamination scan possible here: plain
        # print mode logs only the final answer, so a skill read or a `handoff`
        # command would leave no trace. Its first event also lists the skills
        # the session loaded, which is checked after the run.
        return ["--disable-slash-commands", "--output-format", "stream-json", "--verbose"]
    skills_dir = _codex_home() / "skills"
    skills = sorted(
        path / "SKILL.md"
        for path in (skills_dir.iterdir() if skills_dir.is_dir() else ())
        if path.is_dir() and not path.name.startswith(".")
    )
    # TOML literal strings: a Windows path needs no escaping inside '...'.
    disabled = ",".join("{path='%s',enabled=false}" % path for path in skills)
    return [
        "-c", "features.memories=false",
        "-c", "memories.use_memories=false",
        "-c", "memories.generate_memories=false",
        "-c", "skills.config=[%s]" % disabled,
    ]


def _user_skill_signatures() -> Optional[List[Tuple[str, str]]]:
    """Return user skill names and descriptions, or None if metadata is unreadable."""
    skills_dir = _codex_home() / "skills"
    if not skills_dir.is_dir():
        return []
    signatures = []
    for directory in sorted(skills_dir.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            source = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        frontmatter = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", source, re.DOTALL)
        if frontmatter is None:
            return None
        fields = _skill_frontmatter_fields(frontmatter.group(1))
        name, description = fields.get("name"), fields.get("description")
        if not name or not description:
            return None
        signatures.append((name, description))
    return signatures


def _skill_frontmatter_fields(frontmatter: str) -> Dict[str, str]:
    """Read the two scalar YAML fields Codex uses to list a skill."""
    lines = frontmatter.splitlines()
    fields: Dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = re.match(r"^(name|description):\s*(.*?)\s*$", lines[index])
        if match is None:
            index += 1
            continue
        key, value = match.groups()
        if value in ("|", ">", "|-", ">-"):
            parts = []
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index][0].isspace()):
                if lines[index].strip():
                    parts.append(lines[index].strip())
                index += 1
            fields[key] = " ".join(parts)
            continue
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("''", "'")
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        fields[key] = value
        index += 1
    return fields


def _preflight_verdict(rendered: str, skill_signatures: Optional[Sequence[Tuple[str, str]]]) -> str:
    """Check for a disabled skill's exact name/description catalogue entry.

    The debug command includes unrelated session context, so a bare name match
    is not evidence that Codex made a skill available to the target.
    """
    if skill_signatures is None:
        return "unavailable"
    try:
        messages = json.loads(rendered)
    except (TypeError, json.JSONDecodeError):
        return "unavailable"
    if not isinstance(messages, list):
        return "unavailable"

    developer_text = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "developer":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            return "unavailable"
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "input_text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                return "unavailable"
            developer_text.append(" ".join(text.casefold().split()))

    if skill_signatures and not developer_text:
        return "unavailable"
    rendered_developer = " ".join(developer_text)
    for name, description in skill_signatures:
        name = " ".join(name.casefold().split())
        description = " ".join(description.casefold().split())
        if name and description and name in rendered_developer and description in rendered_developer:
            return "failed"
    return "passed"


def _preflight(target: str, project: Path) -> str:
    """Render what the model will be given, before spending anything on it.

    `codex debug prompt-input` builds the model-visible input without calling
    a model. A user skill that survives the isolation arguments shows up
    there, so a broken override is caught before a launch rather than in a
    finished cohort. `unavailable` when this Codex has no such command; the
    contamination scan after the run still applies.
    """
    executable = shutil.which("codex") if target == "codex" else None
    if executable is None:
        return "unavailable"
    try:
        rendered = subprocess.run(
            [executable, "debug", "prompt-input", *_isolation_args(target), "preflight"],
            cwd=str(project), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return _preflight_verdict(rendered, _user_skill_signatures())


def _claude_allowed_tools() -> List[str]:
    """The commands a Claude Code target may run, and nothing wider.

    Print mode with `acceptEdits` refuses every shell command, so a Claude
    target could not run the acceptance check it was told to pass, nor the
    `handoff checkpoint` the package tells it to run, while Codex could do
    both. This admits exactly what the tasks need: the fixture's check,
    read-only Git, and the handoff subcommands that only record. `python`
    in general is not admitted, since that is arbitrary code; nor are
    `handoff run` or `switch`, which launch other agents. Verified against
    Claude Code 2.1.287 by a probe: the listed commands ran, `python -c` and
    `handoff run` were refused. Both arms get the same list, so the arms
    still differ only by the package.
    """
    commands = ["python acceptance.py", "git status:*", "git diff:*", "git log:*", "handoff --version"]
    commands += ["handoff %s:*" % sub for sub in ("status", "verify", "checkpoint", "tests", "pack")]
    # Claude Code on Windows has a PowerShell tool beside Bash; the same rules
    # cover both. Only the Bash rules were exercised by the probe.
    return ["%s(%s)" % (tool, command) for tool in ("Bash", "PowerShell") for command in commands]


def _target_argv(adapter, prompt: str, target: str, model: str, effort: str) -> list:
    argv = adapter.exec_argv(prompt)
    if target == "codex":
        pinned = ["--model", model, "-c", 'model_reasoning_effort="' + effort + '"']
        return argv[:-1] + pinned + _isolation_args(target) + argv[-1:]
    pinned = ["--model", model, "--effort", effort]
    # `--allowedTools` takes every following word until the next option, so it
    # goes before `--model`; placed last it would swallow the prompt.
    allowed = ["--allowedTools", *_claude_allowed_tools()]
    return argv[:-1] + allowed + pinned + _isolation_args(target) + argv[-1:]


#: What a target's logs show when something outside the arm's condition
#: reached it. The Handoff arm is supposed to use the `handoff` CLI and read
#: the package; the Baseline arm is not, and neither arm should read the
#: handoff skill or the agent's persistent memories.
_SKILL_READ = re.compile(r"skills[\\/]+agent-handoff", re.IGNORECASE)
# Codex keeps memories in `memories/*.md`; Claude Code in
# `projects/<working directory>/memory/*.md`.
_MEMORY_READ = re.compile(
    r"memories[\\/]+[\w.-]*\.md|projects[\\/]+[^\\/\"']+[\\/]+memory[\\/]+[\w.-]*\.md",
    re.IGNORECASE,
)
_HANDOFF_COMMAND = re.compile(
    r"(?:^|[\s;'\"&|(])(?:handoff(?:\.exe)?|-m\s+agent_handoff)\s+"
    r"(?:--version|init|status|verify|checkpoint|tests|pack|snapshot|event|command|"
    r"switch|run|recover|protocol|sessions|health|agents|config)\b",
    re.MULTILINE,
)


def _skills_loaded(stdout: str) -> List[str]:
    """Skills a Claude Code session reported loading, from its stream-JSON log.

    Claude Code has no way to render a session's input without a model call,
    so the check that Codex makes before launching is made here afterwards,
    from the session's own first event.
    """
    for line in (stdout or "").splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            return [str(skill) for skill in event.get("skills") or []]
    return []


def _contamination(arm: str, project: Path, stdout: str, stderr: str) -> List[str]:
    """Reasons this trial's target was not in the condition its arm names."""
    text = (stdout or "") + "\n" + (stderr or "")
    found = []
    loaded = _skills_loaded(stdout)
    if loaded:
        found.append("loaded skills: " + ", ".join(loaded[:5]) + ("…" if len(loaded) > 5 else ""))
    if _SKILL_READ.search(text):
        found.append("read the agent-handoff skill")
    if _MEMORY_READ.search(text):
        found.append("read the agent's persistent memories")
    if arm == "baseline":
        if _HANDOFF_COMMAND.search(text):
            found.append("ran the handoff CLI")
        if (project / ".agent-handoff").exists() or (project / "HANDOFF.md").exists():
            found.append("created a handoff package")
    return found


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fixture_metadata(task: str, snapshot: str) -> Dict[str, str]:
    files = snapshot_files(task, snapshot)
    acceptance = files["acceptance.py"]
    packed = "".join(name + "\0" + text + "\0" for name, text in sorted(files.items()))
    progress_evaluator = _progress_source(task, snapshot)
    return {
        "task": task,
        "snapshot": snapshot,
        "snapshot_sha256": _sha256(packed),
        "evaluator_sha256": _sha256(acceptance),
        "progress_evaluator_sha256": _sha256(progress_evaluator),
    }


def _progress_source(task: str, snapshot: str) -> str:
    if task == "C":
        from benchmarks.fixtures.task_c import PROGRESS

        return PROGRESS
    path = ROOT / "benchmarks" / "fixtures" / ("task-" + task.lower()) / "progress.py"
    return path.read_text(encoding="utf-8")


def _evaluate(
    project: Path,
    task: str,
    snapshot: str,
    *,
    check: str = "completion",
    persist_to: Optional[Path] = None,
) -> Tuple[bool, str, str]:
    """Run one immutable fixture check with the project as its import root.

    ``python -c`` preserves the task workspace on sys.path while the source is
    fetched from benchmarks/fixtures rather than the agent-controlled project.
    """
    if check == "completion":
        source = snapshot_files(task, snapshot)["acceptance.py"]
    elif check == "progress":
        source = _progress_source(task, snapshot)
    else:
        raise ValueError("check must be progress or completion")
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if persist_to is not None and check == "completion":
        (persist_to / "acceptance.stdout.log").write_text(result.stdout, encoding="utf-8")
        (persist_to / "acceptance.stderr.log").write_text(result.stderr, encoding="utf-8")
    return result.returncode == 0, result.stdout, result.stderr


def _git_head(project: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(project), capture_output=True,
        text=True, check=True,
    ).stdout.strip()


def _scope_violations(project: Path, task: str, baseline_head: str) -> list[str]:
    """Return paths outside scope relative to the immutable trial baseline.

    Comparing to the saved baseline also includes target-created commits, so a
    target cannot hide a modified evaluator helper merely by committing it.
    """
    changed = subprocess.run(
        ["git", "diff", "--name-only", baseline_head],
        cwd=str(project), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(project), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return sorted({path for path in changed + untracked if path and path not in ALLOWED_PATHS[task]})


# The cohort plan's state machine lives with the plan file, where the command
# that releases an abandoned row can reach it too. These names are what the
# runner and its tests have always called.
_read_cohort_plan = plan_mod.read_cohort_plan
_claim_cohort_trial = plan_mod.claim_trial
_finish_cohort_trial = plan_mod.finish_trial
_release_cohort_trial = plan_mod.release_trial


class _MetricProbe:
    """Independently record first task progress and full completion."""

    def __init__(
        self, project: Path, task: str, snapshot: str, started: float, *, progress_passed_at_start: bool = False
    ) -> None:
        self.project = project
        self.task = task
        self.snapshot = snapshot
        self.started = started
        self.first_progress: Optional[float] = 0.0 if progress_passed_at_start else None
        self.completion: Optional[float] = None
        # Which probe round saw each pass. Both checks run in the same round,
        # a fraction of a second apart, so two times from one round are one
        # observation and say nothing about progress before completion.
        self.progress_tick: Optional[int] = None
        self.completion_tick: Optional[int] = None
        self._tick = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            probe_started = time.monotonic()
            self._tick += 1
            if self.first_progress is None:
                passed, _, _ = _evaluate(self.project, self.task, self.snapshot, check="progress")
                if passed:
                    self.first_progress = round(time.monotonic() - self.started, 3)
                    self.progress_tick = self._tick
            if self.completion is None:
                passed, _, _ = _evaluate(self.project, self.task, self.snapshot)
                if passed:
                    self.completion = round(time.monotonic() - self.started, 3)
                    self.completion_tick = self._tick
            if self.completion is not None:
                return
            delay = max(0.0, PROBE_SECONDS - (time.monotonic() - probe_started))
            if self._stop.wait(delay):
                return

    def __enter__(self) -> "_MetricProbe":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        self._thread.join(timeout=PROBE_SECONDS * 2)

    @property
    def progress_separable(self) -> bool:
        """Progress was seen during the run, in an earlier round than completion.

        Passing at launch is not progress the target made, and a pass seen in
        the same round as completion cannot be told apart from it.
        """
        if self.progress_tick is None:
            return False
        return self.completion_tick is None or self.progress_tick < self.completion_tick


def _package_stayed_out_of_git(project: Path) -> bool:
    package_paths = {".agent-handoff", "HANDOFF.md"}

    def first_segments(output: str) -> set:
        return {line.split("/")[0] for line in output.splitlines() if line}

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout
    changed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(project), capture_output=True, text=True, check=True
    ).stdout
    seen = first_segments(tracked) | {line[3:].split("/")[0] for line in changed.splitlines() if line}
    return not (seen & package_paths)


def _planned_schedule(
    task: str,
    snapshot: str,
    arm: str,
    replicate: int,
    plan_seed: Optional[int],
    cohort_plan: Optional[Path],
) -> Optional[Dict[str, Any]]:
    """The arm order the plan pre-registered for this trial's cell.

    With a cohort plan the order is read from the plan's own rows, because a
    balanced plan does not follow `arm_order()` — recomputing it would record
    an order the cohort never ran. Only a bare `--plan-seed` falls back to the
    per-cell shuffle, which is all a seed alone can reproduce.
    """
    if cohort_plan is not None:
        data = _read_cohort_plan(cohort_plan)
        if plan_seed is not None and plan_seed != data["seed"]:
            raise ValueError("--plan-seed does not match --cohort-plan")
        cell = sorted(
            (row for row in data["rows"]
             if row["task"] == task and row["snapshot"] == snapshot and row["replicate"] == replicate),
            key=lambda row: row["position"],
        )
        arms = [row["arm"] for row in cell]
        if arm not in arms:
            raise ValueError("this trial is not in the cohort plan")
        return {"seed": data["seed"], "planned_arm_order": arms, "planned_position": arms.index(arm) + 1}
    if plan_seed is not None:
        arms = list(arm_order(task, snapshot, replicate, plan_seed))
        return {"seed": plan_seed, "planned_arm_order": arms, "planned_position": arms.index(arm) + 1}
    return None


def _remove_tree(path: Path) -> None:
    """Delete a trial workspace, including Git's read-only object files."""
    def make_writable(function, target, _info):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def _collect(source: Path, destination: Path, workspace: Path) -> None:
    """Move a finished trial from its neutral workspace to `destination`.

    Copied then removed rather than renamed: the temporary directory is often
    on another drive, and a rename across drives is a copy that then fails on
    Git's read-only objects.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    _remove_tree(workspace)


def run_trial(
    task: str,
    snapshot: str,
    arm: str,
    target: str,
    replicate: int,
    destination: Path,
    *,
    model: str = "unrecorded",
    target_version: str = "unrecorded",
    wall_seconds: int = 600,
    effort: str = "medium",
    launch: bool = False,
    plan_seed: Optional[int] = None,
    cohort_plan: Optional[Path] = None,
) -> Dict[str, Any]:
    """Prepare a trial, optionally launch one selected target, and record evidence."""
    if target not in TARGETS:
        raise ValueError("target must be one of: " + ", ".join(TARGETS))
    if replicate < 1 or wall_seconds < 1:
        raise ValueError("replicate and wall_seconds must be positive")
    if launch and (model == "unrecorded" or target_version == "unrecorded"):
        raise ValueError("--launch requires --model and --target-version")
    if cohort_plan is not None and not launch:
        raise ValueError("--cohort-plan requires --launch")

    destination = Path(destination).resolve()
    trial_id = "%s-%s-%s-r%02d" % (task, snapshot, arm, replicate)
    schedule = _planned_schedule(task, snapshot, arm, replicate, plan_seed, cohort_plan)

    workspace: Optional[Path] = None
    if launch:
        # A launched target works in a fresh temporary directory whose name
        # carries no task, arm or replicate, outside any repository. Its
        # working directory is in the context it reads, and a path like
        # `.trials/B-60-baseline-r06` tells it which arm it is in. The trial is
        # moved to `destination` once the target has finished.
        if destination.exists():
            raise FileExistsError("destination already exists: " + str(destination))
        workspace = Path(tempfile.mkdtemp())
        trial = prepare(task, snapshot, arm, workspace / "work")
    else:
        trial = prepare(task, snapshot, arm, destination)
    if not _package_stayed_out_of_git(trial.project):
        raise RuntimeError("the handoff package entered the trial repository")
    preflight = _preflight(target, trial.project) if launch else "not_run"
    if launch and target == "codex" and preflight != "passed":
        if workspace is not None:
            _remove_tree(workspace)
        raise RuntimeError(
            "isolation preflight did not prove user skills are disabled: " + preflight
        )

    baseline_head = _git_head(trial.project)
    record: Dict[str, Any] = {
        "schema_version": 4,
        "trial_id": trial_id,
        "task": task,
        "snapshot": snapshot,
        "arm": arm,
        "replicate": replicate,
        "source_commit": gitinfo.collect(ROOT).head or "unknown",
        "handoff_version": __version__,
        "target": {"agent": target, "version": target_version, "model": model, "effort": effort},
        "budget": {"wall_seconds": wall_seconds},
        "fixture": dict(_fixture_metadata(task, snapshot), initial_head=baseline_head),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "accepted": False,
        "completed": False,
        "budget_exceeded": False,
        "first_verified_progress_seconds": None,
        "completion_seconds": None,
        "initial_checks": None,
        "agent_exit_seconds": None,
        "target_exit_code": None,
        "target_turns": None,
        "provider_tokens": None,
        "scope_violations": [],
        "progress_separable": False,
        "isolation": {
            "workspace": "neutral-temporary" if launch else "destination",
            "agent_args": _isolation_args(target) if launch else [],
            "allowed_tools": _claude_allowed_tools() if launch and target == "claude-code" else [],
            "preflight": preflight,
        },
        "contamination": [],
        "invalid_reason": "not_launched",
    }
    if schedule is not None:
        record["schedule"] = schedule

    claimed = False
    try:
        if cohort_plan is not None:
            claimed_row = _claim_cohort_trial(cohort_plan, record["trial_id"])
            claimed = True
            record["schedule"]["cohort_plan"] = cohort_plan.name
            record["schedule"]["cohort_position"] = claimed_row["position"]
        if launch:
            record.update(_launch(trial, task, snapshot, target, arm, model, effort, wall_seconds, baseline_head))
        (trial.root / "trial.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except Exception:
        if claimed:
            # Our own claim: the liveness check exists to protect a row from
            # other processes, and this one is by definition alive.
            _release_cohort_trial(cohort_plan, record["trial_id"], force=True)
        raise
    finally:
        if workspace is not None:
            # Whatever happened, the evidence goes where the caller asked.
            _collect(trial.root, destination, workspace)
    if claimed:
        # An invalid trial still uses up its row; saying so keeps the cell's
        # shortfall visible instead of counting it towards the sample.
        final = "recorded" if record["invalid_reason"] is None else "invalid"
        _finish_cohort_trial(cohort_plan, record["trial_id"], final)
    return record


def _launch(trial, task: str, snapshot: str, target: str, arm: str, model: str, effort: str, wall_seconds: int, baseline_head: str) -> Dict[str, Any]:
    adapter = registry.get(target, trial.project)
    prompt = trial.prompt.read_text(encoding="utf-8")
    if arm == "handoff":
        prompt += HANDOFF_INSTRUCTION

    progress_passed_at_start = _evaluate(trial.project, task, snapshot, check="progress")[0]
    completion_passed_at_start = _evaluate(trial.project, task, snapshot)[0]
    if completion_passed_at_start:
        return {
            "initial_checks": {"progress_passed": progress_passed_at_start, "completion_passed": True},
            "invalid_reason": "fixture_already_complete",
        }

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _target_argv(adapter, prompt, target, model, effort),
            cwd=str(trial.project), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        (trial.root / "target.stderr.log").write_text(str(exc) + "\n", encoding="utf-8")
        return {
            "initial_checks": {"progress_passed": progress_passed_at_start, "completion_passed": False},
            "invalid_reason": "target_not_started",
        }

    exceeded = False
    with _MetricProbe(
        trial.project, task, snapshot, started, progress_passed_at_start=progress_passed_at_start
    ) as probe:
        try:
            stdout, stderr = process.communicate(timeout=wall_seconds)
        except subprocess.TimeoutExpired:
            exceeded = True
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)
    (trial.root / "target.stdout.log").write_text(stdout or "", encoding="utf-8")
    (trial.root / "target.stderr.log").write_text(stderr or "", encoding="utf-8")

    passed, _, _ = _evaluate(trial.project, task, snapshot, persist_to=trial.root)
    violations = _scope_violations(trial.project, task, baseline_head)
    contamination = _contamination(arm, trial.project, stdout, stderr)
    if contamination:
        # The target ran, but not in its arm's condition, so the trial is
        # excluded rather than counted. What it measured is kept for audit.
        return {
            "initial_checks": {"progress_passed": progress_passed_at_start, "completion_passed": False},
            "budget_exceeded": False,
            "first_verified_progress_seconds": probe.first_progress,
            "completion_seconds": probe.completion,
            "agent_exit_seconds": elapsed,
            "target_exit_code": process.returncode,
            "scope_violations": violations,
            "contamination": contamination,
            "invalid_reason": "contaminated",
        }
    if not exceeded and process.returncode != 0:
        return {
            "initial_checks": {"progress_passed": progress_passed_at_start, "completion_passed": False},
            "agent_exit_seconds": elapsed,
            "target_exit_code": process.returncode,
            "invalid_reason": "target_failed",
        }

    completed = passed and not violations and not exceeded
    first_progress = probe.first_progress
    completion = probe.completion
    if passed:
        if first_progress is None:
            first_progress = elapsed
        if completion is None:
            completion = elapsed
    return {
        "accepted": True,
        "completed": completed,
        "budget_exceeded": exceeded,
        "first_verified_progress_seconds": first_progress,
        "completion_seconds": completion,
        "agent_exit_seconds": elapsed,
        "target_exit_code": process.returncode,
        "initial_checks": {"progress_passed": progress_passed_at_start, "completion_passed": False},
        "scope_violations": violations,
        "progress_separable": probe.progress_separable,
        "invalid_reason": None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a trial; --launch starts one selected CLI and spends real quota.")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("snapshot", choices=SNAPSHOTS)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("replicate", type=int)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--model", default="unrecorded")
    parser.add_argument("--target-version", default="unrecorded")
    parser.add_argument("--wall-seconds", type=int, default=600)
    parser.add_argument("--effort", choices=("medium",), default="medium")
    parser.add_argument("--plan-seed", type=int, help="record the pre-registered arm-order seed")
    parser.add_argument("--cohort-plan", type=Path, help="claim only the next trial from this pre-registered plan")
    parser.add_argument("--launch", action="store_true", help="start the target agent")
    args = parser.parse_args(argv)
    try:
        record = run_trial(**vars(args))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print("recorded " + record["trial_id"] + ": " + (record["invalid_reason"] or "accepted"))
    problems = validate(record)
    for problem in problems:
        print("  invalid record: " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
