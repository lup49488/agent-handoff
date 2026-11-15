"""A real agent that 'runs out of quota' partway through, for a failover drill.

    python drill_agent.py codex "<prompt>"
    python drill_agent.py claude-code --grace 0 "<prompt>"

Starts the named agent exactly as agent-handoff's built-in adapter does --
including the done check from the project's config, which the supervisor
can only grant the agent it starts, and here that is this wrapper -- and
passes its output through. Once the agent first changes a watched file (or
after --max seconds) it waits --grace seconds, kills the agent's whole
process tree, prints the usage-limit message that agent itself prints, and
exits 1, so the supervisor hands the task on as after a real exhaustion.

With --grace 0 the stop lands on the first edit. Claude Code edits one file
at a time, so that leaves the work genuinely half-done.
"""
import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_handoff import config as config_mod
from agent_handoff.adapters import registry
from agent_handoff.runner import _kill_process_tree

#: What each agent prints when its plan's window is spent, as observed.
LIMIT_MESSAGES = {
    "codex": "ERROR: You've hit your usage limit. Upgrade to Pro or try again later.",
    "claude-code": "You've hit your session limit. Resets at 3am.",
}

parser = argparse.ArgumentParser()
parser.add_argument("agent", choices=sorted(LIMIT_MESSAGES))
parser.add_argument("--grace", type=float, default=8, help="seconds between the first edit and the stop")
parser.add_argument("--max", type=float, default=240, help="stop after this long regardless")
parser.add_argument("--watch", nargs="+", default=["stats.py", "report.py"])
parser.add_argument("prompt")
args = parser.parse_args()

root = Path.cwd()
adapter = registry.get(args.agent, root)
done_check = config_mod.load(root).done_check
adapter.allowed_commands = (done_check,) if done_check else ()
started = time.monotonic()
process = adapter.start(args.prompt, capture_output=True)


def pump(stream, sink):
    for line in iter(stream.readline, ""):
        sink.write(line)
        sink.flush()


for stream, sink in ((process.stdout, sys.stdout), (process.stderr, sys.stderr)):
    threading.Thread(target=pump, args=(stream, sink), daemon=True).start()


def changed():
    out = subprocess.run(["git", "status", "--porcelain", "--", *args.watch],
                         cwd=str(root), capture_output=True, text=True).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def say(text):
    print("[drill] " + text, file=sys.stderr, flush=True)


first_edit = None
while process.poll() is None:
    now = time.monotonic()
    if first_edit is None:
        files = changed()
        if files:
            first_edit = now
            say("first edit at %.1f s (%s); stopping %s in %g s"
                % (now - started, ", ".join(files), args.agent, args.grace))
    if (first_edit is not None and now - first_edit >= args.grace) or now - started >= args.max:
        _kill_process_tree(process)
        process.wait(timeout=30)
        say("%s stopped at %.1f s with %s changed" % (args.agent, time.monotonic() - started,
                                                      ", ".join(changed()) or "nothing"))
        print(LIMIT_MESSAGES[args.agent], file=sys.stderr, flush=True)
        sys.exit(1)
    time.sleep(0.25)

say("%s exited by itself at %.1f s with %s" % (args.agent, time.monotonic() - started, process.returncode))
sys.exit(process.returncode)
