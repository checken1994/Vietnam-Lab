# Handoff Report: Investigation of Task Kernel State Machine & Autonomous Mode (Requirement R1)

**Task**: Deep Architectural Investigation of Task Kernel, State Machine, and Adapter implementations in SCP for Milestone Planning (Requirement R1: Global Autonomous State Machine).  
**Author**: Explorer Subagent (`.agents/explorer_survey_1/`)  
**Date**: 2026-09-20  
**Status**: COMPLETE (Read-Only Analysis)  

---

## 1. Observation

### 1.1 State Machine Location & Definition
The state machine for the SCP Task Kernel is defined and enforced in two primary files:
- **Specification and Public Contract**: `scp/task_kernel.py`
- **Engine Implementation**: `scp/task_kernel_parts/taskkernel.py`
- **Adapter Gate**: `scp/ask_kernel_adapter.py`
- **Hands Bridge Gate**: `scp/hands/task_kernel_bridge.py`

#### Exact State Universe (`scp/task_kernel.py:16-22`)
```python
STATES = {
    "CREATED", "PLANNING", "READY", "QUEUED", "LEASED", "RUNNING",
    "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN", "RECOVERING",
    "RECONCILING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "COMPLETED",
    "FAILED", "CANCELLED",
}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
```
- **Primary States**: Exactly 17 states in `STATES`.
- **Special Gated State**: `WAITING_APPROVAL` is defined in `ALLOWED_TRANSITIONS` (as a transition destination from `PLANNING` and source to `READY`/`CANCELLED`) and is explicitly recognized by `TaskKernel.transition()` in `scp/task_kernel_parts/taskkernel.py:392`:
  ```python
  if to_state not in STATES and to_state != "WAITING_APPROVAL":
      raise InvalidTransition(f"unknown target state {to_state}")
  ```

#### Transition Table (`scp/task_kernel.py:23-42`)
```python
ALLOWED_TRANSITIONS = {
    "CREATED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"READY", "WAITING_APPROVAL", "FAILED", "CANCELLED"},
    "WAITING_APPROVAL": {"READY", "CANCELLED"},
    "READY": {"QUEUED", "CANCELLED"},
    "QUEUED": {"LEASED", "CANCELLED"},
    "LEASED": {"RUNNING", "RECOVERING", "CANCELLED"},
    "RUNNING": {"WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "RECOVERING", "HUMAN_REVIEW", "FAILED", "CANCELLED"},
    "WAITING_TOOL": {"VERIFYING", "UNKNOWN", "RECOVERING", "FAILED", "CANCELLED"},
    "VERIFYING": {"RUNNING", "COMPLETED", "HUMAN_REVIEW", "FAILED"},
    "CHECKPOINTED": {"RUNNING", "QUEUED", "CANCELLED"},
    "UNKNOWN": {"RECONCILING", "HUMAN_REVIEW", "RECOVERING", "FAILED", "CANCELLED"},
    "HUMAN_REVIEW": {"READY", "CANCELLED", "FAILED"},
    "RECOVERING": {"RECONCILING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED"},
    "RECONCILING": {"RECOVERING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED", "CANCELLED"},
    "RETRY_SCHEDULED": {"QUEUED", "FAILED", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}
```

#### Enforcement Logic (`scp/task_kernel_parts/taskkernel.py:381-524`)
1. **Forbidden Direct Transitions**: Lines 394–397 prohibit direct transitions to `COMPLETED` and `FAILED` via `transition()`. One MUST invoke `commit_completed()` or `commit_failed()` with verified evidence.
2. **Forbidden Direct Approval**: Lines 425–428 prohibit direct transition `WAITING_APPROVAL -> READY` via `transition()`. One MUST call `commit_approval()` with a cryptographically verified capability token.
3. **Optimistic Concurrency Control (OCC)**: Version matching on table `tasks` (`cur_version == expected_version`), raising `OptimisticLockError` on concurrency conflict.
4. **Idempotent Self-Loop on `HUMAN_REVIEW`**: Line 420 detects `old == "HUMAN_REVIEW" and to_state == "HUMAN_REVIEW"`, rolls back transaction and returns task (idempotent no-op).
5. **Lease Authority Enforcement**: Active lease required for non-system callers across all execution states (`LEASED`, `RUNNING`, `WAITING_TOOL`, `VERIFYING`, `CHECKPOINTED`, `UNKNOWN`).
6. **Journal Append-Only Hash Chain**: Lines 510–520 append state transitions to the `events` table with monotonic `seq` numbers, payload hashes, and parent hash pointers (`hash_chain_valid`).

---

### 1.2 Analysis of All Triggers for `HUMAN_REVIEW`
The investigation identified 12 distinct code paths that cause a task to enter or require `HUMAN_REVIEW`:

1. **`AskKernelAdapter.finalize` — Verification Failure** (`scp/ask_kernel_adapter.py:638-642`):
   When `verify_response()` returns `verdict != "VERIFIED"` (e.g. `CONTRADICTED` or `INSUFFICIENT` due to failed RealityJudge, ungrounded response, or governance veto), `_escalate_to_human_review()` moves the task to `HUMAN_REVIEW`.
2. **`AskKernelAdapter.finalize` — Stale Lifecycle Authority** (`scp/ask_kernel_adapter.py:591-595`):
   When the task state is no longer in `_ASK_LIFECYCLE_INTACT_STATES` (`{"RUNNING", "VERIFYING"}`) because a background recovery or lease sweep moved it, `_stale_lifecycle_result()` moves the task to `HUMAN_REVIEW` and withholds the answer.
3. **`AskKernelAdapter.finalize` — Race on `transition("VERIFYING")`** (`scp/ask_kernel_adapter.py:600-607`):
   If `transition(task_id, "VERIFYING")` raises `InvalidTransition`, `_stale_lifecycle_result()` catches it and escalates to `HUMAN_REVIEW`.
4. **`AskKernelAdapter.finalize` — Race on Verification Commit** (`scp/ask_kernel_adapter.py:626-636`):
   If `commit_verification_result()` raises `KernelError` (e.g., lease expired during slow LLM judge evaluation), `_stale_lifecycle_result()` catches it and escalates to `HUMAN_REVIEW`.
5. **`TaskKernel.expire_leases` — Verifying Lease Expiry** (`scp/task_kernel_parts/taskkernel.py:820-822`):
   ```python
   elif cur_state == 'VERIFYING':
       cur = self.conn.execute("UPDATE tasks SET state='HUMAN_REVIEW',version=version+1,active_lease_id=NULL,active_fencing_token=0,updated_at=? WHERE task_id=? AND version=?", (now_iso(), task['task_id'], task['version']))
       self._append_event(task['task_id'], 'LEASE_EXPIRED', cur_state, 'HUMAN_REVIEW', 'kernel', 'heartbeat_expired', {'lease_id': lease['lease_id']})
   ```
   If worker heartbeat expires while in `VERIFYING`, the watchdog unconditionally updates state to `HUMAN_REVIEW`.
6. **`TaskKernel.reconcile_unknown` — Outcome `APPLIED`** (`scp/task_kernel_parts/taskkernel.py:1045-1050`):
   When reconciling an action from `UNKNOWN`, if outcome is `APPLIED`, `next_state` is set to `HUMAN_REVIEW`.
7. **`TaskKernel.reconcile_unknown` — Outcome `UNKNOWN`** (`scp/task_kernel_parts/taskkernel.py:1051-1056`):
   If outcome is ambiguous (`UNKNOWN`), `next_state` is set to `HUMAN_REVIEW`.
8. **`TaskKernel.reconcile_unknown` — Outcomes `PARTIAL` and `CONFLICT`** (`scp/task_kernel.py:358-375`):
   `_reconcile_unknown_complete_outcomes` enforces that `PARTIAL` and `CONFLICT` outcomes transition tasks to `HUMAN_REVIEW`.
9. **`TaskKernel.boot_recovery` — In-flight Interruption** (`scp/task_kernel_parts/taskkernel.py:1709-1711`):
   During kernel reboot recovery, any task found in `RUNNING` or `VERIFYING` is forcibly transitioned to `HUMAN_REVIEW`.
10. **`TaskKernel.recovery_decision` — Insufficient Evidence Fallback** (`scp/task_kernel_parts/taskkernel.py:1822`):
    Returns `RecoveryDecision('REVIEW', 'INSUFFICIENT_STATE_EVIDENCE', False, ('last_checkpoint', 'event_journal'), 'HUMAN_REVIEW', 'human_required')`.
11. **`HandsPlanner.execute` — Mutating Step Verification Failure** (`scp/hands/planner.py:553-565, 793`):
    When an action where `definition.mutates_state == True` fails or cannot be verified, `step['state'] = 'HUMAN_REVIEW'` and `plan['state'] = 'HUMAN_REVIEW'`.
12. **`ConfidenceRanker` — Autofix Review Threshold** (`scp/autofix/confidence_ranker.py:63, 204`):
    Fix proposals with confidence scores below auto-apply threshold or >= `DEFAULT_HUMAN_REVIEW_THRESHOLD` (0.50) are gated for human review.

---

### 1.3 Automated Verification and Receipt Mechanisms
Automated verification is governed by three decoupled components:
1. **Cryptographic Verifier Receipts** (`scp/core/verifier_receipt.py`):
   - `VerifierReceipt`: dataclass tracking `task_id`, `verifier_id`, `verdict` (`VERIFIED`), `evidence_ref`, `issued_at`, `signature`, and optional `attempt_id`.
   - `sign_verifier_receipt()`: computes HMAC-SHA256 over canonical bytes `task_id:verifier_id:verdict:evidence_ref:issued_at:.6f` using `SCP_VERIFIER_SECRET` (or `SCP_CAPABILITY_SECRET`).
   - `verify_verifier_receipt()`: validates timestamp skew (< 300s), ensures verdict is strictly `VERIFIED`, verifies `evidence_ref` is present, and checks HMAC signature with constant-time comparison `hmac.compare_digest`.
2. **Kernel Receipt Checkpoint & Commit Gate** (`scp/task_kernel_parts/taskkernel.py:1185-1286`):
   - `commit_verification_result(task_id, lease_id, verification_result)`:
     - Enforces that task is in state `VERIFYING`.
     - Cryptographically validates the receipt.
     - Calls `commit_completed()` to atomically transition task to `COMPLETED`, release lease, and write `TASK_COMPLETED` event with signature digest into the event journal.
3. **Concrete Verifier Implementations**:
   - `AskKernelAdapter.verify_response()`: checks RAG grounding ratio (word overlap), evaluates `RealityJudge.judge_async()` semantic correctness, validates governance uphold, verifies no untracked web fallback, and verifies provenance.
   - `TaskKernelHandsBridge`: verifies execution output against postcondition assertions (`success` and `verification.passed`), signs receipt, and invokes `commit_verification_result()`.
   - `IndependentVerifier` (`scp/verifier.py`): deterministic postcondition schema checker (`url_matches`, `text_contains`, `network_response`, `artifact_hash`).

---

### 1.4 Test Suite Inventory
The following test suites directly test state transitions, `HUMAN_REVIEW`, and kernel boundaries:
- **`tests/T04_kernel/test_task_kernel_mutation_contract.py`**:
  - `test_transition_contract_and_happy_lifecycle`: verifies happy path `CREATED -> PLANNING -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED`.
  - `test_human_review_remains_nonterminal_and_counted_as_in_flight`: tests task entering `HUMAN_REVIEW` and operator transition `HUMAN_REVIEW -> READY`.
- **`tests/T04_kernel/test_gap13_state_machine_boundaries.py`**:
  - `test_attack_commit_approval_on_all_non_waiting_states`: tests all 17 states (including `HUMAN_REVIEW`) rejecting `commit_approval`.
  - `test_attack_raw_transition_waiting_approval_to_ready`: tests `WAITING_APPROVAL -> READY` boundary.
  - `test_terminal_state_immutability_and_resurrection_attempts`: tests `COMPLETED`, `FAILED`, `CANCELLED` immutability.
- **`tests/T04_kernel/test_ask_kernel_lifecycle_and_identity.py`**:
  - `test_finalize_from_reconciling_returns_withheld_result_not_500`: `RECONCILING -> HUMAN_REVIEW`.
  - `test_finalize_from_human_review_never_raises`: `HUMAN_REVIEW` idempotency.
  - `test_double_escalation_is_idempotent_noop_not_raise`: double escalation to `HUMAN_REVIEW`.
  - `test_verified_commit_racing_stale_lease_does_not_500`: verified answer routed to `HUMAN_REVIEW` on expired lease.
  - `test_normal_running_finalize_path_is_unchanged`: `RUNNING -> VERIFYING -> COMPLETED`.
  - `test_transition_table_stays_strict_reconciling_and_human_review`: asserts strict edges for `RECONCILING` and `HUMAN_REVIEW`.
- **`tests/T04_kernel/test_ask_kernel_adapter_verify.py`**: tests RAG grounding, RealityJudge, and contradiction handling.
- **`tests/T04_kernel/test_ask_kernel_terminal_race.py`**: tests terminal race handling.
- **`tests/T04_kernel/test_verifier_receipt_provenance.py` & `test_verifier_receipt_branches.py`**: tests receipt HMAC signing, tampering, and verification.
- **`tests/T04_kernel/test_kernel_p1_regressions.py`**: verifies `_assert_legal_state_chain` across event journal entries.
- **`tests/T04_kernel/test_pg_boot_runtime.py`**: tests boot recovery moving `RUNNING -> HUMAN_REVIEW` and operator loop `HUMAN_REVIEW -> READY -> QUEUED`.
- **`tests/T01_boot/test_flow_01_boot_background_scp_standard.py`**: tests escalation backlog query (`HUMAN_REVIEW`, `UNKNOWN`, `RECONCILING`).
- **`tools/probes/probe_gap11_r2_watchdog_race.py`**: tests watchdog expiry race to `HUMAN_REVIEW`.

---

## 2. Logic Chain

1. **Premise 1 (Requirement R1 Scope)**:
   The objective is to establish a Global Autonomous State Machine that bypasses `HUMAN_REVIEW` so that tasks completing execution or passing automated verification automatically advance to terminal completion (`COMPLETED`) without blocking for operator intervention.
2. **Premise 2 (Invariant Conservation & Zero Mutation of State Table)**:
   - `ALLOWED_TRANSITIONS` defines:
     `"HUMAN_REVIEW": {"READY", "CANCELLED", "FAILED"}`
     `"VERIFYING": {"RUNNING", "COMPLETED", "HUMAN_REVIEW", "FAILED"}`
     `"PLANNING": {"READY", "WAITING_APPROVAL", "FAILED", "CANCELLED"}`
     `"WAITING_APPROVAL": {"READY", "CANCELLED"}`
   - The happy path `CREATED -> PLANNING -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED` **already completely avoids** `HUMAN_REVIEW`.
   - Modifying or loosening `ALLOWED_TRANSITIONS` directly would violate FA-01, fail `_assert_legal_state_chain` in `test_kernel_p1_regressions.py`, and violate `test_transition_table_stays_strict_reconciling_and_human_review`.
3. **Premise 3 (The Human-Review Trap Problem)**:
   - Tasks get stuck in `HUMAN_REVIEW` primarily due to:
     a) Automated verification failure in `AskKernelAdapter.finalize()` or `HandsPlanner`.
     b) Watchdog lease expiry during verification (`TaskKernel.expire_leases()`).
     c) Reconciling applied actions (`TaskKernel.reconcile_unknown()`).
     d) Boot recovery on restart (`TaskKernel.boot_recovery()`).
     e) Approval requirements on risk tiers (`WAITING_APPROVAL`).
4. **Premise 4 (Autonomous Resolution Architecture)**:
   - To bypass `HUMAN_REVIEW` globally without breaking existing states:
     - **Mechanism A (Pre-escalation bypass)**: When an action or verification succeeds with cryptographic proof (`VerifierReceipt`), the task must proceed directly to `COMPLETED` (or through `READY -> QUEUED -> LEASED -> VERIFYING -> COMPLETED`).
     - **Mechanism B (Auto-advance from `HUMAN_REVIEW`)**: If a task does land in `HUMAN_REVIEW` (e.g. from an asynchronous lease sweep during a slow judge run), the state machine or adapter can legally auto-transition:
       `HUMAN_REVIEW -> READY` (using existing allowed transition!), reclaim a lease, and commit the verified result to `COMPLETED`.
     - **Mechanism C (Fail-Closed on Genuine Verification Failure)**: When automated verification genuinely fails (evidence contradicted or ungrounded), in Autonomous Mode the task must transition to `FAILED` (fail-closed) rather than lingering indefinitely in `HUMAN_REVIEW` as an unresolved zombie task.
     - **Mechanism D (Reconciliation in Autonomous Mode)**: In `reconcile_unknown()`, if `outcome == 'APPLIED'` and postcondition verification is cryptographically proven, the state moves to `QUEUED` / `VERIFYING` -> `COMPLETED` rather than `HUMAN_REVIEW`.
     - **Mechanism E (Activation via Configuration)**: Control this behavior via `SCP_AUTONOMOUS_MODE=1` (or constructor parameter `autonomous_mode=True`) so that existing baseline tests continue to test the default human-in-the-loop behavior without regression.

---

## 3. Caveats

1. **No Source Code Modifications Made**: Per the Explorer archetype and pre-session mandate, this investigation is strictly read-only. No source files or tests were modified.
2. **Requirement R2 Synergy**: `WAITING_APPROVAL` is governed by capability tokens and `commit_approval()`. R1 addresses the post-execution and verification phase (`HUMAN_REVIEW`), while R2 will address pre-execution permission grants (`WAITING_APPROVAL`). Both must work harmoniously in Autonomous Mode.
3. **Test Integrity Caution**: Tests in `tests/T04_kernel/test_ask_kernel_lifecycle_and_identity.py` deliberately simulate human-review escalation to ensure HTTP 500 crashes do not occur. The autonomous mode must be configurable (e.g. env var `SCP_AUTONOMOUS_MODE` or adapter flag) so those specific unit tests can still verify their targeted regression cases.

---

## 4. Conclusion

1. **Feasibility**: Global Autonomous State Machine (Requirement R1) is **100% achievable without modifying the 17 core states or loosening `ALLOWED_TRANSITIONS`**.
2. **Primary Integration Targets**:
   - `scp/ask_kernel_adapter.py`: In `finalize()` and `_stale_lifecycle_result()`, under `SCP_AUTONOMOUS_MODE=1`:
     - If `verify_response()` is `VERIFIED`, auto-resolve any raced state and commit directly to `COMPLETED` with signed `VerifierReceipt`.
     - If `verify_response()` is `CONTRADICTED` or `INSUFFICIENT`, transition to `FAILED` (fail-closed) instead of escalating to `HUMAN_REVIEW`.
   - `scp/task_kernel_parts/taskkernel.py`:
     - In `expire_leases()`, under autonomous mode, move expiring `VERIFYING` tasks to `RECOVERING` (for automatic retry/reconciliation) rather than `HUMAN_REVIEW`.
     - In `reconcile_unknown()`, when `outcome == 'APPLIED'` with verified evidence, route to `QUEUED` instead of `HUMAN_REVIEW`.
     - Provide an autonomous resolution method `auto_resolve_human_review(task_id)` that transitions `HUMAN_REVIEW -> READY` when accompanied by valid verification proof.
3. **Compliance**: This design strictly satisfies Zero-Trust, Fail-Closed, FA-01 through FA-13, and enforces all transitions at the SQLite/database transaction level.

---

## 5. Verification Method

To independently verify the baseline state and state machine contracts:
1. **Run mutation contract tests**:
   ```pwsh
   pytest tests/T04_kernel/test_task_kernel_mutation_contract.py -v
   ```
2. **Run lifecycle and identity tests**:
   ```pwsh
   pytest tests/T04_kernel/test_ask_kernel_lifecycle_and_identity.py -v
   ```
3. **Run GAP-13 state machine boundary tests**:
   ```pwsh
   pytest tests/T04_kernel/test_gap13_state_machine_boundaries.py -q
   ```
4. **Run verifier receipt provenance tests**:
   ```pwsh
   pytest tests/T04_kernel/test_verifier_receipt_provenance.py tests/T04_kernel/test_verifier_receipt_branches.py -q
   ```
5. **Run entire T04 kernel test suite**:
   ```pwsh
   pytest tests/T04_kernel/ -q
   ```
   *Observed runtime baseline*: `240 passed, 23 skipped in 68.65s` (100% PASS across active suite).
6. **Check meta-audit for FA rules compliance**:
   ```pwsh
   python tools/t00_meta_audit.py
   ```
   *Observed runtime baseline*: `[T00 Meta-Audit] All integrity checks passed (0 new regressions).` Exit code 0.
