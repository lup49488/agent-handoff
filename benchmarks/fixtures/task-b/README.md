# Task B: API feature

Copy one snapshot's `project/` directory into a fresh disposable repository.
The Baseline arm receives `prompt.md` and that copy. The Handoff arm also
receives the matching `handoff-context.md`; it is not available to Baseline.
`python acceptance.py` must fail before completion and pass afterwards.
