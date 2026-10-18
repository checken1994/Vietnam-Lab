# E2E Test Architecture Specification: Bun Eradication & Real LLM Gateway Migration

**Document Version:** 1.0.0  
**Status:** APPROVED / ACTIVE  
**Date:** 2026-10-18  
**Scope:** Dual-Track End-to-End (E2E) Test Suite for Bun Eradication, Real LLM Gateway, Node.js Loop Scheduler, Docker 4-Container Topography, and Host Hygiene.  
**Authoritative References:**  
- `d:\scp\.agents\teamwork\ORIGINAL_REQUEST.md` (Headers 2026-10-15T21:14:48Z, 2026-10-17T23:23:54Z)  
- `d:\scp\.agents\teamwork\orchestrator_9\PROJECT.md` (Features 1–9, Milestones M1–M5, Interface Contracts)  
- `d:\scp\.agents\AGENTS.md` (FA-01 through FA-13)  
- `.agents/skills/scp-dna/SKILL.md` (29 Core Principles, Reality > Model, Fail-Closed, Anti-Placebo)  
- `.agents/skills/scp-gateway-resilience/SKILL.md` (Circuit Breakers, Backoff, Cascade)  
- `.agents/skills/scp-reality-verifier/SKILL.md` (4-level evidence, provenance, independent verification)  

---

## 1. Executive Summary & Purpose

The objective of this E2E Test Architecture is to establish an opaque-box, empirically verifiable, dual-track testing harness for validating the complete eradication of Bun and fake Ollama simulation across the entire SCP Agent OS ecosystem.

The system transitions from an unstable host-dependent Bun environment and fake `sha256:deadbeef...` Ollama stubs into:
1. **Direct Python LLM Gateway**: Native `httpx.AsyncClient` integration with OpenRouter, OpenAI, and Gemini with tri-state Circuit Breakers, Exponential Backoff with Jitter, and Bounded Timeouts (10s hedge, 90s cap).
2. **Decoupled Node.js Loop Scheduler**: Autonomous operations loop mini-service on Port 3030 running on standard Node.js (ESM), decoupled from fake bridge URLs and resilient to backend outages.
3. **4-Container Docker Topography**: Standardized `docker-compose.full.yml` with exactly 4 canonical services (`backend:8000`, `agent-desktop:6080`, `loop-scheduler:3030`, `dashboard:3000`), zero `oven/bun` images, strict `127.0.0.1` binding, and persistent SQLite WAL volume mounts.
4. **Clean Windows Host Environment**: Complete uninstallation of Bun from global npm (`npm uninstall -g bun`), deletion of leftover directories (`%APPDATA%\npm\bun*`, `%USERPROFILE%\.bun`), and absence of active `bun.exe` processes.

---

## 2. Testing Principles & Invariants

All test cases within this architecture adhere strictly to the following invariants:
- **Reality > Model (DNA #1, #26)**: No simulated or hardcoded fake outputs (`assert True`). Every test executes genuine code, real filesystem queries, process invocations, or HTTP client calls.
- **Fail-Closed by Default (DNA #2, FA-01, FA-04)**: Missing credentials, malformed configs, or unauthenticated requests must be rejected immediately with appropriate HTTP status codes (401, 403, 422) or exceptions (`ProviderCredentialError`).
- **Anti-Placebo Gate Pattern (Rule #11)**: Every verification gate is accompanied by negative test cases verifying that invalid inputs, path traversal, expired tokens, or tripped circuit breakers fail closed.
- **Progressive Testability**: Tests run hermetically without external network dependencies by using ephemeral mock transport handlers where live provider credentials are absent, preventing flakiness while asserting genuine client logic.
- **Strict Isolation**: Tests set up and clean up their own state, use thread-safe fixtures, and never pollute shared repository state.

---

## 3. Four-Tier Test Methodology

The E2E test suite (`tests/e2e/test_bun_eradication_e2e.py`) is structured into four distinct, hierarchically escalating tiers:

```
+-------------------------------------------------------------------------+
| Tier 4: Real-World Scenarios (End-to-End Operational Workflows)         |
+-------------------------------------------------------------------------+
                                    ^
+-------------------------------------------------------------------------+
| Tier 3: Cross-Feature Combinations (Pairwise Component Interactions)    |
+-------------------------------------------------------------------------+
                                    ^
+-------------------------------------------------------------------------+
| Tier 2: Boundary & Corner Cases (Timeout bounds, missing keys, fallback)|
+-------------------------------------------------------------------------+
                                    ^
+-------------------------------------------------------------------------+
| Tier 1: Feature Coverage (Core behaviors for all 4 inventoried features)|
+-------------------------------------------------------------------------+
```

### 3.1 Tier 1: Feature Coverage (≥5 Tests per Feature, 20 Tests Total)
Verifies the nominal primary behavior (happy path) and foundational specifications for each feature:
- **Feature A: Real LLM Gateway**: Client initialization, OpenRouter provider configuration, direct HTTP execution, absence of Ollama fake hashes, and free model catalog integrity.
- **Feature B: Loop Scheduler Port 3030**: Node.js execution capability, health endpoint `/healthz`, loop status `/`, decoupling from `LLM_BRIDGE_URL`, and loop state pause/resume controls.
- **Feature C: Docker 4-Container Topography**: 4 canonical services in compose, zero `oven/bun` images, localhost `127.0.0.1` binding, `scp-network` bridge connectivity, and CPU/memory resource limits.
- **Feature D: Host Bun Eradication**: Host eradication audit detection, Node.js runtime availability (v20+), process audit verifying zero `bun.exe` execution, script migration check, and clean command handling.

### 3.2 Tier 2: Boundary & Corner Cases (≥5 Tests per Feature, 20 Tests Total)
Exposes edge cases, resource boundaries, error handling, and security boundaries:
- **Feature A Boundaries**: Fail-closed missing API keys (`ProviderCredentialError`), circuit breaker trip on consecutive 5xx/429 errors, circuit breaker fast-fail (503 response), 90s total cap timeout bound, and multi-provider fallback cascade.
- **Feature B Boundaries**: Invalid startup configuration rejection (`SCP_AUTOFIX_MODE`), single-flight concurrency lock preventing duplicate overlapping audits, 401 unauthorized protection on administrative endpoints, resilience when backend is offline, and boundary validation of `LOOP_INTERVAL_SEC`.
- **Feature C Boundaries**: Environment variable fail-closed defaults, strict memory limit thresholds, egress allowlist enforcement, read-only code mount boundaries, and healthcheck retry caps.
- **Feature D Boundaries**: Rejection of corrupted bun shims, absolute filesystem scan against PATH spoofing, permission denial handling, orphan `bun.lock` detection, and npm uninstall idempotency.

### 3.3 Tier 3: Cross-Feature Combinations (5 Tests Total)
Tests pairwise interactions across component boundaries:
- **T3.1 Gateway + Scheduler**: Scheduler triggers audit cycle via backend which directly resolves LLM queries through Python Gateway without bridge intermediate.
- **T3.2 Scheduler + Docker**: Scheduler container communicates with backend over internal bridge network `http://backend:8000` while completely decoupled from bridge.
- **T3.3 Gateway + Docker**: Backend container communicates with external LLM APIs via allowlisted egress without requiring host port 8081.
- **T3.4 Bun Eradication + Scheduler**: Host scheduler executes purely under Node.js runtime with zero Bun subprocess spawning.
- **T3.5 Bun Eradication + Docker**: Host container build and management workflows execute cleanly without host Bun dependencies.

### 3.4 Tier 4: Real-World Scenarios (3 Tests Total)
Full end-to-end operational flows reflecting 24/7 continuous production:
- **T4.1 Complete Continuous Operations Loop**: Full cycle: Scheduler tick -> backend preflight probe -> audit trigger -> LLM query resolution -> SQLite WAL persistence -> log append.
- **T4.2 Circuit Breaker Outage & Recovery Cascade**: Upstream provider outage simulation -> 3 consecutive failures trip breaker -> fast-fail -> graceful scheduler logging -> recovery and half-open probe success.
- **T4.3 Zero-Bun System Readiness & Stack Verification**: Complete stack audit: zero host Bun, zero container Bun, 4 healthy services, and 100% Tabula Rasa database compliance.

---

## 4. Comprehensive Test Case Catalog

| Test ID | Tier | Category / Feature | Description | Preconditions | Expected Outcome |
|---|---|---|---|---|---|
| `test_t1_gateway_direct_client_init` | Tier 1 | Real LLM Gateway | Verify direct `OpenRouterProvider` initialization with API key | Mock OpenRouter Key | Client initialized, base URL points to `https://openrouter.ai/api/v1` |
| `test_t1_gateway_native_httpx_async_client` | Tier 1 | Real LLM Gateway | Verify native `httpx.AsyncClient` usage and timeout bounds | Gateway module loaded | `httpx.AsyncClient` configured with timeout <= 90s |
| `test_t1_gateway_deprecation_of_fake_ollama_bridge` | Tier 1 | Real LLM Gateway | Verify active gateway does not route to port 8081 or contain fake sha256 | Gateway client inspected | No route to `http://127.0.0.1:8081`, 0 fake `sha256:deadbeef` hashes |
| `test_t1_gateway_real_provider_cascade_registration` | Tier 1 | Real LLM Gateway | Verify registration of OpenRouter and EnvCompat providers | Gateway factory | Both providers registered in cascade order |
| `test_t1_gateway_free_models_catalog_integrity` | Tier 1 | Real LLM Gateway | Verify OPENROUTER_FREE_MODELS contains valid models without fake local stubs | Providers module | Valid list of free models (DeepSeek, Llama, Gemma, etc.), no Ollama |
| `test_t1_scheduler_healthz_endpoint_contract` | Tier 1 | Loop Scheduler | Verify scheduler `/healthz` contract responds with HTTP 200 OK | Ephemeral Scheduler | HTTP 200 with `status: ok` and uptime telemetry |
| `test_t1_scheduler_state_endpoint_contract` | Tier 1 | Loop Scheduler | Verify scheduler root `/` endpoint returns full loop state JSON | Ephemeral Scheduler | JSON with `running`, `paused`, `total_runs`, `last_run` fields |
| `test_t1_scheduler_decoupled_from_fake_bridge` | Tier 1 | Loop Scheduler | Verify scheduler runs and survives when `LLM_BRIDGE_URL` is omitted | Scheduler config | Scheduler initializes without error; bridge marked offline safely |
| `test_t1_scheduler_pause_resume_state_transitions` | Tier 1 | Loop Scheduler | Verify `/pause` and `/resume` transition `state.paused` durably | Admin authenticated | Pause sets `paused: true`; resume sets `paused: false` |
| `test_t1_scheduler_nodejs_runtime_execution` | Tier 1 | Loop Scheduler | Verify loop scheduler script executes cleanly under Node.js runtime | Host Node.js v20+ | Node.js spawns scheduler and outputs initialization banner |
| `test_t1_docker_compose_topography_4_services` | Tier 1 | Docker Topography | Verify `docker-compose.full.yml` defines the 4 canonical services | Compose file | Services contain `backend`, `agent-desktop`, `loop-scheduler`, `dashboard` |
| `test_t1_docker_zero_oven_bun_images` | Tier 1 | Docker Topography | Verify no service in the compose file uses `oven/bun` images | Compose file | Zero occurrences of `oven/bun` in image declarations |
| `test_t1_docker_strict_localhost_port_bindings` | Tier 1 | Docker Topography | Verify all published ports (8000, 6080, 3030, 3000) bind to `127.0.0.1` | Compose file | All host port strings match `127.0.0.1:<port>:<port>` |
| `test_t1_docker_isolated_bridge_network` | Tier 1 | Docker Topography | Verify all services attach to internal bridge network `scp-network` | Compose file | Network `scp-network` defined with driver `bridge` and joined by all |
| `test_t1_docker_resource_limits_enforced` | Tier 1 | Docker Topography | Verify `deploy.resources.limits` (cpus, memory, pids) on all services | Compose file | All services have cpus, memory, and pids limits defined |
| `test_t1_host_bun_eradication_audit_detection` | Tier 1 | Host Eradication | Verify eradication auditor detects host Bun status correctly | System audit tool | Auditor accurately inspects PATH and filesystem |
| `test_t1_host_nodejs_runtime_availability` | Tier 1 | Host Eradication | Verify host `node` is available and version >= 20 | Host system | `node -v` succeeds with version >= 20.0.0 |
| `test_t1_host_absence_of_bun_processes` | Tier 1 | Host Eradication | Verify no running processes named `bun.exe` or `bun` exist | OS process table | Process scan finds 0 instances of `bun.exe` |
| `test_t1_host_scripts_nodejs_purity` | Tier 1 | Host Eradication | Verify launcher scripts support Node.js execution | Script files | Scripts execute via `node` instead of `bun` |
| `test_t1_host_fail_closed_missing_bun_handling` | Tier 1 | Host Eradication | Verify system handles missing bun cleanly without silent unhandled crashes | Environment | Invoking non-existent bun fails closed with FileNotFoundError/exit != 0 |
| `test_t2_gateway_missing_api_keys_fail_closed` | Tier 2 | Real LLM Gateway | Verify empty/placeholder API key raises `ProviderCredentialError` | Missing ENV | `ProviderCredentialError` raised; no requests sent |
| `test_t2_gateway_circuit_breaker_trips_on_failures` | Tier 2 | Real LLM Gateway | Verify 3 consecutive 5xx/429 errors trip breaker from CLOSED to OPEN | Breaker registry | Breaker state becomes OPEN after 3 failures; `allow()` returns False |
| `test_t2_gateway_circuit_breaker_fast_fail_503` | Tier 2 | Real LLM Gateway | Verify OPEN breaker immediately rejects requests without network IO | OPEN Breaker | Immediate 503 response with `Retry-After: 300` |
| `test_t2_gateway_timeout_bounded_to_cap` | Tier 2 | Real LLM Gateway | Verify requests exceeding 90s cap fail closed with timeout | Slow response | Async timeout triggered at cap; fails closed |
| `test_t2_gateway_offline_fallback_cascade` | Tier 2 | Real LLM Gateway | Verify transition from failed primary to fallback provider | Failed primary | Fallback provider invoked; returns valid response or fails closed |
| `test_t2_scheduler_invalid_mode_refuses_start` | Tier 2 | Loop Scheduler | Verify invalid `SCP_AUTOFIX_MODE` throws error and halts startup | Bad config mode | Startup throws `Error: invalid SCP_AUTOFIX_MODE` (fail-closed) |
| `test_t2_scheduler_single_flight_concurrency_lock` | Tier 2 | Loop Scheduler | Verify concurrent trigger requests do not spawn duplicate audits | Active audit | Second trigger returns `busy` or `queued`; no parallel run |
| `test_t2_scheduler_unauthorized_endpoints_return_401` | Tier 2 | Loop Scheduler | Verify administrative actions without token return HTTP 401 | Missing token | HTTP 401 with `detail: unauthorized` |
| `test_t2_scheduler_backend_offline_resilience` | Tier 2 | Loop Scheduler | Verify scheduler logs `scp_online: false` and continues when backend down | Offline backend | Loop run logged with `scp_offline`; loop continues ticking |
| `test_t2_scheduler_interval_boundary_enforcement` | Tier 2 | Loop Scheduler | Verify negative or zero interval defaults to safe minimum | Extreme interval | Interval clamped to default (>= 30s / 300s) |
| `test_t2_docker_missing_env_fails_closed` | Tier 2 | Docker Topography | Verify required secrets fail closed when missing from environment | Compose config | Missing required tokens trigger compose validation failure |
| `test_t2_docker_memory_limits_within_bounds` | Tier 2 | Docker Topography | Verify memory limits: backend <= 1G, desktop <= 1.5G, scheduler <= 512M | Compose file | All limits within specification bounds |
| `test_t2_docker_egress_allowlist_boundary` | Tier 2 | Docker Topography | Verify `SCP_EGRESS_MODE=allowlist` blocks unapproved external domains | Egress policy | Non-allowlisted domains rejected with `EgressDeniedError` |
| `test_t2_docker_volume_persistence_and_ro_code` | Tier 2 | Docker Topography | Verify `./data` is rw for SQLite WAL, while source code mounts are ro | Compose file | `./data` has read-write access; `./mini-services/*` is `:ro` |
| `test_t2_docker_healthcheck_retries_bounded` | Tier 2 | Docker Topography | Verify healthcheck retries <= 3 and timeout <= 5s across all services | Compose file | Retries <= 3 and timeout <= 5s on all services |
| `test_t2_host_corrupted_bun_shim_rejection` | Tier 2 | Host Eradication | Verify corrupted or dummy bun shim failing version check is flagged | Dummy binary | Eradication detector flags binary as invalid/suspicious |
| `test_t2_host_path_independent_filesystem_scan` | Tier 2 | Host Eradication | Verify scan inspects `%APPDATA%\npm` and `%USERPROFILE%\.bun` directly | PATH altered | Scanner detects leftover files even if PATH does not include them |
| `test_t2_host_locked_file_permission_handling` | Tier 2 | Host Eradication | Verify cleanup handles Windows file lock errors gracefully | File locked | Returns clear failure diagnostics without crashing |
| `test_t2_host_orphan_lockfile_detection` | Tier 2 | Host Eradication | Verify detection of leftover `bun.lock` or `bun.lockb` files in repo | Repo scan | Flags presence of Bun lockfiles in project directories |
| `test_t2_host_uninstall_idempotency` | Tier 2 | Host Eradication | Verify running uninstallation when already removed returns exit 0 | Clean system | Clean returncode 0 without side-effects |
| `test_t3_cross_gateway_and_scheduler_interaction` | Tier 3 | Cross-Feature | Verify scheduler audit cycle calls backend which queries Gateway directly | Full mock stack | End-to-end chain succeeds without calling port 8081 |
| `test_t3_cross_scheduler_and_docker_networking` | Tier 3 | Cross-Feature | Verify containerized scheduler connects to `http://backend:8000` | Bridge network | HTTP request reaches backend across internal bridge network |
| `test_t3_cross_gateway_and_docker_egress` | Tier 3 | Cross-Feature | Verify backend container queries real LLM endpoints via egress allowlist | Egress policy | Outbound LLM call passes egress filter; no local bridge used |
| `test_t3_cross_host_eradication_and_scheduler` | Tier 3 | Cross-Feature | Verify host scheduler runs under Node.js with 0 bun subprocesses | Subprocess audit | Process tree shows `node` -> `node`; zero `bun` processes |
| `test_t3_cross_host_eradication_and_docker` | Tier 3 | Cross-Feature | Verify Docker build and compose up execute without calling host bun | Docker CLI | All compose tasks execute via Docker engine and Node.js |
| `test_t4_real_world_complete_continuous_loop` | Tier 4 | Real-World Scenario | Full autonomous loop: probe -> audit trigger -> LLM query -> WAL log | Integrated stack | End-to-end audit cycle completes and writes to `loop_runs.jsonl` |
| `test_t4_real_world_circuit_breaker_outage_recovery`| Tier 4 | Real-World Scenario | Provider outage -> breaker trips -> fast fail -> recovery -> reset | Outage sim | Loop logs error without crashing; recovers when provider recovers |
| `test_t4_real_world_zero_bun_stack_readiness` | Tier 4 | Real-World Scenario | Complete stack verification: 0 Bun, 4 services, Tabula Rasa preserved | Full system audit | Stack passes all production readiness checks |

---

## 5. Test Execution Harness & Environment Isolation

### 5.1 Test Command
The complete test suite is executed using standard `pytest`:
```powershell
python -m pytest tests/e2e/test_bun_eradication_e2e.py -v --tb=short
```

### 5.2 Mocking & Hermetic Network Transport
To ensure tests run hermetically and deterministically in any CI/CD or development environment without requiring active internet connectivity or paid API credits:
- Mock transport uses `httpx.MockTransport` at the HTTP client boundary.
- All request schemas, authentication headers, request paths, and response bodies strictly adhere to the OpenAI/OpenRouter wire format.
- Real client code, circuit breaker state machines, retry decorators, and error handlers are fully executed.

### 5.3 Concurrency & Database Durability
- Database tests verify SQLite WAL mode (`PRAGMA journal_mode = WAL`) and busy timeouts (`PRAGMA busy_timeout = 5000`).
- No shared temporary files: tests utilize isolated temporary directories (`tmp_path`) for log files and state persistence.

---

## 6. Traceability Matrix

| Requirement / Spec | Feature | Test Cases |
|---|---|---|
| ORIGINAL_REQUEST R1 / PROJECT M1 | Real LLM Provider Integration | `test_t1_gateway_direct_client_init`, `test_t1_gateway_native_httpx_async_client`, `test_t1_gateway_real_provider_cascade_registration`, `test_t2_gateway_missing_api_keys_fail_closed`, `test_t2_gateway_circuit_breaker_trips_on_failures`, `test_t2_gateway_circuit_breaker_fast_fail_503` |
| ORIGINAL_REQUEST R1 / PROJECT M1 | Deprecate Fake Ollama Bridge | `test_t1_gateway_deprecation_of_fake_ollama_bridge`, `test_t1_gateway_free_models_catalog_integrity`, `test_t3_cross_gateway_and_scheduler_interaction` |
| ORIGINAL_REQUEST R2 / PROJECT M2 | Loop Scheduler Node.js Migration | `test_t1_scheduler_healthz_endpoint_contract`, `test_t1_scheduler_state_endpoint_contract`, `test_t1_scheduler_nodejs_runtime_execution`, `test_t2_scheduler_invalid_mode_refuses_start`, `test_t2_scheduler_single_flight_concurrency_lock`, `test_t2_scheduler_unauthorized_endpoints_return_401` |
| ORIGINAL_REQUEST R2 / PROJECT M2 | Loop Scheduler Decoupling | `test_t1_scheduler_decoupled_from_fake_bridge`, `test_t2_scheduler_backend_offline_resilience`, `test_t3_cross_scheduler_and_docker_networking` |
| ORIGINAL_REQUEST R3 / PROJECT M4 | Docker 4-Container Topography | `test_t1_docker_compose_topography_4_services`, `test_t1_docker_zero_oven_bun_images`, `test_t1_docker_strict_localhost_port_bindings`, `test_t1_docker_isolated_bridge_network`, `test_t1_docker_resource_limits_enforced`, `test_t2_docker_memory_limits_within_bounds`, `test_t2_docker_volume_persistence_and_ro_code` |
| ORIGINAL_REQUEST R4 / PROJECT M4 | Windows Host Bun Eradication | `test_t1_host_bun_eradication_audit_detection`, `test_t1_host_nodejs_runtime_availability`, `test_t1_host_absence_of_bun_processes`, `test_t1_host_scripts_nodejs_purity`, `test_t2_host_corrupted_bun_shim_rejection`, `test_t2_host_path_independent_filesystem_scan`, `test_t2_host_orphan_lockfile_detection` |
| ORIGINAL_REQUEST R5 / PROJECT M5 | Real-World Workflows & Quality Gate | `test_t4_real_world_complete_continuous_loop`, `test_t4_real_world_circuit_breaker_outage_recovery`, `test_t4_real_world_zero_bun_stack_readiness` |

---

## 7. Anti-Placebo & Security Tripwire Evidence

In accordance with FA-01, FA-04, FA-09, and Rule #11, this test suite incorporates negative tripwires:
1. **Forged Credentials Rejection**: Proves that invalid or empty API tokens cannot invoke external providers.
2. **Circuit Breaker Open State Assertion**: Proves that when tripped, zero network sockets are opened and HTTP 503 is returned immediately.
3. **SSRF and Egress Gate**: Proves that non-allowlisted external endpoints are blocked at the boundary.
4. **State Machine Concurrency Gate**: Proves that concurrent audit calls do not corrupt SQLite state.
5. **Path Traversal & Host Hygiene**: Proves that scanner detects Bun artifacts regardless of environment path manipulation.
