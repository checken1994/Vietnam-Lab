# Project: SCP Task Kernel Autonomous Mode

## Architecture
Autonomous Mode enables SCP to operate 24/7 without manual operator approval bottlenecks, while strictly maintaining Zero-Trust, Fail-Closed, and FA-01 through FA-13 invariants.

Key Architectural Principles:
1. **Zero-Trust State Progression (R1)**:
   - Preserves all 17 core lifecycle states and the strict transition table in `TaskKernel`.
   - In Autonomous Mode (`SCP_AUTONOMOUS_MODE=1` or `autonomous_mode=True`), successful execution and automated verification (`VERIFIED`) proceed directly to `COMPLETED` via cryptographic `VerifierReceipt`.
   - Verification failures fail-closed to `FAILED` (or `RETRY_SCHEDULED` within retry budgets) rather than stalling in `HUMAN_REVIEW`.
   - Any asynchronous race into `HUMAN_REVIEW` (such as watchdog lease expiration during slow external calls) is automatically reconciled by advancing `HUMAN_REVIEW -> READY` when valid verification evidence is proven.
2. **Independent Autonomous Capability Governance (R2 - FA-05 Compliance)**:
   - Enforces strict separation of concerns: The executor (`HandsExecutor`, `PCController`) remains a Policy Enforcement Point (PEP) and **never self-issues tokens** (strictly adhering to FA-05).
   - An independent `AutonomousCapabilityGovernor` evaluates autonomous plan steps against safety invariants (workspace sandboxing, sensitive file blacklists, command regex allowlists, loop retry limits).
   - Upon successful evaluation, the Governor issues cryptographically signed HMAC-SHA256 tokens via `CapabilityAuthority.issue()` and sets `approved=True` on the plan step before dispatching to the executor.
3. **Rigorous Empirical Verification (R3)**:
   - Dedicated integration test proving end-to-end task completion with zero visits to `HUMAN_REVIEW` in SQLite event journal.
   - Zero test deletion, zero assertion loosening, zero test skipping (FA-01, FA-02).
   - 100% PASS across core test suites and 0 regressions in `tools/t00_meta_audit.py`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Autonomous State Machine Progression | Direct progression from VERIFYING to COMPLETED with signed VerifierReceipt in AskKernelAdapter without entering HUMAN_REVIEW | M1 | Survey 1 |
| 2 | Fail-Closed Autonomous Error Routing | Route verification failures and lease expirations to FAILED or RETRY_SCHEDULED in autonomous mode instead of parking in HUMAN_REVIEW | M1 | Survey 1 |
| 3 | Autonomous Capability Governor | Independent PDP and authority to evaluate autonomous plan steps and issue signed HMAC-SHA256 CapabilityToken instances | M2 | Survey 2 |
| 4 | HandsPlanner Autonomous Approval Integration | Auto-evaluate and inject capability tokens into HandsPlanner steps, bypassing WAITING_APPROVAL halts | M2 | Survey 2 |
| 5 | End-to-End Autonomous Lifecycle Integration Test | Integration test proving a complete task lifecycle without any HUMAN_REVIEW state entries | M3 | Survey 3 |
| 6 | Full Core Suite & Meta-Audit Zero-Regression Verification | Verify full pytest suite passes and tools/t00_meta_audit.py reports 0 regressions | M3 | Survey 3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: Autonomous State Machine & Adapter | `scp/ask_kernel_adapter.py`, `scp/task_kernel_parts/taskkernel.py` | none | IN_PROGRESS |
| 2 | M2: Autonomous Capability Governor & Token Dispatch | `scp/security/autonomous_governor.py`, `scp/hands/planner.py`, `scp/pc_control/pc_controller.py` | M1 | PLANNED |
| 3 | M3: End-to-End Integration Test & Full Verification | `tests/T04_kernel/test_autonomous_state_machine_lifecycle.py`, `pytest tests/`, `tools/t00_meta_audit.py` | M1, M2 | PLANNED |

## Interface Contracts
### AutonomousGovernor ↔ HandsPlanner
- Function: `AutonomousCapabilityGovernor.evaluate_and_grant_step(step: dict, plan: dict, working_dir: str) -> tuple[bool, Optional[CapabilityToken], str]`
- Inputs:
  - `step`: Dict containing `action`, `params`, `capabilityLevel`, `approved`.
  - `plan`: Dict containing plan metadata, `task_id`, `state`.
  - `working_dir`: Resolved workspace path.
- Outputs:
  - `granted`: bool. True if safety invariants are satisfied.
  - `token`: `CapabilityToken` (HMAC-SHA256 signed by `CapabilityAuthority`) or None.
  - `reason`: Explanation of approval or rejection.
- Invariants:
  - Must reject commands matching `BLOCKED_PATTERNS` or paths outside `working_dir`.
  - Must never self-issue inside the executor; tokens are injected into `step["capabilityToken"]` prior to executor dispatch.

### AskKernelAdapter ↔ TaskKernel (Autonomous Mode)
- Env Var: `SCP_AUTONOMOUS_MODE` ("1", "true", "yes") or constructor flag `autonomous_mode: bool = False`.
- Invariants:
  - When `autonomous_mode=True` and `verdict == "VERIFIED"`: Atomically commit `COMPLETED` via `commit_verification_result`.
  - If raced to `HUMAN_REVIEW` by watchdog: Auto-transition `HUMAN_REVIEW -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED`.
  - When `verdict != "VERIFIED"`: Transition to `FAILED` (fail-closed) rather than `HUMAN_REVIEW`.

## Code Layout
- `scp/ask_kernel_adapter.py`: Autonomous state machine handling, verification commit, and failure fail-closed routing.
- `scp/task_kernel_parts/taskkernel.py`: Autonomous lease expiry routing, reconciliation outcome routing, and `auto_resolve_human_review`.
- `scp/security/autonomous_governor.py`: Independent Governor for evaluating autonomous safety and issuing HMAC-signed capability tokens.
- `scp/hands/planner.py`: HandsPlanner integration to consult `AutonomousCapabilityGovernor` for step capability grants.
- `tests/T04_kernel/test_autonomous_state_machine_lifecycle.py`: Dedicated integration test proving full autonomous lifecycle without `HUMAN_REVIEW`.
