# SCP Skills

**SCP Skills** is a focused skill pack for building and operating AI-agent runtimes with evidence, safety boundaries, recovery, and verifiable release gates.

> These files are engineering guidance and review workflows. They are not a security certification, an AI model, a sandbox, or a guarantee that every SCP code path is production-ready.

## Pack status

- **14 normative SCP Skills** are currently part of the target authority set.
- **1 candidate Skill** (`scp-continuous-operations-loop`) is present for evaluation but is **not normative ground truth yet**. Promotion requires `scp-skill-review` C1–C6 with Integration-or-higher evidence and independent verification.
- Counts are hypotheses until re-counted from `.agents/skills/*/SKILL.md`; index drift is a blocker for claiming the pack is fully reconciled.

## What this pack adds

The pack is designed for the part of an agent system that ordinary coding workflows often leave implicit: what the agent is allowed to do, what actually happened after an action, how to recover when a response is lost, and what evidence is required before calling a task or release complete.


| Skill | Main question it answers | Status |
|---|---|---|
| [`scp-dna`](scp-dna/SKILL.md) | How do we reason from evidence, find missing pieces, and avoid confusing PASS with truth? | Normative |
| [`scp-capability-security-review`](scp-capability-security-review/SKILL.md) | Is this action allowed for this task, attempt, resource, operation,and risk tier? | Normative |
| [`scp-computer-use-recovery`](scp-computer-use-recovery/SKILL.md) | What should happen when a browser/tool/worker fails after a side effect may already have happened? | Normative |
| [`scp-delta-audit`](scp-delta-audit/SKILL.md) | Can a suspected architectural/security gap be proven by evidence, causal analysis, probe-before-patch and anti-placebo mutation rather than static inference? | Normative |
| [`scp-gateway-resilience`](scp-gateway-resilience/SKILL.md) | Govern the LLM Gateway, Circuit Breakers, Model Fallback Cascade,and API rate limits. | Normative |
| [`scp-learning-loop-guard`](scp-learning-loop-guard/SKILL.md) | Govern the continuous learning loop, knowledge warehouse, Deep Scraper,and Autofix engine. | Normative |
| [`scp-reality-verifier`](scp-reality-verifier/SKILL.md) | Is the result static evidence, integration evidence, end-to-end proof, or recovery proof? | Normative |
| [`scp-release-evidence-gate`](scp-release-evidence-gate/SKILL.md) | What must be checked before calling an agent-runtime candidate stable? | Normative |
| [`scp-runtime-audit`](scp-runtime-audit/SKILL.md) | Are the process, port, health, readiness, test runner,and runtime evidence real now? | Normative |
| [`scp-safe-latency-optimizer`](scp-safe-latency-optimizer/SKILL.md) | How can latency be reduced without removing policy, verifier, revocation, egress, or recovery controls? | Normative |
| [`scp-startup-troubleshooter`](scp-startup-troubleshooter/SKILL.md) | Why does a service fail to start or report the wrong port/readiness state? | Normative |
| [`scp-task-kernel-review`](scp-task-kernel-review/SKILL.md) | Is the runtime a durable task kernel or only an orchestrator with loosely connected modules? | Normative |
| [`scp-web-orchestration-safety`](scp-web-orchestration-safety/SKILL.md) | Govern browser sessions, DOM manipulation, CDP protocol rules,and anti-honeypot tactics. | Normative |
| [`scp-skill-review`](scp-skill-review/SKILL.md) | Review bộ skill: index nhất quán, bằng chứng, calibration, và định xem skill nào đáng tin làm chuẩn sửa chính SCP? | Normative |
| [`scp-continuous-operations-loop`](scp-continuous-operations-loop/SKILL.md) | How should SCP discover recurring work, schedule it, persist durable state, separate maker/checker, bound cost/retries, reconcile unknown side effects, and mature safely toward unattended operation? | **Candidate** |

## Recommended order

For a new change, start with `scp-dna` and define the claim and evidence required. Use `scp-capability-security-review` before granting or executing a capability. Use `scp-task-kernel-review` and `scp-computer-use-recovery` when the change crosses task state, worker, lease, checkpoint, or side-effect boundaries. Use `scp-reality-verifier` and `scp-runtime-audit` to check the actual result. Finish with `scp-release-evidence-gate`; use `scp-safe-latency-optimizer` only after measuring the baseline. Use `scp-startup-troubleshooter` when the observed runtime does not match the service contract. For recurring or autonomous operation, evaluate `scp-continuous-operations-loop` as a candidate orchestration skill, but do not treat it as normative authority until promoted through `scp-skill-review`.

## Minimal installation

The files are plain Markdown and can be copied into an agent's project skill directory:

```bash
# From the repository root
cp -R skills/scp-dna /path/to/your/agent/skills/
cp -R skills/scp-reality-verifier /path/to/your/agent/skills/
```

For a complete **normative** SCP review, copy all fourteen normative skill directories and preserve `scp-dna/references/dna-principles.md`. The candidate `scp-continuous-operations-loop` may be copied for evaluation, but its presence must not be counted as proof of normative promotion.

## How to use them safely

A skill is a process contract, not permission to perform an action. Before an external write, publish, upload, delete, payment, credential change, or privilege change, the runtime still needs a task-scoped capability, an explicit policy decision, the required approval, an independent postcondition, and an audit trail. If the state or evidence is unclear, the safe result is `UNKNOWN`, `RECONCILING`, `HUMAN_REVIEW`, or `BLOCKED` rather than an automatic retry.

## Verification standard

Use the following evidence levels:

| Level | Meaning |
|---|---|
| Static | A file, route, schema, pattern, or assertion exists; this does not prove runtime behavior. |
| Integration | Components connect and a bounded interaction works. |
| End-to-end | A real task crosses planning, policy, tool, observation, verification, audit, and artifact boundaries. |
| Recovery | End-to-end evidence plus crash, timeout, lease, unknown-state, or restart handling. |

A green test suite is reported as `PASS_WITHIN_SCOPE`. It must not be expanded into “no bugs”, “production safe”, or “top-ranked” without additional independent evidence.

## Relationship to the SCP repository

This directory contains the reusable skill documents. The Python/TypeScript runtime, tests, launchers, reports, and current release status remain in the rest of the repository. The current SCP repository status is documented in the root [`README.md`](../README.md), including known limitations for 24/7 runtime, security, and RAG evaluation.

## License

The skill documents are distributed with the repository's MIT license. Third-party tools, models, and dependencies may have separate licenses.
