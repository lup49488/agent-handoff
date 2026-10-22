"""A quiet agent that is working is not a stalled one.

Silence used to mean no output, so an agent that printed nothing while it
worked was killed as stalled and the task handed on: `claude -p` prints its
answer only at the end, and a long test run or build prints nothing until it
finishes. Work leaves other traces -- files change, the agent checkpoints,
its process tree uses the CPU -- and an agent retrying an unreachable
endpoint leaves none of them.
"""

import subprocess
import sys

import pytest

from agent_handoff import runner
from agent_handoff.adapters.base import AgentAdapter
from agent_handoff.models import Task
from agent_handoff.runner import run
from agent_handoff.store import Store


class _Script(AgentAdapter):
    executable = sys.executable

    def __init__(self, root, name, script):
        super().__init__(root)
        self.name = name
        self.script = script

    def resume_argv(self, instruction):
        return [self.executable, "-u", "-c", self.script]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Store(tmp_path)
    s.init(Task(original_request="Fix it", source_agent="codex", fallback_agent="claude-code"))
    return s


def ran_without_handoff(store, script, stall_timeout=2):
    code = run(
        store,
        _Script(store.root, "codex", script),
        [_Script(store.root, "claude-code", "print('took over')")],
        stall_timeout=stall_timeout,
    )
    task = store.read_task()
    stalls = [e for e in store.iter_jsonl(store.events_file)
              if e["event"] == "agent_unavailable" and e.get("reason") == "agent_stalled"]
    return code == 0 and task.source_agent == "codex" and not stalls


#: Prints nothing for five seconds, against a two-second timeout, but edits a
#: file in the work tree throughout -- as an agent applying a patch does.
SILENT_BUT_EDITING = (
    "import time, pathlib\n"
    "p = pathlib.Path('report.py')\n"
    "for i in range(10):\n"
    "    p.write_text('x = %d\\n' % i); time.sleep(0.5)\n"
)

#: Prints nothing and changes nothing for five seconds, but uses a core -- as
#: a long, quiet test run or build does.
SILENT_BUT_COMPUTING = (
    "import time\n"
    "end = time.time() + 5\n"
    "while time.time() < end:\n"
    "    sum(i * i for i in range(10000))\n"
)

#: Prints nothing, but writes a checkpoint now and then.
SILENT_BUT_CHECKPOINTING = (
    "import time, pathlib\n"
    "p = pathlib.Path('.agent-handoff/state.md')\n"
    "for i in range(10):\n"
    "    p.write_text('# Current Objective\\n\\nstep %d\\n' % i); time.sleep(0.5)\n"
)


def test_an_agent_editing_files_in_silence_is_not_stalled(store):
    assert ran_without_handoff(store, SILENT_BUT_EDITING)


def test_an_agent_computing_in_silence_is_not_stalled(store):
    assert ran_without_handoff(store, SILENT_BUT_COMPUTING)


def test_an_agent_checkpointing_in_silence_is_not_stalled(store):
    assert ran_without_handoff(store, SILENT_BUT_CHECKPOINTING)


def test_an_agent_doing_nothing_in_silence_is_still_stalled(store):
    """Waiting on an unreachable endpoint: no output, no edits, no CPU."""
    assert not ran_without_handoff(store, "import time; time.sleep(60)")
    assert store.read_task().source_agent == "claude-code"


def test_activity_is_looked_for_only_once_the_agent_has_gone_quiet(store):
    """A chatty agent never pays for the probe."""
    calls = []
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time\nfor _ in range(6):\n    print('working'); time.sleep(0.3)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    code, _, _, stalled = runner._drain(store, process, stall_timeout=4,
                                        activity_probe=lambda: calls.append(1) or False)
    assert code == 0 and not stalled
    assert calls == []


def test_a_probe_that_fails_is_no_evidence_either_way(store):
    def broken():
        raise RuntimeError("ps is not installed")

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _, _, _, stalled = runner._drain(store, process, stall_timeout=2, activity_probe=broken)
    assert stalled


def test_the_workspace_state_sees_a_second_edit_to_a_modified_file(store, tmp_path):
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "report.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=str(tmp_path), check=True, capture_output=True)

    (tmp_path / "report.py").write_text("x = 2\n", encoding="utf-8")
    first = runner._workspace_state(store)
    (tmp_path / "report.py").write_text("x = 33\n", encoding="utf-8")

    assert runner._workspace_state(store) != first


def test_ps_cumulative_times_are_read_in_every_shape():
    assert runner._ps_time("05:07") == 307
    assert runner._ps_time("01:02:03") == 3723
    assert runner._ps_time("2-00:00:01") == 172801
    assert runner._ps_time("0:00.50") == 0.5
