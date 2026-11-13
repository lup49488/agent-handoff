"""A real Codex that 'runs out of quota' partway through, for a failover drill.

    python drill_codex.py "<prompt>"

Starts Codex exactly as agent-handoff's built-in adapter does, passes its
output through, and once Codex has first changed a file in the work tree
(or after MAX_SECONDS) waits GRACE_SECONDS, kills its whole process tree,
prints a usage-limit message in the form Codex itself uses, and exits 1 --
so the supervisor hands the task on as it would after a real exhaustion.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_handoff.adapters import registry
from agent_handoff.runner import _kill_process_tree

GRACE_SECONDS = 8
MAX_SECONDS = 240
WATCHED = ("stats.py", "report.py")

root = Path.cwd()
prompt = sys.argv[1]
started = time.monotonic()
process = registry.get("codex", root).start(prompt, capture_output=True)


def pump(stream, sink):
    for line in iter(stream.readline, ""):
        sink.write(line)
        sink.flush()


for stream, sink in ((process.stdout, sys.stdout), (process.stderr, sys.stderr)):
    threading.Thread(target=pump, args=(stream, sink), daemon=True).start()


def changed():
    out = subprocess.run(["git", "status", "--porcelain", "--", *WATCHED],
                         cwd=str(root), capture_output=True, text=True).stdout
    return bool(out.strip())


first_edit = None
while process.poll() is None:
    now = time.monotonic()
    if first_edit is None and changed():
        first_edit = now
        print("[drill] first edit at %.1f s; stopping Codex in %d s" % (now - started, GRACE_SECONDS),
              file=sys.stderr, flush=True)
    if (first_edit is not None and now - first_edit >= GRACE_SECONDS) or now - started >= MAX_SECONDS:
        _kill_process_tree(process)
        process.wait(timeout=30)
        print("[drill] Codex stopped at %.1f s" % (time.monotonic() - started), file=sys.stderr, flush=True)
        print("ERROR: You've hit your usage limit. Upgrade to Pro or try again later.",
              file=sys.stderr, flush=True)
        sys.exit(1)
    time.sleep(1)

print("[drill] Codex exited by itself at %.1f s with %s" % (time.monotonic() - started, process.returncode),
      file=sys.stderr, flush=True)
sys.exit(process.returncode)
