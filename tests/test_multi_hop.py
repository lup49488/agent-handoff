"""Two handoffs in a row: A -> B -> C, with work done at every hop.

The chain tests elsewhere check the hops themselves. This checks what the
third agent inherits: both earlier agents' uncommitted work, the second
agent's checkpoint and test result rather than the first's, a package that
still verifies, and the latest test result marked current because nothing
changed after it was recorded.
"""

import json
import subprocess
import sys

import pytest

from agent_handoff import integrity
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


HANDOFF = "[sys.executable, '-m', 'agent_handoff']"

#: Edits the work, checkpoints it, then runs out of quota.
AGENT_A = """
import pathlib, subprocess, sys
pathlib.Path('report.py').write_text('STEP = 1\\n')
subprocess.run(%s + ['checkpoint', '--objective', 'three steps', '--done', 'step 1 written by A'], check=True)
print('quota exhausted', file=sys.stderr)
raise SystemExit(1)
""" % HANDOFF

#: Notes what it inherited, adds its own work and a test result, then is rate limited.
AGENT_B = """
import json, pathlib, subprocess, sys
saw = {
    'report': pathlib.Path('report.py').read_text(),
    'package_has_a': 'step 1 written by A' in pathlib.Path('HANDOFF.md').read_text(),
}
pathlib.Path('report.py').write_text('STEP = 2\\n')
pathlib.Path('dedupe.py').write_text('SEEN = set()\\n')
subprocess.run(%s + ['checkpoint', '--done', 'step 2 written by B'], check=True)
subprocess.run(%s + ['tests', 'python check.py', '--exit-code', '1', '--summary', '1 step left'], check=True)
pathlib.Path('.b-saw.json').write_text(json.dumps(saw))
print('rate_limit_error', file=sys.stderr)
raise SystemExit(1)
""" % (HANDOFF, HANDOFF)

#: Notes what it inherited and finishes.
AGENT_C = """
import json, pathlib
package = pathlib.Path('HANDOFF.md').read_text()
saw = {
    'report': pathlib.Path('report.py').read_text(),
    'dedupe_exists': pathlib.Path('dedupe.py').exists(),
    'package_has_a': 'step 1 written by A' in package,
    'package_has_b': 'step 2 written by B' in package,
    'test_summary_is_bs': '1 step left' in package,
    'test_is_current': '**Current:**' in package,
    'reason': 'rate_limited' in package,
}
pathlib.Path('.c-saw.json').write_text(json.dumps(saw))
pathlib.Path('done.txt').write_text('ok')
print('finished')
"""

DONE_CHECK = sys.executable + ' -c "import pathlib, sys; sys.exit(0 if pathlib.Path(\'done.txt\').exists() else 1)"'


def git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
        git(tmp_path, *args)
    (tmp_path / "report.py").write_text("STEP = 0\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".b-saw.json\n.c-saw.json\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    s = Store(tmp_path)
    s.init(Task(original_request="three steps", source_agent="codex", fallback_agent="claude-code"))
    return s


@pytest.fixture
def ran(store):
    code = run(
        store,
        _Script(store.root, "codex", AGENT_A),
        [_Script(store.root, "claude-code", AGENT_B), _Script(store.root, "pi", AGENT_C)],
        done_check=DONE_CHECK,
    )
    b = json.loads((store.root / ".b-saw.json").read_text(encoding="utf-8"))
    c = json.loads((store.root / ".c-saw.json").read_text(encoding="utf-8"))
    return code, b, c


def test_the_second_agent_inherits_the_first_agents_work(ran):
    _, b, _ = ran
    assert b["report"] == "STEP = 1\n"
    assert b["package_has_a"]


def test_the_third_agent_inherits_both_agents_work(ran):
    _, _, c = ran
    assert c["report"] == "STEP = 2\n"
    assert c["dedupe_exists"]


def test_the_third_agent_reads_the_second_agents_checkpoint_and_result(ran):
    _, _, c = ran
    assert c["package_has_a"] and c["package_has_b"]  # the checkpoint accumulates
    assert c["test_summary_is_bs"]
    assert c["test_is_current"]  # B recorded it last; nothing changed after
    assert c["reason"]  # the package says why B stopped, not why A did


def test_the_chain_ends_done_with_two_handoffs_and_a_valid_package(ran, store):
    code, _, _ = ran
    task = store.read_task()
    assert code == 0
    assert task.status == "done"
    assert task.handoff_count == 2
    assert task.attempted == ["codex", "claude-code"]
    assert task.source_agent == "pi"
    assert integrity.verify_package(store) == []
    hops = [(e["from_agent"], e["to"]) for e in store.iter_jsonl(store.events_file)
            if e["event"] == "handoff_started"]
    assert hops == [("codex", "claude-code"), ("claude-code", "pi")]
    reasons = [e["reason"] for e in store.iter_jsonl(store.events_file) if e["event"] == "agent_unavailable"]
    assert reasons == ["quota_exhausted", "rate_limited"]
