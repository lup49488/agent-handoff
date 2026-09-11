# Benchmark assets

`manifest.json` is the fixed task/snapshot map. Keep task fixtures, source
snapshots, and raw local transcripts outside published results until they have
been reviewed for secrets and licensing.

Before aggregating a redacted result, validate it:

```text
python benchmarks/validate_trial.py results/B-60-handoff-r03.json
```

The validator rejects malformed records, impossible completion states, negative
measurements, and common credential markers. It is a publication guardrail, not
a substitute for human redaction review. Follow `docs/benchmark-design.md` for
the actual experiment protocol and result-claim rules.

Prepare an instrumentation-only workspace with the same fixture for either
experimental arm:

```text
python benchmarks/prepare_trial.py A 60 baseline .trials/A-60-baseline-r01
python benchmarks/prepare_trial.py A 60 handoff .trials/A-60-handoff-r01
```

The command never starts an agent. Both outputs contain the same incomplete
project and `prompt.md`; only the Handoff output adds `.agent-handoff/` and a
validated `HANDOFF.md`. It refuses to reuse an existing output directory, so a
setup error cannot overwrite an earlier trial.
