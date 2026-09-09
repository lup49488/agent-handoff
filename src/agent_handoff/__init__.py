"""Lightweight cross-agent failover / handoff tool.

The layer records what an agent did to the repository (deterministic state)
and why it did it (semantic checkpoints), then renders a HANDOFF package
another agent can continue from.

Implemented through Phase 6: manual handoff, supervised automatic failover,
semantic checkpoints, a configurable bidirectional fallback chain, agents
described as data, the durability the whole idea rests on -- if the layer that
remembers the work can lose it, nothing else here matters -- and enough memory
of which agents are currently out of action to stop re-launching them.
"""

__version__ = "0.9.2"

HANDOFF_DIR = ".agent-handoff"
