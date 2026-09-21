# Project: SCP Security Remediation (GAP-05, GAP-06, GAP-08, GAP-09)

## Architecture
- **Storage Subsystem (`scp/kernel_storage.py`, `scp/task_kernel_parts/taskkernel.py`)**:
  - Database-level Optimistic Concurrency Control (OCC) using SQLite WAL mode, `BEGIN IMMEDIATE`, exponential busy-retry, and version-checked SQL updates (`WHERE version=?`) raising `OptimisticLockError`.
  - In-memory `RLock` is recognized as a placebo and eliminated; SQLite file locks and OCC version increments protect multi-process state.
  - `make_storage(db_path)` provides the storage initialization interface, guarded by fail-closed environment configuration (`SCP_STORAGE_BACKEND`).
- **Capability Security Subsystem (`scp/core/capability_token.py`, `scp/security/capability_epoch.py`)**:
  - Cryptographic token authentication using HMAC-SHA256.
  - Fail-closed secret loading (`SCP_CAPABILITY_SECRET`): missing secret raises `MissingSecretError` immediately.
  - `CapabilityToken` dataclass in `scp/security/capability_epoch.py` signs and verifies tokens with SHA256 HMAC; unsigned or tampered tokens raise `InvalidTokenSignatureError` (subclass of `PermissionError`).
  - Test runner protection via top-level `tests/conftest.py` setting default test secret during automated suite execution without compromising production fail-closed security.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | GAP-05 Concurrency Verification | Verify elimination of in-memory RLock placebo; prove OCC database concurrency under multi-process workload | M1 | Survey / ORIGINAL_REQUEST §R1 |
| 2 | GAP-06 SQLite SPOF Documentation | Add explicit docstring WARNING in `make_storage()` regarding SQLite as a Single Point of Failure | M1 | Survey / ORIGINAL_REQUEST §R2 |
| 3 | GAP-06 Storage Backend Guard | Add `SCP_STORAGE_BACKEND` check in `make_storage()`: raise `NotImplementedError` for unsupported backends | M1 | Survey / ORIGINAL_REQUEST §R2 |
| 4 | GAP-06 Unit Tests | Add comprehensive unit tests in `tests/T04_kernel/test_kernel_storage.py` for storage backend guard | M1 | Survey / ORIGINAL_REQUEST §R2 |
| 5 | GAP-09 Test Harness Preparation | Create root `tests/conftest.py` injecting `SCP_CAPABILITY_SECRET` for test discovery | M2 | Survey / ORIGINAL_REQUEST §R4 |
| 6 | GAP-09 Env Documentation | Update `.env.example` and VPS env documentation for `SCP_CAPABILITY_SECRET` | M2 | Survey / ORIGINAL_REQUEST §R4 |
| 7 | GAP-09 Remove Fallback Secret | Delete hardcoded `dev-secret-do-not-use-in-prod-12345` from `scp/core/capability_token.py` | M2 | Survey / ORIGINAL_REQUEST §R4 |
| 8 | GAP-09 Fail-Closed Import Guard | Raise `MissingSecretError` when `SCP_CAPABILITY_SECRET` is unset | M2 | Survey / ORIGINAL_REQUEST §R4 |
| 9 | GAP-08 Token HMAC Signing | Implement HMAC-SHA256 signing in `CapabilityAuthority.issue()` attached to `CapabilityToken.signature` | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 10 | GAP-08 Token Signature Verification | Implement constant-time signature verification in `CapabilityAuthority.validate()`; reject unsigned/tampered tokens | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 11 | GAP-08 InvalidTokenSignatureError | Define fail-closed `InvalidTokenSignatureError` (inheriting `PermissionError`) on missing or bad signatures | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 12 | GAP-08 Backward Compatibility Reject | Strictly reject legacy unsigned tokens (no silent acceptance) | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 13 | Final Anti-Placebo & Adversarial Proof | Execute all 4 anti-placebo probes (turning GREEN), adversarial challenger testing, full test suite >= 450 tests pass | M4 | Survey / ORIGINAL_REQUEST Acceptance Criteria |
| 14 | Final Meta-Audit & Handoff | Pass `tools/t00_meta_audit.py` (0 regressions) and generate handoff report at `.agents/sentinel_4/handoff.md` | M4 | Survey / ORIGINAL_REQUEST Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: Kernel Storage (GAP-05 & GAP-06) | Verify RLock removal in `scp/kernel_storage.py`; add SPOF warning and `SCP_STORAGE_BACKEND` guard; add unit tests | none | PLANNED |
| 2 | M2: Capability Secret Guard (GAP-09) | Add root `tests/conftest.py`, update `.env.example`, remove hardcoded secret, raise `MissingSecretError` | none | PLANNED |
| 3 | M3: CapabilityToken HMAC Signing (GAP-08) | Add HMAC-SHA256 signature to `CapabilityToken`, verify in `CapabilityAuthority.validate()`, raise `InvalidTokenSignatureError` | M2 | PLANNED |
| 4 | M4: Adversarial Hardening & Handoff | Challenger penetration probes, full test suite execution (>= 450 tests), meta-audit verification, handoff at `.agents/sentinel_4/handoff.md` | M1, M2, M3 | PLANNED |

## Interface Contracts
### Storage Configuration (`scp/kernel_storage.py`)
- `make_storage(db_path: str | Path) -> SQLiteKernelStorage`:
  - Reads `os.environ.get("SCP_STORAGE_BACKEND", "sqlite").strip().lower()`.
  - If equal to `"sqlite"` or `""`: returns `SQLiteKernelStorage(db_path)`.
  - If any other value (e.g. `"postgres"`, `"mysql"`): raises `NotImplementedError(f"Unsupported storage backend '{backend}'. Only 'sqlite' is currently supported.")`.
  - Docstring includes:
    ```
    WARNING: SQLite is a Single Point of Failure (SPOF) in distributed deployments.
    It does not support cross-node replication or active-active clustering.
    For high availability or multi-node production setups, a distributed storage backend is required.
    ```

### Capability Secret & Token Contracts (`scp/core/capability_token.py` & `scp/security/capability_epoch.py`)
- `MissingSecretError(RuntimeError)` in `scp/core/capability_token.py` and exported.
  - Raised when `SCP_CAPABILITY_SECRET` is missing, empty, or whitespace.
- `InvalidTokenSignatureError(PermissionError)` in `scp/security/capability_epoch.py`.
  - Raised when token signature is missing, invalid, or forged.
- `CapabilityToken`:
  - Fields: `subject: str`, `epoch: int`, `token_id: str`, `issued_at: float`, `signature: str = ""`.
  - Canonical payload format for signing: `f"{subject}:{epoch}:{token_id}:{issued_at:.6f}".encode("utf-8")`.
  - Signature algorithm: `hmac.new(secret, canonical_payload, hashlib.sha256).hexdigest()`.

## Code Layout
- `scp/kernel_storage.py`: Storage interface, `SQLiteKernelStorage`, `make_storage`. Owned by M1 Worker.
- `tests/T04_kernel/test_kernel_storage.py`: Unit tests for storage backend guard and SQLite storage. Owned by M1 Worker.
- `tests/conftest.py`: Root pytest fixtures initializing environment defaults for automated test runs. Owned by M2 Worker.
- `.env.example`: Root environment variable template. Owned by M2 Worker.
- `scp/core/capability_token.py`: Token utilities, `MissingSecretError`, secret loading. Owned by M2 Worker.
- `scp/security/capability_epoch.py`: `CapabilityToken`, `CapabilityAuthority`, `InvalidTokenSignatureError`. Owned by M3 Worker.
- `tests/T03_capability/test_capability_hmac_gap08.py`: Dedicated tests for GAP-08 HMAC signing & verification. Owned by M3 Worker.
- `.agents/sentinel_4/handoff.md`: Final handoff artifact. Owned by M4.
