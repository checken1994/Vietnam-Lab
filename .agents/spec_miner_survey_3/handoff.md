# SPECIFICATION MINING REPORT: SCP Invariants, T00 Meta-Audit Authority & Test Suite Architecture (R3 Support)

**Working Directory**: `c:\Users\check\Downloads\scp\.agents\spec_miner_survey_3`  
**Authoritative Request**: `c:\Users\check\Downloads\scp\.agents\ORIGINAL_REQUEST.md` (Autonomous Mode, Requirement R3)  
**Applicable Skills & Principles**: `scp-dna` (29 Core Principles, Zero-Trust, Fail-Closed, Reality > Model, PASS ≠ TRUE)  
**Auditor**: Specification Miner (Survey 3)  
**Timestamp**: 2026-09-20T14:21:00+07:00  

---

## 1. Observation

### 1.1 T00 Meta-Audit Tool & Guardrails (`tools/t00_meta_audit.py`, `tools/install_git_hooks.py`, `.github/workflows/scp_guardrails.yml`)
- **Direct AST Inspection & Visitor Rules (`tools/t00_meta_audit.py:75-149`)**:
  - AST `AuditVisitor` inspects `FunctionDef` and `AsyncFunctionDef` decorator lists: detects any decorator matching `('skip', 'xfail', 'skipif')` regardless of whether invoked as `ast.Attribute` (e.g. `@pytest.mark.skip`) or `ast.Call` (e.g. `@pytest.mark.skipif(condition)`).
  - Module- and class-level `pytestmark` assignment checking (`_check_pytestmark_target`, lines 124-130, 131-143): inspects `Assign`, `AnnAssign`, and `AugAssign` AST nodes where target is `pytestmark`. Catches full-file and class-wide skips that evade function-level scans.
  - Function call checking (`visit_Call`, lines 144-148): detects `pytest.skip()`, `pytest.xfail()`, and `pytest.importorskip()`.
  - Signature tracking (`get_fa01_signatures`, line 150): extracts exact violation signatures. Compares against baseline (`origin/main`): new violations are added to `all_new_violations` and trigger fail-closed exit code 1; matching historical violations are categorized as non-blocking `BASELINE_DEBT`.
- **Manufactured Green & Simulated Verification Regex Checks (`tools/t00_meta_audit.py:160-170`)**:
  - Scans all files under `scp/` with `.py` extension.
  - Flags any occurrence matching:
    1. `re.search(r'["\']simulated\s+verifi(ed|cation)["\']', content, re.IGNORECASE)`
    2. `re.search(r'return\s*\{.*["\']status["\'].*["\']VERIFIED["\']', content, re.IGNORECASE)`
  - Deltas against baseline trigger `FA-04: <path> -> <sig> (<count> new instances)`.
- **Collected Test NodeID Deletion Detection (`tools/t00_meta_audit.py:203-314`)**:
  - Invokes `pytest tests/ scp/tests/ --collect-only -q` on candidate branch and in a temporary git worktree checking out `origin/main`.
  - Compares nodeids sets: `missing = filtered_b_nodeids - c_nodeids`.
  - If any test was deleted without architectural deprecation allowlisting, reports `FA-02: Deleted test nodeid: <nodeid>` and exits 1.
- **Stale-Code Tripwire Extension (`tools/t00_meta_audit.py:330-357`, `tools/stale_code_tripwire.py`)**:
  - Enforces 4 checks:
    1. *Blueprint-vs-code*: modules `scp.knowledge.warehouse`, `scp.autofix.evidence_replay`, `scp.meta.reverify_scheduler`, `scp.core.free_discovery_scheduler` must exist on disk and be importable.
    2. *Unresolved import*: all `from scp.X import Y` and `import scp.X` must resolve to real files and symbols on disk.
    3. *Duplicated logic*: judge prompt templates (>=120 chars, markers `Question:`, `Context:`, `AI Answer:`) duplicated across >=2 files FAIL; function bodies >20 lines duplicate WARN.
    4. *Metric drift*: AST count of `print()` calls and silent `except: pass` in `scp/**` must not increase > 5% over `data/governance/tripwire_baseline.json`.
- **Pre-commit and CI Wire-up**:
  - `tools/install_git_hooks.py:12-22`: writes `.git/hooks/pre-commit` running `python tools/t00_meta_audit.py`. Non-zero exit blocks git commit.
  - `.github/workflows/scp_guardrails.yml:22-30`: CI runs on `push` and `pull_request`, running `python tools/t00_meta_audit.py` and `pytest tests/ scp/tests/ --collect-only`.
- **Runtime Execution Result (`python tools/t00_meta_audit.py`)**:
  - Command: `python tools/t00_meta_audit.py`
  - Exit code: `0`
  - Output summary:
    - `[T00 Meta-Audit] All integrity checks passed (0 new regressions).`
    - Baseline debt observed: 12 historical FA-01 instances (e.g. PG test fixtures, Playwright browser test skips), 1 historical FA-04 instance (`scp/autofix/evidence_replay.py`).

---

### 1.2 Specifications & Invariants (`spec/protected_invariants.yaml`, `spec/complete_scp_reference.yaml`, `spec/scp_future_target_manifest.yaml`, overlay)
- **`spec/protected_invariants.yaml:3-65`**:
  - Defines 10 protected invariant scopes requiring explicit governance:
    - `scp.dna` (`.agents/skills/scp-dna/**`)
    - `complete_scp.reference` (`spec/complete_scp_reference.yaml`)
    - `test.integrity` (`tests/**`, `pyproject.toml`, `spec/scp_target_test_coverage.yaml`, `tools/verify_scp_future_target.py`, `tools/verify_scp_target_test_coverage.py`)
    - `capability.pep` (`scp/security/capability_epoch.py`, `scp/hands/hands_executor.py`)
    - `secret.boundary` (`scp/security/auth.py`, `scp/security/auth_config.py`, `scp/security/production_guard.py`)
    - `egress.boundary` (`scp/llm_gateway/egress_policy.py`)
    - `evidence.authority` (`scp/epistemic/evidence_store.py`)
    - `reality.authority` (`scp/verifier.py`)
    - `runtime.kill_rollback` (`scp/task_kernel.py`, `scp/task_kernel_parts/**`, `scp/autofix/**`)
    - `release.authority` (`.github/workflows/**`, `tools/verify_scp_test_skill_contract.py`, `.agents/skills/release-gate-skill-dna-bindings.json`)
- **`spec/complete_scp_reference.yaml:1-106`**:
  - Defines principles: `reality_over_model`, `pass_not_equal_true`, `fail_closed_unknown`, `external_data_untrusted`, `independent_lineage_required`, `reversible_change_required`.
  - Authoritative verdicts: `VERIFIED`, `CONTRADICTED`, `INSUFFICIENT`, `UNKNOWN`.
  - Evidence levels: `A: static`, `B: integration`, `C: end_to_end`, `D: recovery`.
  - Required capability minimum maturities: `execution.task_kernel: D`, `execution.capability_security: D`.
- **`spec/scp_future_target_manifest.yaml` & Overlay (`v4_0_2.overlay.json:715-737`)**:
  - Target Architecture 4.0.2 contracts:
    - `task_kernel_contract`: 8 required semantics (`task_identity_owner_deadline_version_risk`, `guarded_state_machine`, `append_only_journal`, `rebuildable_projection`, `ttl_heartbeat_fencing_lease`, `checkpoint_hash_and_secret_exclusion`, `logical_action_idempotency`, `UNKNOWN_no_new_side_effect`).
    - `terminal_states`: `COMPLETED`, `FAILED`, `CANCELLED`.
    - `uncertain_states`: `UNKNOWN`, `RECOVERING`, `HUMAN_REVIEW`, `RECONCILING`.
- **`release-gate-skill-dna-bindings.json:1-90`**:
  - Binds 14 mandatory release gates and 1 handoff gate to DNA principles (specifically DNA #22 and #26) and specialized skills.
  - Lists 11 strictly forbidden shortcuts: `delete_test`, `skip_test`, `xfail_test`, `loosen_assertion`, `lower_threshold`, `lower_coverage`, `lower_security_policy`, `lower_mutation_score`, `drop_acceptance_gate`, `ignore_exit_code`, `fail_open_instead_of_fail_closed`.

---

### 1.3 TaskKernel State Machine & Autonomous Execution Flow
- **17 Lifecycle States (`scp/task_kernel.py:16-42`)**:
  - States: `CREATED`, `PLANNING`, `READY`, `QUEUED`, `LEASED`, `RUNNING`, `WAITING_TOOL`, `VERIFYING`, `CHECKPOINTED`, `UNKNOWN`, `RECOVERING`, `RECONCILING`, `HUMAN_REVIEW`, `RETRY_SCHEDULED`, `COMPLETED`, `FAILED`, `CANCELLED`.
  - Terminal states: `COMPLETED`, `FAILED`, `CANCELLED`.
  - Key transitions:
    - `PLANNING -> {READY, WAITING_APPROVAL, FAILED, CANCELLED}`
    - `WAITING_APPROVAL -> {READY, CANCELLED}` (direct transition via `kernel.transition` is explicitly blocked: requires `commit_approval` with HMAC token).
    - `RUNNING -> {WAITING_TOOL, VERIFYING, CHECKPOINTED, RECOVERING, HUMAN_REVIEW, FAILED, CANCELLED}`
    - `VERIFYING -> {RUNNING, COMPLETED, HUMAN_REVIEW, FAILED}`
    - `HUMAN_REVIEW -> {READY, CANCELLED, FAILED}`
- **Verification & Completion Path (`scp/task_kernel_parts/taskkernel.py:1164-1285`)**:
  - Direct transition to `COMPLETED` is valid **ONLY** from state `VERIFYING` via `commit_completed(task_id, lease_id, ...)` or `commit_verification_result(task_id, lease_id, receipt)`.
  - Must supply a cryptographically valid `VerifierReceipt` signed with HMAC-SHA256 (`scp/core/verifier_receipt.py`).
- **Why Tasks End Up in `HUMAN_REVIEW` in Current System**:
  1. *Risk Policy*: High-risk planning actions transition `PLANNING -> WAITING_APPROVAL`, requiring operator approval.
  2. *Verification Failure*: In `scp/ask_kernel_adapter.py:609-642`, if `verify_response()` returns `INSUFFICIENT` or `CONTRADICTED`, `_escalate_to_human_review()` moves the task to `HUMAN_REVIEW`.
  3. *Lease Expiry Watchdog*: `expire_leases()` (`taskkernel.py:821-822`) sweeps in-flight tasks in `RUNNING` or `VERIFYING` whose lease TTL expired into `HUMAN_REVIEW`.
  4. *Boot Recovery*: `reconcile()` (`taskkernel.py:1710`) sweeps unverified in-flight tasks upon reboot into `HUMAN_REVIEW`.
  5. *Reconciliation Outcome*: `reconcile_outcome()` (`taskkernel.py:1049, 1055`) moves task to `HUMAN_REVIEW` when outcome is `APPLIED` or `UNKNOWN`.

---

### 1.4 Test Suite Architecture & Invocation (`tests/`)
- **Gate Taxonomy**:
  - `tests/T00_integrity`: Meta-audit AST checks, declared infra-skips allowlist validation, target coverage authority, stale code tripwire.
  - `tests/T01_boot`: Startup health, configuration loading, process bootstrapping.
  - `tests/T02_contract`: External API contracts, WebSocket messaging, schema validations.
  - `tests/T03_capability`: Capability token issuance, verification, PEP enforcement, PCController boundary, egress filtering, sandbox isolation.
  - `tests/T04_kernel`: TaskKernel durability, state transitions, lease fencing, verifier receipts provenance, OCC version checks, PG event bus.
  - `tests/T05_gateway`: Outbound LLM gateway, circuit breakers, multi-provider failover, timeout racing.
  - `tests/T06_verifier`: Automated verifiers, RAG evaluations, contradiction detection.
  - `tests/T07_learning`: Continuous learning loop, AutoFix engine, shadow snapshot rollback.
  - `tests/T08_runtime`: Physical port bindings, daemon health probes, live service auditing.
  - `tests/T09_golden_task`: End-to-end integration and recovery benchmarks.
  - `tests/T10_recovery`: Chaos testing, crash consistency, lease reclaims.
  - `tests/T11_release`: Release candidate workflows, manifest verification, customer handoff gates.
- **Test Invocation Mechanisms**:
  - Full Core Test Suite: `python -m pytest tests/`
  - Subsystem Focused: `python -m pytest tests/T04_kernel tests/T03_capability -q`
  - Meta-Audit Tripwire: `python tools/t00_meta_audit.py`
  - Portable Reality Suite: `python scripts/run_reality_tests_portable.py`
  - Mandatory Gates Contract: `python tools/verify_scp_test_skill_contract.py`
  - Declared Infra-Skip Allowlist: `tests/T00_integrity/declared_infra_skips.json` (strictly pins allowed skips).

---

## 2. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Meta-Audit | AST Decorator Skip & Xfail Detection | Detects `@pytest.mark.skip`, `@pytest.mark.xfail`, `@pytest.mark.skipif` across all tests | Python AST of test files in `tests/`, `scp/tests/` | Counter of decorator occurrences | Deltas vs baseline block with exit code 1 (FA-01) | `tools/t00_meta_audit.py:80-105` |
| 2 | Meta-Audit | `pytestmark` Module/Class Skip Audit | Detects whole-file or class-level silent skips via assignment to `pytestmark` | Assign, AnnAssign, AugAssign AST nodes | Counter of `pytestmark {mark}` occurrences | Deltas vs baseline block with exit code 1 (FA-01) | `tools/t00_meta_audit.py:124-143` |
| 3 | Meta-Audit | AST `pytest.skip()` Call Audit | Scans for direct programmatic calls to `pytest.skip()`, `pytest.xfail()`, `pytest.importorskip()` | AST Call nodes targeting `pytest.<attr>` | Counter of call instances | Deltas vs baseline block with exit code 1 (FA-01) | `tools/t00_meta_audit.py:144-148` |
| 4 | Meta-Audit | Collected NodeID Deletion Detection | Verifies no tests were deleted or dropped between `trusted_base` worktree and candidate | NodeID set from `pytest --collect-only -q` | Missing nodeids difference set | Any missing nodeid blocks commit with exit code 1 (FA-02) | `tools/t00_meta_audit.py:203-314` |
| 5 | Meta-Audit | Manufactured Green Regex Audit | Scans production code for simulated verification or hardcoded unconditional VERIFIED stubs | Lines in `scp/**/*.py` | Counter of regex matches | New instances block commit with exit code 1 (FA-04) | `tools/t00_meta_audit.py:160-170` |
| 6 | Meta-Audit | L4 Protected Path Modifications Warning | Detects modifications to governance specs, tests, and CI workflows | Staged/modified git paths | L4 Codeowners warning string | Local warning emitted; enforced server-side | `tools/t00_meta_audit.py:315-328` |
| 7 | Meta-Audit | Blueprint-vs-Code Existence Tripwire | Ensures core blueprint modules exist on disk and are importable | Dotted module names in `BLUEPRINT_MODULES` | Existence & import check | Missing/unimportable module blocks with exit code 1 | `tools/stale_code_tripwire.py:83-88` |
| 8 | Meta-Audit | Unresolved Import Detection Tripwire | Resolves all `scp.*` module and symbol imports against actual disk AST | Import & ImportFrom AST nodes in `scp/**` | Symbol resolution report | Unresolvable module/symbol blocks with exit code 1 | `tools/stale_code_tripwire.py:165-220` |
| 9 | Meta-Audit | Duplicated Judge Prompt & Body Tripwire | Detects duplicated prompt templates and identical function bodies | String constants & AST function dumps | Duplicate findings list | Duplicate judge prompt literal across >=2 files blocks | `tools/stale_code_tripwire.py:59-66` |
| 10 | Meta-Audit | Metric Drift Tripwire | Enforces <=5% drift on bare `print()` calls and silent `except: pass` handlers | AST Call and ExceptHandler counts | Percentage drift vs `tripwire_baseline.json` | Drift > 5% blocks commit with exit code 1 | `tools/stale_code_tripwire.py:518-616` |
| 11 | Test Integrity | Declared Infra-Skip Contract & Allowlist Pinning | Restricts allowable skips to reviewed infrastructure dependencies | `declared_infra_skips.json` allowlist | Verified skip records | Undeclared skip or stale entry raises AssertionError | `tests/T00_integrity/test_meta_audit.py:1-260` |
| 12 | Test Integrity | Architectural Gate Completeness Audit | Enforces presence of all 12 architectural gate directories (`T00`..`T11`) | Directory list in `tests/` | Presence assertion | Missing directory raises AssertionError | `tests/T00_integrity/test_meta_audit.py:489-496` |
| 13 | Test Integrity | Nonexistent Bare SCP Import Prohibition | Catches invented module harnesses in tests | Bare `import scp.*` in `tests/` | Spec finding & import verification | Unresolvable bare import raises AssertionError | `tests/T00_integrity/test_meta_audit.py:512-557` |
| 14 | Specification | Protected Invariants Spec | Machine-readable registry of 10 governance-protected subtrees | Path patterns in `spec/protected_invariants.yaml` | Policy `REQUIRE_GOVERNANCE` | Direct modification requires governance review | `spec/protected_invariants.yaml:3-65` |
| 15 | Specification | Complete SCP Reference | Normative definitions of principles, verdicts, evidence levels, maturities | Principles & capabilities in `complete_scp_reference.yaml` | Reference baseline | Implementation detail in spec is forbidden by T00 | `spec/complete_scp_reference.yaml:1-106` |
| 16 | Specification | Future Target Manifest 4.0.2 | Authoritative composed target architecture (138 capabilities, 67 edges) | Base 4.0.1 + overlay v4_0_2 | Composed matrix | Duplicate ID or broken reference errors out | `spec/scp_future_target_manifest.yaml:1-67` |
| 17 | TaskKernel | 17-State Atomic State Machine | Atomic state transitions guarded by OCC version checks and event journal | Task ID, target state, lease ID, version | Updated task record + event row | Illegal transition raises `InvalidTransition`; conflict raises `OptimisticLockError` | `scp/task_kernel.py:16-42` |
| 18 | TaskKernel | Approval Authority Gate (`commit_approval`) | Validates signed approval tokens to transition `WAITING_APPROVAL -> READY` | Approval token, task ID, actor | Updated task in `READY` state | Missing/tampered/expired token raises `InvalidTokenSignatureError` | `scp/task_kernel_parts/taskkernel.py:1287-1350` |
| 19 | TaskKernel | Cryptographic Verifier Receipt Gate | Enforces HMAC-signed receipt before committing state `COMPLETED` | Task ID, lease ID, `VerifierReceipt` | Updated task in `COMPLETED` state | Invalid signature or non-VERIFYING state raises error | `scp/task_kernel_parts/taskkernel.py:1164-1285` |
| 20 | Task Execution | AskKernelAdapter Autonomous Finalization | Routes response through automated verifier; completes task on `VERIFIED` | Task dict, response object, request | Safe response + updated task | Verification failure routes to `HUMAN_REVIEW` | `scp/ask_kernel_adapter.py:499-650` |
| 21 | TaskKernel | Lease Expiry Watchdog Sweep | Reclaims expired leases and sweeps unverified in-flight tasks to `HUMAN_REVIEW` | Expired leases in SQLite | State update to `HUMAN_REVIEW` | Heartbeat loss fail-closed prevents zombie tasks | `scp/task_kernel_parts/taskkernel.py:821-825` |
| 22 | Capability | CapabilityAuthority Epoch-Based Revocation | Manages tool execution permissions with cryptographic HMAC tokens | Subject, state file, capability secret | Signed `CapabilityToken` | Expired epoch raises `CapabilityRevokedError`; bad token raises `InvalidTokenSignatureError` | `scp/security/capability_epoch.py:94-265` |

---

## 3. Edge Cases

| # | Feature | Input / Condition | Observed Behavior |
|---|---------|-------------------|-------------------|
| 1 | T00 Meta-Audit Delta | Historical skip present in `origin/main` | Tracked as non-blocking `BASELINE_DEBT`; T00 outputs `[DEBT]` and passes with exit code 0. |
| 2 | T00 Meta-Audit Delta | Newly added `@pytest.mark.skip` or `pytest.skip()` | Flagged as `FA-01 new instance`; T00 fails closed with exit code 1. |
| 3 | T00 Test Deletion | Single test function removed from a test file | Flagged as `FA-02: Deleted test nodeid: tests/...::test_name`; T00 fails closed with exit code 1. |
| 4 | T00 Test Collection | Syntax or unhandled import error in any test file | Pytest collection fails with non-zero exit code; T00 fails closed with error message. |
| 5 | T00 Manufactured Green | New line containing `'status': 'VERIFIED'` in `scp/` | Regex detects manufactured green; flagged as `FA-04 new instance` and blocks commit. |
| 6 | Declared Infra-Skip Gate | `pytest.skip()` inside allowlisted file without matching declared reason | `test_meta_audit.py` fails with AssertionError: skip reason does not match allowlist pattern. |
| 7 | Declared Infra-Skip Gate | Allowlisted file no longer contains declared `env_guard_token` | `test_meta_audit.py` fails with AssertionError: env guard token was stripped away. |
| 8 | Declared Infra-Skip Gate | Skip removed from allowlisted file but entry retained in JSON | `test_meta_audit.py` fails with AssertionError: stale declared infra-skip allowlist entry. |
| 9 | TaskKernel Transition | Direct transition `WAITING_APPROVAL -> READY` via `transition()` | Fails closed with `InvalidTransition: direct transition from WAITING_APPROVAL to READY is forbidden; use commit_approval()`. |
| 10 | TaskKernel Transition | Direct call `commit_completed()` when task is in `RUNNING` or `HUMAN_REVIEW` | Fails closed with `InvalidTransition: <state>->COMPLETED`; tasks can only complete from `VERIFYING`. |
| 11 | TaskKernel Transition | Calling `commit_completed()` with tampered or unsigned `VerifierReceipt` | Fails closed with `InvalidReceiptSignatureError` (HMAC verification fails). |
| 12 | TaskKernel Transition | Calling `commit_approval()` with expired capability token | Fails closed with `InvalidTransition: Capability token has expired`. |
| 13 | AskKernelAdapter | Concurrent lease expiry sweeping task to `HUMAN_REVIEW` while `finalize()` executes | Handled idempotently without HTTP 500 (`HUMAN_REVIEW -> HUMAN_REVIEW` is caught and treated as safe no-op). |
| 14 | Stale-Code Tripwire | Adding bare `print()` calls in `scp/**` exceeding 5% of baseline count | `stale_code_tripwire.py` flags `metric_drift: print_calls: X > baseline Y + 5%` and fails closed. |
| 15 | Stale-Code Tripwire | Adding a bare `from scp.unknown import nonexistent_func` in `scp/**` | `stale_code_tripwire.py` flags `unresolved_import` or `missing_module` and fails closed. |
| 16 | Stale-Code Tripwire | Adding identical prompt templates containing judge markers to 2 distinct files | `stale_code_tripwire.py` flags `duplicate_prompt_template` and fails closed. |

---

## 4. Logic Chain

1. **Premise 1 (User Request & Objective)**: The user request requires Autonomous Mode for the SCP Task Kernel, ensuring a task proceeds end-to-end without being blocked in `HUMAN_REVIEW`, while maintaining 100% test pass rates and 0 regressions in `tools/t00_meta_audit.py` (Requirement R3).
2. **Premise 2 (T00 Meta-Audit Enforcement Rigor)**: From our inspection of `tools/t00_meta_audit.py`, `tests/T00_integrity/test_meta_audit.py`, and `tools/stale_code_tripwire.py`, T00 enforces zero new test skips (FA-01), zero test deletions (FA-02), zero manufactured green returns (FA-04), and strict drift limits (<5% on `print` and `except: pass`). Furthermore, `tests/T00_integrity/test_meta_audit.py` fails if any mandatory test is skipped without a declared entry in `declared_infra_skips.json`.
3. **Premise 3 (State Machine Invariant Protection)**:
   - In `scp/task_kernel.py:16-42`, `STATES` contains 17 states including `HUMAN_REVIEW`.
   - Existing adversarial tests (`tests/T04_kernel/test_gap13_state_machine_boundaries.py`, `test_gap13_adversarial_challenge.py`, `test_adversarial_kernel_flaws.py`) exhaustively attack all 17 states.
   - Deleting `HUMAN_REVIEW` from `STATES` or `ALLOWED_TRANSITIONS` would cause dozens of existing tests to crash with `KeyError` or unexpected `InvalidTransition`, resulting in massive test suite failure.
4. **Premise 4 (Happy Path Does Not Require HUMAN_REVIEW)**:
   - In `TaskKernel`, `ALLOWED_TRANSITIONS["VERIFYING"]` already contains `COMPLETED`.
   - In `AskKernelAdapter:609-625`, when `verify_response()` succeeds (`VERIFIED`), it signs a `VerifierReceipt` and calls `commit_verification_result()`, transitioning directly from `VERIFYING -> COMPLETED`.
   - Tasks land in `HUMAN_REVIEW` only when an error, unverified output, lease expiry, or risk gate occurs.
5. **Premise 5 (Zero-Trust & FA-05 Compliance for Capability Tokens)**:
   - Under FA-05, an execution component cannot self-grant capability tokens.
   - For Autonomous Mode (Requirement R2), automatic granting of capabilities must be performed by a dedicated autonomous policy authority (e.g. `CapabilityAuthority` or `AutonomousBroker`), minting properly signed HMAC-SHA256 tokens at the DB/Hardware boundary, rather than bypassing PEP checks in memory.
6. **Deduction & Invariant Guardrail Strategy**:
   - Therefore, implementing Autonomous Mode without violating R3 requires:
     a) Preserving all 17 states and existing transition tables in `TaskKernel`.
     b) Providing an autonomous execution path where tasks satisfy automated verification and automated approval criteria, transitioning seamlessly `PLANNING -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED`.
     c) Ensuring no tests are deleted, modified to loosen assertions, or skipped.
     d) Maintaining strict AST compliance so `python tools/t00_meta_audit.py` returns 0 regressions.

---

## 5. Precise Acceptance Requirements & Regression Prevention Guidelines

### 5.1 Acceptance Criteria Formulation for R3
1. **[AC-R3-1: State Machine Lifecycle Integration Test]**:
   - A dedicated integration test (under `tests/T04_kernel/` or `tests/T09_golden_task/`) must programmatically instantiate `TaskKernel` (and/or `AskKernelAdapter`), submit an autonomous task, drive it through its complete lifecycle (`CREATED -> PLANNING -> READY -> QUEUED -> LEASED -> RUNNING -> VERIFYING -> COMPLETED`), and physically verify in the SQLite database that:
     - The task reaches `state = 'COMPLETED'`.
     - At no point in the event journal (`events` table) was state `HUMAN_REVIEW` recorded.
     - The verifier receipt provenance is cryptographically valid and recorded in the completion event.
2. **[AC-R3-2: System Integrity Core Test Suite Pass]**:
   - Running `pytest tests/` (specifically all core gates `T00_integrity`, `T01_boot`, `T02_contract`, `T03_capability`, `T04_kernel`, `T05_gateway`, `T06_verifier`, `T07_learning`) must achieve 100% PASS with 0 failures and 0 errors.
   - No pre-existing tests may be broken or modified to accept looser assertions.
3. **[AC-R3-3: T00 Meta-Audit Zero Regressions]**:
   - Running `python tools/t00_meta_audit.py` must produce:
     - `exit code: 0`
     - Terminal output: `[T00 Meta-Audit] All integrity checks passed (0 new regressions).`
     - Zero new FA-01, FA-02, FA-04, or tripwire violations.

### 5.2 Regression Prevention Guidelines
- **Guideline 1 (Preserve State Definitions)**: Never delete `HUMAN_REVIEW` from `STATES` or `ALLOWED_TRANSITIONS` in `scp/task_kernel.py`. Autonomous Mode must be implemented as an autonomous progression policy, not by dismantling the safety fallback states.
- **Guideline 2 (Cryptographic Integrity Over Bypass)**: Never bypass token checks with stubs or booleans. When automatically granting approval for autonomous tasks, issue a cryptographically signed token via `CapabilityAuthority.issue()` or `mint_token()` using `get_capability_secret()`.
- **Guideline 3 (Zero Test Loosening - FA-01)**: Never add `@pytest.mark.skip`, `@pytest.mark.xfail`, or `pytest.skip()` to tests. If an external dependency is unavailable, it must already be in `declared_infra_skips.json` with an exact reason pattern and env guard.
- **Guideline 4 (Zero Test Deletion - FA-02)**: Never delete any test file or test method. T00 compares collected nodeids against git baseline and fails immediately on missing nodeids.
- **Guideline 5 (Tripwire Drift Control)**: Do not add debug `print()` statements in `scp/` code (use `logger.info` or `logger.debug`). Do not use silent `except: pass` constructs. Ensure all imports in `scp/` resolve to real modules and symbols on disk.

---

## 6. Caveats

1. **Baseline Debt**: The repository currently contains 12 historical FA-01 instances (mostly PostgreSQL integration tests requiring external database credentials and Playwright tests requiring browser binaries) and 1 historical FA-04 instance. These are tracked as non-blocking `BASELINE_DEBT` by T00, but any new additions will immediately cause T00 to fail.
2. **L4 Protected Paths**: Modifying files in `spec/` or `.github/` triggers local L4 warnings. In GitHub CI, changes to these paths are enforced by GitHub Server-Side Rulesets.
3. **Scope Boundary**: This report performs pure specification and architectural mining. It does not implement product modifications, adhering to the read-only mandate of the Specification Miner.

---

## 7. Conclusion

The SCP test architecture and guardrail infrastructure are exceptionally robust and fail-closed:
1. `tools/t00_meta_audit.py` operates via AST parsing and worktree diffing, strictly prohibiting new skips, xfails, test deletions, manufactured verification returns, and code drift.
2. `TaskKernel` already possesses the architectural capability to complete tasks without human review (`VERIFYING -> COMPLETED`), provided automated verifiers and signed receipts are satisfied.
3. Implementing Autonomous Mode (R1 & R2) in full compliance with R3 requires building an autonomous execution and token issuance pipeline that satisfies the cryptographic and state machine contracts, rather than deleting safety states or altering test assertions.

---

## 8. Verification Method

To independently verify all findings and baseline claims in this report, run the following commands:

1. **Verify T00 Meta-Audit Execution & Zero Regressions**:
   ```bash
   python tools/t00_meta_audit.py
   ```
   *Expected Outcome*: Exits 0 with `[T00 Meta-Audit] All integrity checks passed (0 new regressions).`

2. **Verify Meta-Audit Integrity Tests**:
   ```bash
   pytest tests/T00_integrity/test_meta_audit.py -q
   ```
   *Expected Outcome*: All test cases in `test_meta_audit.py` pass, confirming skip allowlist and architecture rules are strictly enforced.

3. **Verify Stale-Code Tripwire Independently**:
   ```bash
   python tools/stale_code_tripwire.py
   ```
   *Expected Outcome*: Exits 0 with all 4 checks (blueprint, unresolved imports, duplicate prompts, metric drift) passing.

4. **Verify Mandatory Gate Bindings & DNA Contract**:
   ```bash
   python tools/verify_scp_test_skill_contract.py
   ```
   *Expected Outcome*: Returns `PASS_WITHIN_SCOPE` with exit code 0.
