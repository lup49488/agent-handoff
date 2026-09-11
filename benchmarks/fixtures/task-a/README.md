# Task A: single-module slug bug

Each snapshot is an incomplete implementation of the same prompt. Copy exactly
one `project/` directory into a fresh disposable repository. Run
`python acceptance.py` from that directory; it must fail before the target
agent works and pass only after a correct completion.

For the Baseline arm, provide `prompt.md` and the copied project only. For the
Handoff arm, additionally provide the matching `handoff-context.md`. Do not
show that context file to the Baseline arm.
