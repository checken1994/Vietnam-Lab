# Handoff Report: Capability Authority & Token Management for Autonomous Mode (R2)

**Explorer**: `explorer_survey_2`  
**Milestone**: Autonomous Mode Milestone Planning (Requirements R1, R2, R3)  
**Task Focus**: Investigation of Capability Authority, PDP, PEP, Unified Broker, and Token Management in SCP to support Requirement R2 (Automatic Capability Granting without Operator Bottlenecks under Zero-Trust & FA-05).  
**Date**: 2026-09-20  

---

## 1. Observation

### 1.1 Architecture & Code Locations of Capability Components

Through direct inspection of the live SCP codebase, the Capability and Token Management system is distributed across the following core files and modules:

| Component | Role | File Path & Line Numbers | Key Classes / Functions |
|---|---|---|---|
| **CapabilityAuthority** | Durable epoch-based capability authority & token issuer | `scp/security/capability_epoch.py:94-255` | `CapabilityAuthority`, `CapabilityToken`, `parse_capability_token`, `issue()`, `validate()`, `revoke()`, `restore()` |
| **Token Cryptography & Signing** | HMAC-SHA256 signature computation, verification & secrets | `scp/core/capability_token.py:1-114` | `compute_token_signature()`, `verify_token_signature()`, `get_capability_secret()`, `mint_token()`, `verify_token()` |
| **PEP: PC Controller** | Zero-Trust execution & filesystem enforcement point | `scp/pc_control/pc_controller.py:112-179, 308, 372` | `PCController._verify_token()`, `execute()`, `write_file()`, `read_file()`, `rollback()` |
| **PDP: PC Controller** | Command policy evaluation against allowlists/regex | `scp/pc_control/pc_controller.py:235-265` | `PCController.evaluate()` |
| **PEP: Hands Executor** | Action-scoped capability enforcement for tool dispatch | `scp/hands/hands_executor.py:75-85, 132-178` | `HandsExecutor._check_capability()`, `execute()`, `_pc_command()` |
| **PDP / Catalog: Action Registry** | Declarative action inventory, risks, levels, approval requirements | `scp/hands/action_registry.py:14-85` | `ActionRegistry`, `ActionDefinition`, `policy_preview()` |
| **PEP / Durable Bridge: Kernel Hands Bridge** | Pre-dispatch PEP preventing kernel mutation before authorization | `scp/hands/task_kernel_bridge.py:305-322` | `TaskKernelHandsBridge.execute()`, `_policy_blocked_before_dispatch()` |
| **State Machine & Kernel Approval PEP** | Cryptographic approval verification for `WAITING_APPROVAL -> READY` | `scp/task_kernel_parts/taskkernel.py:39-130, 1287-1375` | `TaskKernel.commit_approval()`, `verify_approval_authority()` |
| **Verifier Provenance PEP** | Cryptographic receipt enforcement for `VERIFYING -> COMPLETED` | `scp/task_kernel_parts/taskkernel.py:1164-1230` | `TaskKernel.commit_verification_result()`, `commit_completed()` |
| **Workflow PDP / Orchestration** | Plan execution, step-level capability checks & approval halts | `scp/hands/planner.py:478-520, 605-625, 755-775` | `HandsPlanner.run_plan()`, `_run_step_dag()`, `_run_step_sequential()` |
| **Capability Level Governance** | 6-tier governance manager (Gà §12) | `scp/meta/capability_levels.py:26-100, 203-222` | `CapabilityLevel`, `CapabilityManager`, `get_capability_manager()` |
| **HTTP Boundary PEPs** | API route tokens & capability header extraction | `scp/api/routes/pc_controller_routes.py:71-75, 94-112`, `scp/api/routes/hands_routes.py:160-183` | `_guard()`, `pc_execute()`, `hands_execute()` |

#### Note on Conceptual Entity Names:
- **"Unified Broker"**: While referenced in `ORIGINAL_REQUEST.md` (e.g., line 17: *"PCController có các phương thức thực thi trực tiếp mà thiếu boundary kiểm tra token hợp lệ từ Unified Broker"*), there is no class or file named `UnifiedBroker` in the codebase. Instead, the "Unified Broker" boundary was concretely implemented as `CapabilityAuthority` (`scp/security/capability_epoch.py`) + `PCController._verify_token()` + `HandsExecutor._check_capability()`.
- **`scp/core/capability_authority.py`**: Does not exist as a separate file; the authoritative class is `CapabilityAuthority` in `scp/security/capability_epoch.py`. (A legacy stub exists in `scp/core/capability.py:1-18` containing an un-cryptographic `CapabilityManager`, but as noted in `reports/expert-panel/ARCH-AUDIT-D-security.md:24`, it has 0 import sites and is dead code).

---

### 1.2 Current Token Lifecycle: Request, Evaluation, Granting, and Validation

#### A. Request & Issuance
- **Current implementation**: In the production runtime (`scp/api/routes/`, `HandsExecutor`, `PCController`), **no in-process caller automatically requests tokens**. The system expects the caller (external client, HTTP request, or test runner) to provide a valid token in `capabilityToken` or the HTTP header `X-SCP-Capability-Token`.
- **Issuance mechanism**: `CapabilityAuthority.issue(subject: str) -> CapabilityToken` (`scp/security/capability_epoch.py:179-203`):
  1. Validates that `subject` is non-empty.
  2. Acquires `threading.RLock()`.
  3. Checks `state["revoked"]` (raises `CapabilityRevokedError` if revoked).
  4. Reads current `epoch` from atomic JSON state file (`capability_state.json`).
  5. Mints random `token_id = uuid.uuid4().hex` and timestamp `issued_at`.
  6. Computes HMAC-SHA256 signature using `self.secret` (`compute_token_signature` in `scp/core/capability_token.py:21-24` over `canonical = f"{subject}:{epoch}:{token_id}:{issued_at:.6f}"`).
  7. Returns immutable `CapabilityToken(subject, epoch, token_id, issued_at, signature)`.
- **Observed Limitation**: `CapabilityAuthority.issue()` is purely an issuer/signer; it does not evaluate contextual permissions, task state, or command safety. In existing tests, test fixtures call `authority.issue(...)` directly.

#### B. Validation & Policy Enforcement (PEP)
When an action is dispatched to `PCController` or `HandsExecutor`:
1. **PCController PEP** (`scp/pc_control/pc_controller.py:112-179`):
   - Fails closed if token is missing (`PermissionError: CapabilityRequiredError... FA-05`).
   - Parses token via `parse_capability_token(token)`.
   - Calls `capability_authority.validate(parsed)`.
   - Rejects tampered signatures fail-closed (`InvalidTokenSignatureError`).
   - Rejects stale/revoked epochs fail-closed (`PermissionError: CapabilityRevokedError`).
   - Checks scope matching:
     - `pc.execute`: subject must be in `{"pc.execute", "pc:execute", "hands:execute", "hands:pc.execute"}`.
     - `pc.write_file`: subject must be in `{"pc.write_file", "pc:write_file", "hands:pc.write_file"}`.
     - `pc.read_file`: subject must be in `{"pc.read_file", "pc:read_file", "hands:pc.read_file"}`.
     - `pc.rollback`: subject must be in `{"pc.rollback", "pc:rollback", "hands:rollback", "hands:pc.rollback"}`.
     - `pc.clear_kill_switch`: subject must be in `{"pc.clear_kill_switch", "pc:clear_kill_switch", "hands:admin", "hands:pc.clear_kill_switch"}`.
2. **HandsExecutor PEP** (`scp/hands/hands_executor.py:75-85, 132-178`):
   - Rejects missing token (`CapabilityRequiredError`).
   - Rejects subject mismatch: requires `subject == f"hands:{action}"`.
   - Re-validates token immediately prior to physical dispatch (`hands_executor.py:175`).
3. **TaskKernelHandsBridge PEP** (`scp/hands/task_kernel_bridge.py:305-316`):
   - Rejects missing token before creating any TaskKernel task, acquiring any lease, or recording any checkpoint.

---

### 1.3 Identification of Operator/Human Approval Bottlenecks ("Treo Hệ Thống")

Currently, the pipeline halts or hangs waiting for an operator in 7 specific code locations:

1. **`HandsPlanner.run_plan()`** (`scp/hands/planner.py:493-498` & DAG `:609-613, :764-768`):
   ```python
   requested_capability = max(int(capability_level), int(step.get("capabilityLevel", 0))) if token_is_valid else min(...)
   request_approved = bool(approved or step.get("approved", False))
   if requested_capability < definition.capability_level or (definition.requires_approval and not request_approved):
       step["state"] = "WAITING_APPROVAL"
       step["error"] = "Explicit approval or higher capability is required"
       plan["state"] = "WAITING_APPROVAL"
       self._save(plan, "PLAN_STEP_WAITING_APPROVAL", {"stepId": step["stepId"], "action": step["action"]})
       return {"success": False, "waitingApproval": True, "plan": self._public_plan(plan), ...}
   ```
   *Impact*: Whenever an action has `requires_approval: True` or needs `capability_level >= 1`, if the caller did not pre-set `approved=True` and provide matching capability level, the planner halts execution immediately.

2. **`ActionRegistry` Declarative Approval Requirements** (`scp/hands/action_registry.py:34-58`):
   8 of the 25 registered actions have `requires_approval = True`:
   - `pc.write_file`: capability level 3 (WORKSPACE), `requires_approval: True`.
   - `pc.process_start_managed`: capability level 3, `requires_approval: True`.
   - `pc.process_stop_owned`: capability level 4, `requires_approval: True`.
   - `web.dom_snapshot`: capability level 1, `requires_approval: True`.
   - `web.open_public_tab`: capability level 2, `requires_approval: True`.
   - `web.follow_public_link`: capability level 2, `requires_approval: True`.
   - `web.wait_for_text`: capability level 1, `requires_approval: True`.
   - `web.read_logged_in`: capability level 1, `requires_approval: True`.

3. **`PCController.evaluate()` & `write_file()` Approval Gates** (`scp/pc_control/pc_controller.py`):
   - In `evaluate()` (`lines 262-264`):
     ```python
     if not approved:
         return PolicyDecision(False, "Explicit approval required for workspace execution", "medium", True, int(level))
     ```
     Any command outside `READ_ONLY_PATTERNS` (even if in `WORKSPACE_PATTERNS`) returns `allowed=False` unless `approved=True`.
   - In `write_file()` (`lines 379-380`):
     ```python
     if self.kill_switch_engaged() or capability_level < CapabilityLevel.WORKSPACE or not approved:
         return {"success": False, "error": "Write requires capability >= 3 and explicit approval"}
     ```
   - In `rollback()` (`lines 435-436`): Requires `approved=True` and `capability_level >= 3`.

4. **`TaskKernel` State Machine Approval Gate** (`scp/task_kernel_parts/taskkernel.py:425-427, 1287-1375`):
   - Direct transition `WAITING_APPROVAL -> READY` is explicitly forbidden:
     ```python
     if old == "WAITING_APPROVAL" and to_state == "READY":
         raise InvalidTransition("direct transition from WAITING_APPROVAL to READY is forbidden; use commit_approval() with valid capability token")
     ```
   - Transitioning out of `WAITING_APPROVAL` requires calling `TaskKernel.commit_approval(task_id, approval_token)`.
   - `commit_approval()` verifies `approval_token` via `verify_approval_authority()`:
     Requires a valid HMAC-signed `CapabilityToken` or `mint_token` with scope `approval:grant` or `approval:grant:{task_id}`.

5. **`ask_kernel_adapter.py` Escalation to `HUMAN_REVIEW`** (`scp/ask_kernel_adapter.py:455-498, 638-642`):
   - Line 638: If `verification["verdict"] != "VERIFIED"`, it executes:
     `self._escalate_to_human_review(task_id, reason="ask_evidence_insufficient_or_contradicted", payload={"verification": verification})`
   - Lines 537-568 (`_stale_lifecycle_result`): If commit races or lease expires, task escalates to `HUMAN_REVIEW`.
   - The task is parked in `HUMAN_REVIEW` indefinitely awaiting human intervention.

6. **`TaskKernel` Recovery Escalation to `HUMAN_REVIEW`**:
   - `expire_leases()` (`taskkernel.py:821-822`): Expired leases on RUNNING/VERIFYING tasks sweep state to `HUMAN_REVIEW`.
   - `reconcile_unknown()` (`taskkernel.py:1049, 1055`): Reconciling with outcome `APPLIED` or `UNKNOWN` moves task to `HUMAN_REVIEW`.
   - `recover_on_boot()` (`taskkernel.py:1682, 1710`): In-flight tasks recover to `HUMAN_REVIEW`.

7. **`CapabilityManager` AI Escalation Denial** (`scp/meta/capability_levels.py:81-83`):
   ```python
   if actor == "ai" or actor == "system":
       logger.warning("[CapabilityManager] AI cannot self-escalate (Gà §12) — denied")
       return False
   ```
   If SCP runs at level < 3, automated attempts to escalate capability level programmatically are rejected.

---

### 1.4 Existing Capability & Token Test Suite Inventory

In `tests/T03_capability/` (57 files) and related suites:

| Test File | Primary Invariant / Requirement Tested |
|---|---|
| `tests/T03_capability/test_capability_token_hmac_signing.py` | GAP-08: Deterministic HMAC-SHA256 signing, constant-time validation, rejection of unsigned/tampered/wrong-key tokens with `InvalidTokenSignatureError`. |
| `tests/T03_capability/test_capability_secret_fail_closed.py` | GAP-09: Fail-closed requirement on `SCP_CAPABILITY_SECRET`. Constructor raises `MissingSecretError` if secret is missing or whitespace. |
| `tests/T03_capability/test_capability_token_mutation_contract.py` | Zero kernel mutation contract (`kernel-mutation-0` when denied). |
| `tests/T03_capability/test_pc_controller_token_pep.py` | R2 PEP enforcement in `PCController.execute`, `write_file`, `read_file`, `rollback`, `clear_kill_switch`, HTTP routes (`pc_controller_routes.py`). |
| `tests/T03_capability/test_hands_authority_pep.py` | FA-05 & INV-AUTH-02: `HandsExecutor` and `TaskKernelHandsBridge` reject missing tokens, scope mismatches, revoked epochs; `HandsPlanner` step-level token propagation. |
| `tests/T03_capability/test_api_token_boundary.py` | Header verification (`X-SCP-Capability-Token`, `X-SCP-PC-Token`) across FastAPI routers. |
| `tests/T03_capability/test_egress_enforcement.py` | SSRF prevention and outbound network boundaries. |
| `tests/T04_kernel/test_adversarial_kernel_flaws.py` | GAP-13: `WAITING_APPROVAL` direct transition bypass prevention (`commit_approval` requirement). |
| `tests/T04_kernel/test_gap13_adversarial_challenge.py` | Stress tests against unauthenticated approval bypass vectors. |
| `tests/T04_kernel/test_verifier_receipt_provenance.py` | Cryptographic signature enforcement on `VerifierReceipt` before `commit_verification_result`. |

---

## 2. Logic Chain

### 2.1 The Tension: Autonomous Operation vs. FA-05 & Zero-Trust
1. **The User Requirement (R2)**:
   The user mandates: *"Điều chỉnh cơ chế Capability Authority để nó tự động cấp phát hoặc xác thực hợp lệ các token (như network, fs_write, execution) trong luồng Autonomous, đảm bảo hệ thống không bị 'treo' chờ operator duyệt quyền."*
2. **The Hard Constraint (FA-05)**:
   Rule FA-05 states: *"KHÔNG self-grant authority. Executor không tự issue token. Caller phải cung cấp token đã được cấp bởi authority riêng biệt."*
   System Invariant: *"Knowledge/Reasoning/Risk -> Proposal -> Governance -> Execution"*.
   Agent OS Invariant: *"Any code modifications must explicitly enforce boundaries at the Database/Hardware level, not via RAM/Variables."*
3. **The Anti-Pattern (What MUST NOT be done)**:
   If we simply modify `HandsExecutor.execute()` or `PCController.execute()` so that when `capability_token is None`, the executor calls `self.capability_authority.issue(action)`:
   - This directly violates FA-05: The executor is self-granting authority.
   - It collapses PEP into PDP: If an attacker injects a command through a tool or prompt, the worker itself generates the capability token to execute it.
   - It violates Zero-Trust: There is zero independent lineage between decision and execution.
   - It violates the database/hardware boundary rule: The permission check becomes a trivial in-memory bypass.

### 2.2 Reconciling Autonomous Operation with FA-05: The Independent Autonomous Capability Authority
To maintain strict adherence to FA-05 while enabling fully autonomous execution:
1. **Separation of Lineage**:
   - **Worker / Executor (PEP)**: Remains strictly a Policy Enforcement Point (`HandsExecutor`, `PCController`). It possesses **NO ability to issue tokens** and has **no access to `SCP_CAPABILITY_SECRET`**. It continues to strictly reject any request missing an authorized token.
   - **Autonomous Capability Authority / Policy Governor (PDP & Issuer)**: Operates as an **independent authority subsystem** (e.g. `AutonomousCapabilityGovernor` or `AutonomousPolicyEngine`).
2. **Autonomous Policy Evaluation (Safe Automatic Granting)**:
   The Authority does NOT issue tokens blindly. Instead, when an Autonomous task or plan step requires capabilities, the Pipeline/Governor evaluates the request against objective, auditable invariant rules:
   - **Task Context & Liveness**: Is the request originating from a valid task registered in `TaskKernel`? Is the task in `PLANNING` or `RUNNING` state with a valid lease?
   - **Loop & Budget Safeguard**: Has the task's attempt count exceeded `max_attempts` (e.g. from `TaskKernel.create_task(..., max_attempts=3)` or `retry_policy.py`)? If attempts >= max_attempts, deny and fail closed.
   - **Sandboxing & Scope Rules**:
     - For `pc.execute`: The command must strictly match `WORKSPACE_PATTERNS`, must not contain shell chaining (`;&|` or `$()`), and must not match `BLOCKED_PATTERNS`.
     - For `pc.write_file`: The target file path must resolve strictly inside `working_dir` (`_inside_root`), and must not intersect `SENSITIVE_PARTS` (`.env`, secrets, private credentials).
     - For `web.*`: The URL must satisfy egress policy (no private IPs, no loopback SSRF, no credentials).
   - **Autonomous Risk Decision**: If all invariant rules pass, the Governor determines that operator approval is automatically granted under Autonomous Policy, sets `approved=True`, and issues an authenticated `CapabilityToken`.
3. **Token Delivery & Provenance**:
   - The Governor signs the token using `SCP_CAPABILITY_SECRET` with subject `hands:{action}` or `pc:{action}`.
   - The token is attached to the step execution payload (in `HandsPlanner` or `AgentOrchestrator`).
   - The Worker receives the token as a caller argument and passes it to the PEP.
   - The PEP validates the signature against `CapabilityAuthority.validate()`.
   - **Verdict**: FA-05 is 100% preserved. The Caller/Pipeline obtained the token from an independent Authority before handing it to the Executor. The Executor never issued its own token.

### 2.3 Resolving the State Machine Bottlenecks (R1 & R2 Interlock)
1. **Handling `WAITING_APPROVAL` in `HandsPlanner`**:
   - In `HandsPlanner.run_plan()`, when `step` requires approval:
     - In Autonomous Mode (`autonomous=True` or `SCP_AUTONOMOUS_MODE=1`):
       The planner delegates to `AutonomousCapabilityGovernor.evaluate_and_grant_step(step, plan)`.
       If approved, the Governor returns a valid `CapabilityToken`, sets `step["approved"] = True`, `step["capabilityLevel"] = definition.capability_level`, and logs the grant in the audit trail.
       Execution proceeds directly to `step["state"] = "RUNNING"` without halting at `WAITING_APPROVAL`.
     - If the Governor rejects the step (e.g. sensitive path or blocked command):
       The step transitions directly to `FAILED` with a deterministic error reason. It does NOT hang in `WAITING_APPROVAL`.
2. **Handling `WAITING_APPROVAL` in `TaskKernel`**:
   - In Autonomous Mode, tasks created by the orchestrator do not transition `PLANNING -> WAITING_APPROVAL`. They transition directly `PLANNING -> READY` once the plan is evaluated and verified.
   - If a task legitimately enters `WAITING_APPROVAL` (e.g. via external policy injection), the Autonomous Governor can invoke `TaskKernel.commit_approval(task_id, approval_token)` where `approval_token` is legitimately minted by the Authority with scope `approval:grant:{task_id}`.
3. **Handling `HUMAN_REVIEW` in `ask_kernel_adapter.py` and `TaskKernel`**:
   - In `ask_kernel_adapter.py:638`, when `verification["verdict"] != "VERIFIED"`:
     - In Autonomous Mode, instead of calling `_escalate_to_human_review`, the adapter transitions the task to `FAILED` (if unrecoverable) or `RETRY_SCHEDULED` (if `attempts < max_attempts`), or `COMPLETED` with an explicit fallback error response.
     - Tasks never enter `HUMAN_REVIEW`.
   - In `TaskKernel`:
     - When lease expires or recovery occurs: if `autonomous=True`, tasks are moved to `RETRY_SCHEDULED` or `FAILED` instead of `HUMAN_REVIEW`.

---

## 3. Caveats

1. **Destructive / High-Stakes Actions (Tier R3 / Critical)**:
   - Certain actions in `ActionRegistry` (such as `pc.clear_kill_switch` or destructive process stops `pc.process_stop_owned` with capability level 4) must remain strictly fail-closed. Autonomous granting must NOT automatically approve clearing the physical kill-switch or writing to sensitive root directories.
2. **Environment Variable Configuration**:
   - Autonomous mode should be governed by an explicit configuration (e.g. `SCP_AUTONOMOUS_MODE=1` or `autonomous: true` flag in Task metadata). When disabled, human approval gates must remain active to prevent unexpected regressions in standard supervised operation.
3. **T00 Meta-Audit Compliance (FA-01 to FA-13)**:
   - Existing tests in `tests/T03_capability/` and `tests/T04_kernel/` assert that unapproved or unauthenticated requests are rejected.
   - **Crucial**: Any implementation of Autonomous Mode MUST NOT loosen these assertions (FA-01) or skip tests (FA-02). Autonomous granting must be implemented by *providing validly issued tokens and verified approval metadata* to the existing strict PEPs, NOT by relaxing the PEPs to accept unapproved/unsigned calls.

---

## 4. Conclusion

1. **Root Cause of "Treo" / Hanging**:
   The system hangs because `HandsPlanner`, `PCController`, and `TaskKernel` have hardcoded checks for `approved == True` and `capability_token != None`, while the runtime lacks an automated Policy Governor to evaluate and issue these tokens programmatically for authorized autonomous tasks.
2. **Architectural Solution for R2**:
   Implement an independent **`AutonomousCapabilityGovernor`** (or `AutonomousCapabilityAuthority`) that:
   - Evaluates task safety, resource bounds, command regex, and loop attempt limits.
   - Mints authentic, HMAC-SHA256 signed `CapabilityToken` instances via `CapabilityAuthority.issue()`.
   - Injects the valid tokens into `HandsPlanner` plan steps and task execution parameters.
   - Allows the existing PEPs (`PCController._verify_token()`, `HandsExecutor._check_capability()`, `TaskKernel.commit_approval()`) to strictly validate the tokens and execute without human delay.
3. **FA-05 & Zero-Trust Compliance**:
   Because the Governor acts as an independent authority separate from the Executor, and all tokens are signed with cryptographic provenance and verified at the database/PEP level, FA-05 is completely upheld with zero self-granting by workers.

---

## 5. Verification Method

To independently verify the observations and validate future R2 implementation:

1. **Inspect Capability Authority & PEPs**:
   ```pwsh
   # View CapabilityAuthority implementation and token signing
   python -c "from scp.security.capability_epoch import CapabilityAuthority; auth = CapabilityAuthority('data/hands/capability_state.json', secret=b'0'*32); tok = auth.issue('hands:pc.write_file'); print(tok); assert auth.validate(tok, 'hands:pc.write_file')"
   ```
2. **Run Existing Capability & Token PEP Tests**:
   ```pwsh
   # Run full capability suite to confirm current baseline
   pytest tests/T03_capability/test_capability_token_hmac_signing.py tests/T03_capability/test_capability_secret_fail_closed.py tests/T03_capability/test_pc_controller_token_pep.py tests/T03_capability/test_hands_authority_pep.py -v
   ```
3. **Run Kernel Approval & Provenance Tests**:
   ```pwsh
   pytest tests/T04_kernel/test_verifier_receipt_provenance.py tests/T04_kernel/test_gap13_adversarial_challenge.py -v
   ```
4. **Run Meta-Audit Integrity Check**:
   ```pwsh
   python tools/t00_meta_audit.py
   ```
   *Expected outcome*: 0 regressions, all existing invariants intact.
