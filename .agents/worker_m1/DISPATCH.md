## 2026-09-20T07:26:17Z
MANDATORY BINDING: You are strictly bound by Zero-Trust and Fail-Closed principles. You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Any code modifications must explicitly enforce boundaries at the Database/Hardware level, not via RAM/Variables.

DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

You are a Worker implementing Milestone 1 (M1: Autonomous State Machine & Adapter - Requirement R1).
Your working directory is: c:\Users\check\Downloads\scp\.agents\worker_m1\
Authoritative user request: c:\Users\check\Downloads\scp\.agents\ORIGINAL_REQUEST.md (read this file first).
Project scope: c:\Users\check\Downloads\scp\PROJECT.md.
Explorer findings: c:\Users\check\Downloads\scp\.agents\explorer_survey_1\handoff.md.

Relevant skills: View and follow c:\Users\check\Downloads\scp\.agents\skills\scp-dna\SKILL.md and c:\Users\check\Downloads\scp\.agents\skills\scp-task-kernel-review\SKILL.md.

FILES OWNED EXCLUSIVELY:
- c:\Users\check\Downloads\scp\scp\ask_kernel_adapter.py
- c:\Users\check\Downloads\scp\scp\task_kernel_parts\taskkernel.py

TASK OBJECTIVES:
Implement Requirement R1: Global Autonomous State Machine in SCP Task Kernel:
1. Support Autonomous Mode via configuration (environment variable `SCP_AUTONOMOUS_MODE` in {"1", "true", "yes"} and/or constructor parameter `autonomous_mode: bool = False` in `AskKernelAdapter` and `TaskKernel`).
2. In `scp/ask_kernel_adapter.py`:
   - In `finalize()` and `_stale_lifecycle_result()`, under Autonomous Mode:
     - When `verification["verdict"] == "VERIFIED"`: Commit the task directly to `COMPLETED` using a cryptographically signed `VerifierReceipt`. If a concurrent watchdog/sweep moved the task to `HUMAN_REVIEW`, auto-resolve it via `auto_resolve_human_review(task_id)` -> `READY` and complete.
     - When `verification["verdict"] != "VERIFIED"` (i.e. verification failed): Fail-closed by transitioning to `FAILED` (or `RETRY_SCHEDULED` if attempts < max_attempts) with an informative error payload instead of escalating to `HUMAN_REVIEW`.
     - In `_stale_lifecycle_result()`: In autonomous mode, do not escalate to `HUMAN_REVIEW`; fail-closed or auto-recover.
3. In `scp/task_kernel_parts/taskkernel.py`:
   - Add `auto_resolve_human_review(task_id: str, reason: str = "autonomous_verification_passed") -> dict`:
     Transitions `HUMAN_REVIEW -> READY` (an already legal edge in `ALLOWED_TRANSITIONS`), recording an audit event `AUTONOMOUS_HUMAN_REVIEW_RESOLVED`.
   - In `expire_leases()`: When `cur_state == 'VERIFYING'`, if autonomous mode is active, do not park in `HUMAN_REVIEW`; transition to `RECOVERING` or `FAILED`.
   - In `reconcile_unknown()`: When `outcome == 'APPLIED'` with verified evidence in autonomous mode, route to `QUEUED` / `VERIFYING` rather than `HUMAN_REVIEW`.
4. STRICT INVARIANTS:
   - DO NOT delete or loosen `STATES` (all 17 states must remain intact).
   - DO NOT modify `ALLOWED_TRANSITIONS` in `scp/task_kernel.py`.
   - DO NOT delete/skip/xfail any tests (FA-01, FA-02).
   - Verify that existing kernel tests pass: run `pytest tests/T04_kernel/ -q`.

OUTPUT:
Write your full implementation handoff report to `c:\Users\check\Downloads\scp\.agents\worker_m1\handoff.md`.
Include:
- Exact changes made with code diffs
- Build/test execution commands and full terminal output
- Confirmation of compliance with FA-01 to FA-13
Then send a message back to the orchestrator with a concise summary.
