# SCP Task Kernel Review
(Copied from .agents/skills/scp-task-kernel-review/SKILL.md)
---
name: scp-task-kernel-review
description: Review kiến trúc và bằng chứng của SCP Task Kernel gồm state machine, event journal, queue, lease, checkpoint, idempotency, verifier, recovery, observability và kill switch.
---
Core principles: State Machine invariants, Terminal state immutability, Atomic transitions, OCC lease fencing.
