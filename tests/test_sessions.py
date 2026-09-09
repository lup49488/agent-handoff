"""Reading an agent's own session log.

Every fixture here is synthetic, shaped after records observed in a real
codex-cli 0.153.4 rollout. The suite never reads a real session file: those
belong to whoever is running the tests.
"""

import datetime as dt
import json

import pytest

from agent_handoff import health as health_mod
from agent_handoff import sessions
from agent_handoff.cli import main
from agent_handoff.models import Task
from agent_handoff.store import Store

#: The message a real Codex exhaustion produced, verbatim.
REAL_QUOTA_MESSAGE = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits "
    "or try again at Sep 8, 2026 at 3:10 AM."
)


def write_rollout(day_dir, name, cwd, *, message=None, resets_at=None, used=None):
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / name
    lines = [
        {
            "timestamp": "2026-09-08T05:19:31.646Z",
            "type": "session_meta",
            "payload": {"cwd": str(cwd), "cli_version": "0.153.4"},
        },
        # The bulk of a transcript: never parsed, and nothing here is read.
        {"type": "response_item", "payload": {"text": "a secret token: hunter2"}},
    ]
    if used is not None or resets_at is not None:
        primary = {"used_percent": used, "window_minutes": 300}
        if resets_at is not None:
            primary["resets_at"] = resets_at
        lines.append(
            {
                "timestamp": "2026-09-08T05:24:00.000Z",
                "type": "event_msg",
                "payload": {"type": "token_usage_record", "rate_limits": {"primary": primary}},
            }
        )
    if message is not None:
        lines.append(
            {
                "timestamp": "2026-09-08T05:24:10.000Z",
                "type": "event_msg",
                "payload": {"type": "task_complete", "error": {"message": message}},
            }
        )
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    store = Store(root)
    store.init(Task(original_request="fix it", source_agent="codex", fallback_agent="claude-code"))
    return store


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-sessions"
    reader = sessions.CodexSessionReader(home)
    monkeypatch.setitem(sessions.READERS, "codex", reader)
    return home / "2026" / "09" / "07"


# -- reading ----------------------------------------------------------------


def test_a_real_exhaustion_message_is_found_and_classified(project, codex_home):
    write_rollout(codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE)
    found = sessions.latest_failure("codex", project.root)
    assert found.reason == "quota_exhausted"
    assert "usage limit" in found.message


def test_the_providers_own_reset_time_is_read(project, codex_home):
    # A fixed date would quietly stop testing the countdown once it passed.
    moment = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).replace(microsecond=0)
    write_rollout(
        codex_home,
        "r.jsonl",
        project.root,
        message=REAL_QUOTA_MESSAGE,
        resets_at=int(moment.timestamp()),
        used=100.0,
    )
    found = sessions.latest_failure("codex", project.root)
    assert found.resets_at == moment.isoformat()
    assert found.used_percent == 100.0
    assert found.window_minutes == 300
    assert "resets in 119 min" in found.describe()


def test_only_this_projects_sessions_are_read(project, codex_home, tmp_path):
    write_rollout(codex_home, "other.jsonl", tmp_path / "somewhere-else", message=REAL_QUOTA_MESSAGE)
    assert sessions.latest_failure("codex", project.root) is None


def test_a_session_that_did_not_fail_yields_nothing(project, codex_home):
    write_rollout(codex_home, "r.jsonl", project.root, used=42.0)
    assert sessions.latest_failure("codex", project.root) is None


def test_a_missing_session_directory_is_not_an_error(project, tmp_path, monkeypatch):
    monkeypatch.setitem(
        sessions.READERS, "codex", sessions.CodexSessionReader(tmp_path / "nope")
    )
    assert sessions.latest_failure("codex", project.root) is None


def test_an_agent_without_a_reader_yields_nothing(project):
    assert sessions.latest_failure("gemini", project.root) is None


def test_a_corrupt_rollout_is_skipped_not_fatal(project, codex_home):
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "broken.jsonl").write_text("{ not json\n", encoding="utf-8")
    assert sessions.latest_failure("codex", project.root) is None


def test_nothing_but_the_two_fields_is_kept(project, codex_home):
    """A session log holds whatever the agent saw. Only two fields are read."""
    write_rollout(codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE, used=100.0)
    found = sessions.latest_failure("codex", project.root)
    blob = json.dumps(found.__dict__, ensure_ascii=False)
    assert "hunter2" not in blob  # the transcript line is never touched


# -- what it changes --------------------------------------------------------


def test_a_manual_switch_learns_the_real_reason(project, codex_home, capsys):
    write_rollout(codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE)
    assert main(["switch", "--to", "claude", "--no-launch"]) == 0

    out = capsys.readouterr().out
    assert "from codex's own session log" in out
    failures = [
        e for e in project.iter_jsonl(project.events_file) if e["event"] == "agent_unavailable"
    ]
    # Not the manual_handoff default: the agent said why it stopped.
    assert failures[-1]["reason"] == "quota_exhausted"


def test_the_cooldown_becomes_the_measured_reset_time(project, codex_home):
    resets = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3, minutes=46)).timestamp())
    write_rollout(
        codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE, resets_at=resets
    )
    assert main(["switch", "--to", "claude", "--no-launch"]) == 0

    record = health_mod.Health.open().get("codex")
    assert record.measured is True
    minutes = record.seconds_left() / 60
    assert 220 < minutes < 232  # the real window, not the five-hour estimate
    assert "its own reset time" in record.describe()


def test_an_explicit_reason_is_never_overridden(project, codex_home, capsys):
    write_rollout(codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE)
    assert main(["switch", "--to", "claude", "--reason", "manual_handoff", "--no-launch"]) == 0
    # `--reason manual_handoff` is indistinguishable from the default, so the
    # log still speaks; any *other* explicit reason must win outright.
    assert main(["init", "--force", "again"]) == 0
    assert main(["switch", "--to", "claude", "--reason", "network_failure", "--no-launch"]) == 0
    failures = [
        e for e in project.iter_jsonl(project.events_file) if e["event"] == "agent_unavailable"
    ]
    assert failures[-1]["reason"] == "network_failure"


def test_reading_can_be_turned_off(project, codex_home, tmp_path):
    write_rollout(codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE)
    (project.root / "handoff.toml").write_text("read_agent_sessions = false\n", encoding="utf-8")
    assert main(["switch", "--to", "claude", "--no-launch"]) == 0
    failures = [
        e for e in project.iter_jsonl(project.events_file) if e["event"] == "agent_unavailable"
    ]
    assert failures[-1]["reason"] == "manual_handoff"


# -- the CLI ----------------------------------------------------------------


def test_cli_sessions_shows_what_it_read(project, codex_home, capsys):
    resets = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).timestamp())
    write_rollout(
        codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE, resets_at=resets, used=100.0
    )
    assert main(["sessions"]) == 0
    out = capsys.readouterr().out
    assert "quota_exhausted" in out
    assert "usage limit" in out
    assert "r.jsonl" in out  # provenance
    assert "Only the failure message and rate-limit telemetry are read" in out


def test_cli_sessions_when_there_is_nothing(project, codex_home, capsys):
    assert main(["sessions"]) == 0
    assert "nothing recorded for this project" in capsys.readouterr().out


def test_cli_sessions_respects_the_switch_being_off(project, capsys):
    (project.root / "handoff.toml").write_text("read_agent_sessions = false\n", encoding="utf-8")
    assert main(["sessions"]) == 0
    assert "read_agent_sessions is off" in capsys.readouterr().out


def test_health_can_be_refreshed_from_the_logs(project, codex_home, capsys):
    """A failure that never went through a handoff still leaves health stale."""
    import datetime as _dt

    resets = int((_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=3)).timestamp())
    write_rollout(
        codex_home, "r.jsonl", project.root, message=REAL_QUOTA_MESSAGE, resets_at=resets
    )
    assert not health_mod.Health.open().cooling("codex")

    assert main(["health", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "updated codex from its own log" in out

    record = health_mod.Health.open().get("codex")
    assert record.measured is True
    assert 170 < record.seconds_left() / 60 < 185


def test_refresh_with_nothing_to_find(project, codex_home, capsys):
    assert main(["health", "--refresh"]) == 0
    assert "nothing to update" in capsys.readouterr().out


# -- Claude Code ------------------------------------------------------------
#
# Shaped after a real Claude Code 2.1.263 session that hit its five-hour limit.

REAL_CLAUDE_MESSAGE = "You've hit your session limit \u00b7 resets 12:20am (America/Los_Angeles)"


def write_claude_session(project_dir, name, cwd, *, message=None, resets_at=None, status=None):
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / name
    lines = [
        # A real transcript opens with records that carry no cwd at all.
        {"type": "mode", "mode": "default"},
        {"type": "permission-mode"},
        {"type": "bridge-session"},
        {"type": "user", "cwd": str(cwd), "sessionId": "s1", "version": "2.1.263"},
        {"type": "assistant", "cwd": str(cwd), "message": {"content": "a token: hunter2"}},
    ]
    if message is not None:
        quota = {}
        if resets_at is not None:
            quota["resetsAt"] = resets_at
        if status is not None:
            quota["status"] = status
            quota["rateLimitType"] = "five_hour"
        lines.append(
            {
                "type": "assistant",
                "isApiErrorMessage": True,
                "apiErrorStatus": 429,
                "error": "rate_limit",
                "cwd": str(cwd),
                "timestamp": "2026-09-08T06:32:10.295Z",
                "quotaLimits": quota or None,
                "message": {"content": [{"type": "text", "text": message}]},
            }
        )
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude-projects"
    monkeypatch.setitem(
        sessions.READERS, "claude-code", sessions.ClaudeCodeSessionReader(home)
    )
    return home / "D--Files-Programs-Whatever"


def test_a_real_claude_session_limit_is_found(project, claude_home):
    write_claude_session(claude_home, "s.jsonl", project.root, message=REAL_CLAUDE_MESSAGE)
    found = sessions.latest_failure("claude-code", project.root)
    assert found.reason == "quota_exhausted"
    assert "session limit" in found.message


def test_a_rejected_window_beats_the_http_status(project, claude_home):
    """429 alone is a throttle; a rejected window is hours of unavailability."""
    write_claude_session(
        claude_home, "s.jsonl", project.root, message=REAL_CLAUDE_MESSAGE, status="rejected"
    )
    found = sessions.latest_failure("claude-code", project.root)
    assert found.reason == "quota_exhausted"
    assert found.window_minutes == 300


def test_claude_publishes_its_own_reset_time(project, claude_home):
    moment = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).replace(microsecond=0)
    write_claude_session(
        claude_home,
        "s.jsonl",
        project.root,
        message=REAL_CLAUDE_MESSAGE,
        resets_at=int(moment.timestamp()),
        status="rejected",
    )
    found = sessions.latest_failure("claude-code", project.root)
    assert found.resets_at == moment.isoformat()
    assert "resets in 239 min" in found.describe()


def test_a_bare_429_without_quota_state_is_a_rate_limit(project, claude_home):
    write_claude_session(claude_home, "s.jsonl", project.root, message="Request failed")
    found = sessions.latest_failure("claude-code", project.root)
    assert found.reason == "rate_limited"


def test_claude_sessions_from_other_projects_are_ignored(project, claude_home, tmp_path):
    write_claude_session(
        claude_home, "s.jsonl", tmp_path / "elsewhere", message=REAL_CLAUDE_MESSAGE
    )
    assert sessions.latest_failure("claude-code", project.root) is None


def test_a_claude_session_that_did_not_fail_yields_nothing(project, claude_home):
    write_claude_session(claude_home, "s.jsonl", project.root)
    assert sessions.latest_failure("claude-code", project.root) is None


def test_the_claude_transcript_is_not_read(project, claude_home):
    write_claude_session(claude_home, "s.jsonl", project.root, message=REAL_CLAUDE_MESSAGE)
    found = sessions.latest_failure("claude-code", project.root)
    assert "hunter2" not in json.dumps(found.__dict__, ensure_ascii=False)


def test_handing_back_from_claude_learns_the_real_reason(project, claude_home, capsys):
    """The flow that prompted all of this: claude runs out, codex takes over."""
    moment = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).replace(microsecond=0)
    write_claude_session(
        claude_home,
        "s.jsonl",
        project.root,
        message=REAL_CLAUDE_MESSAGE,
        resets_at=int(moment.timestamp()),
        status="rejected",
    )
    task = project.read_task()
    task.source_agent = "claude-code"
    project.write_task(task)

    assert main(["switch", "--to", "codex", "--no-launch"]) == 0
    assert "from claude-code's own session log" in capsys.readouterr().out

    record = health_mod.Health.open().get("claude-code")
    assert record.reason == "quota_exhausted"
    assert record.measured is True


def test_an_unrecognised_api_error_is_not_called_a_crash(project, claude_home):
    # The shared classifier's process_crashed fallback is about exit codes.
    write_claude_session(claude_home, "s.jsonl", project.root, message="Something odd")
    found = sessions.latest_failure("claude-code", project.root)
    assert found.reason == "rate_limited"  # from the 429, not from the text


def test_a_codex_message_nobody_recognises_yields_no_opinion(project, codex_home):
    write_rollout(codex_home, "r.jsonl", project.root, message="something unfamiliar")
    found = sessions.latest_failure("codex", project.root)
    assert found is not None and found.reason is None
