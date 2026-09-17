# Project: Chiến dịch "Đại Phẫu Thuật" (Full Sweep)

## Architecture
- **LLM Gateway & Zero-Trust Verification Subsystem (`scp/llm_gateway/`)**:
  - `ContractProber` (`scp/llm_gateway/prober.py`): Performs adversarial prompt injection, roleplay bypass, and format breaking probes against LLM endpoints before model qualification. Real network requests via `httpx.AsyncClient` without mocks.
  - `ModelDiscoveryStore` & `LocalEndpointScanner` (`scp/llm_gateway/discovery.py`): Manages the full state machine (`DISCOVERED -> QUARANTINED -> (probe) -> QUALIFIED -> ACTIVE / COOLDOWN`). Persisted via FoundationDB/SQLite migrations.
  - Ephemeral TCP loopback test infrastructure (`http.server.ThreadingHTTPServer(("127.0.0.1", 0))`): Real physical OS network sockets for tests, 100% eliminating `unittest.mock` and `AsyncMock`.
- **System Integrity & Technical Debt Resolution Subsystem**:
  - Elimination of top-level and latent `ImportError` occurrences across all 585 modules.
  - Wire-in completion for all un-wired TODO code blocks (`smart_classifier`, `domain_store`, `external_trust`, `cisa_kev`, `callgraph_delta`).
  - Stale debt marker cleanup in `GATEWAY.md` and `routes/README.md` acknowledging active status of audited routes (`audit_routes`, `threat_routes`, `prediction_routes`).
- **Architecture Backlog Subsystem**:
  - **Token Streaming (SSE)**: Real-time token delta streaming via `LLMGateway.chat_stream`, bridged to `POST /v105/ask/stream` and OpenAI-compatible `POST /v1/chat/completions` SSE chunk streaming.
  - **Structured Logging (Structlog)**: Centralized `scp/core/logging_config.py` with JSON/Console formatters replacing scattered AST `print()` calls in runtime and core modules.
  - **Typed Settings (`pydantic-settings`)**: `SCPSettings(BaseSettings)` in `scp/core/config.py` unifying environment configuration with typed fields and backward compatibility.
  - **Unified Auth JWT System**: Dual-mode `verify_admin` in `scp/security/auth.py` accepting valid Admin JWT tokens from `/auth/token` while preserving static secret compatibility.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | S33 Contract Prober | Implement `ContractProber` with adversarial probes (injection, roleplay, format break) | M1 | Survey / ORIGINAL_REQUEST §R1 |
| 2 | S35 Model Lifecycle | Implement `ModelDiscoveryStore` & `LocalEndpointScanner` with lifecycle states | M1 | Survey / ORIGINAL_REQUEST §R1 |
| 3 | S33/S35 Loopback Test Harness | Implement 100% mock-free tests in `tests/T05_gateway/` using `ThreadingHTTPServer(("127.0.0.1", 0))` | M1 | Survey / ORIGINAL_REQUEST §R1 |
| 4 | Eliminate T05 Gateway Mocks | Verify `grep -rn "unittest.mock" tests/T05_gateway/` returns EMPTY | M1 | Survey / ORIGINAL_REQUEST §R1 |
| 5 | Fix Top-level & Latent ImportErrors | Fix `scpv14_process_mixin.py` (JudgeVerdict) and 5 latent imports; achieve 0 import errors | M2 | Survey / ORIGINAL_REQUEST §R2 |
| 6 | Wire-in 6 Unimplemented TODOs | Implement and wire logic in `smart_classifier`, `domain_store`, `external_trust`, `cisa_kev`, `callgraph_delta` | M2 | Survey / ORIGINAL_REQUEST §R2 |
| 7 | Clean 5 Stale TODOs & 11 DEAD Markers | Clean stale comments in 5 files and update `GATEWAY.md` / `routes/README.md` | M2 | Survey / ORIGINAL_REQUEST §R2 |
| 8 | Structlog Integration | Install `structlog==24.4.0`, create `logging_config.py`, migrate `print()` to structured logger | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 9 | Typed Settings (pydantic-settings) | Implement `SCPSettings(BaseSettings)` in `scp/core/config.py` and integrate | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 10 | Unified Auth JWT System | Upgrade `verify_admin` to dual-mode (JWT + static secret fallback) | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 11 | Real-time Token Streaming (SSE) | Implement `LLMGateway.chat_stream` and bridge to `/v105/ask/stream` & OpenAI SSE | M3 | Survey / ORIGINAL_REQUEST §R3 |
| 12 | Global Anti-Cheating & Regression Gates | Run `t00_meta_audit.py`, full `pytest tests/ -q`, verify 0 violations | M4 | Survey / ORIGINAL_REQUEST Acceptance |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: S33 & S35 Zero-Trust Implementation | Contract Prober, Model Lifecycle, Mock-Free Loopback Tests in T05 | none | DONE |
| 2 | M2: Technical Debt Cleanup (Tripwire 5) | Fix all import errors, wire 6 TODOs, clean stale markers & DEAD docs | none | IN_PROGRESS |
| 3 | M3: Architecture Backlog Integration | Structlog, Typed Settings, Unified Auth JWT, Token Streaming SSE | M1, M2 | PLANNED |
| 4 | M4: Final Verification & Anti-Cheating Gates | Full test pass, T00 meta-audit, zero-mock audit, forensic signoff | M1, M2, M3 | PLANNED |

## Interface Contracts
### Contract Prober (`scp/llm_gateway/prober.py`)
- `ContractProber(endpoint_url: str, api_key: str = "", model: str = "", timeout: float = 10.0)`
- `async probe_async() -> bool`: Returns `True` if all adversarial probes pass, `False` on any violation or exception (fail-closed).

### Model Discovery (`scp/llm_gateway/discovery.py`)
- `ModelLifecycleState(str, Enum)`: `DISCOVERED`, `QUARANTINED`, `QUALIFIED`, `ACTIVE`, `COOLDOWN`.
- `ModelDiscoveryStore(db_path: str)`: Manages model records in database.
- `LocalEndpointScanner(store: ModelDiscoveryStore, prober_factory=None)`: Scans endpoints, runs prober, updates states.

### Logging Configuration (`scp/core/logging_config.py`)
- `configure_logging(log_level: str = "INFO", json_output: bool = False)`: Configures structlog with standard library interop.

### Typed Settings (`scp/core/config.py`)
- `class SCPSettings(BaseSettings)`: Defines system settings with prefix `SCP_` and `.env` fallback.
- `get_settings() -> SCPSettings`: Cached access to singleton settings instance.

### Dual-Mode Auth (`scp/security/auth.py`)
- `verify_admin(authorization: str = Header(None), token: str = Query(None), request: Request = None) -> bool`:
  - Decodes Bearer token as JWT using `SCP_JWT_SECRET`. If valid with `sub="admin"` or `role="admin"`, returns `True`.
  - On `InvalidTokenError`, falls back to constant-time static secret comparison with `SCP_AUTH_TOKEN_SECRET` or `SCP_AUTH_PASSWORD`.

## Code Layout
- M1 Owner: `scp/llm_gateway/prober.py`, `scp/llm_gateway/discovery.py`, `scp/llm_gateway/__init__.py`, `tests/T05_gateway/test_prober.py`, `tests/T05_gateway/test_model_discovery.py`
- M2 Owner: `scp/runtime/engine_parts/scpv14_process_mixin.py`, `scp/core/smart_classifier.py`, `scp/knowledge/domain_store.py`, `scp/meta/external_trust.py`, `scp/security/cisa_kev.py`, `scp/security/predictor.py`, `scp/autofix/callgraph_delta.py`, `scp/autofix/runner_phases/blast_radius.py`, `GATEWAY.md`, `scp/api/routes/README.md`, `scp/api/routes/admin_v100.py`, `scp/api/routes/v105_routes.py`, `scp/ask_kernel_adapter.py`, `scp/autofix/engine.py`, `scp/meta/reverify_scheduler.py`
- M3 Owner: `scp/requirements.txt`, `scp/core/logging_config.py`, `scp/core/config.py`, `scp/security/auth.py`, `scp/api/routes/stream_routes.py`, `scp/api/routes/openai_compat.py`, `scp/llm_gateway/client.py`, `scp/api_server.py`, `scp/api_server_parts/lifespan.py`
- M4 Owner: Verification across entire repository, audit evidence compilation, handoff report.
