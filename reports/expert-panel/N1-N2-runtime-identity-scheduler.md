# N1 + N2 — Runtime Identity and Scheduler Readiness

- **Scope:** `Dockerfile`, `compose.yml`, `compose.test.yml`, `scp/api_server.py`, `scp/api_server_parts/lifespan.py`, and targeted T01/T02 contracts.
- **Working SHA:** `ff08b8772f3473c49bb74d361ed681a593963c77`
- **Branch:** `audit/runtime-guard-AUDIT-20260909`
- **Commit:** none (requested; working tree preserved).
- **Verdict:** `CANDIDATE_NOT_PROVEN` / acceptance **PENDING**. The isolated Docker runtime gate below passed within this scope, but this is not a release/customer-handoff verdict. A broader targeted run also exposed an unrelated OpenAI streaming interaction failure and the full mandatory suite was not green on this SHA.

## 1. Problem and why-chain

### N1 — source identity

1. Why could `/health.service_identity.commit` become `unknown`? The Docker image had a build-time `ARG/ENV`, but Compose used `${SCP_GIT_SHA:-unknown}` and `env_file: .env`; a stale runtime value could override the intended build identity.
2. Why is that unsafe? The runtime identity then no longer binds health evidence to the exact image source.
3. Why was it not caught by liveness alone? `/health` could still be HTTP 200 while the identity was unproven.
4. Root correction: the build and runtime environment now require the same explicit current full SHA; the Dockerfile rejects missing, malformed, or unknown SHA; production/release health and readiness fail closed when no exact SHA is available.

### N2 — background scheduler

1. Why was readiness misleading? The lifespan called `_judge.schedule_background_jobs()` even though the canonical `RealityJudge` has no such method.
2. Why could it look started? `asyncio.create_task()` was followed immediately by `background_scheduler_started=True`, before task completion or failure.
3. Why is that dangerous? `/readiness` could become green without a real scheduler and task exceptions could be hidden from readiness.
4. Root correction: removed the nonexistent judge hook; readiness is now tied to the actual `BackgroundJobRegistry`, required jobs are checked after start, and an observer revokes readiness after five consecutive required-job runtime errors. S23 FreeDiscovery and S35 model lifecycle remain separate schedulers.

## 2. Changes

- `Dockerfile`
  - Removed the `unknown` build default.
  - Added a build-time 40-character hexadecimal SHA check.
  - A missing or malformed SHA fails the image build before the image can be used for release evidence.
- `compose.yml` and `compose.test.yml`
  - Build args and runtime environment both require `${SCP_GIT_SHA:?…}`.
  - The same explicit SHA is passed to build and container; `.env` cannot silently replace it with `unknown`.
- `scp/api_server.py`
  - Exact SHA validation and production/release identity policy.
  - `/health` returns 503 with `source_identity_unavailable` in production/release when identity is not exact.
  - `/readiness` requires judge, real background registry, and source identity; it reports scheduler state instead of faking `pending`/`ok`.
  - The cached identity is invalidated when the environment SHA changes, which keeps tests and runtime probes from reusing a stale identity.
- `scp/api_server_parts/lifespan.py`
  - Removed the dead `RealityJudge.schedule_background_jobs` path and its false-positive started log.
  - Starts and validates the real registry, stores explicit scheduler status/error state, and observes required-job failures.
  - Shutdown cancels the registry observer without changing S23/S35 lifecycle code.
- Tests
  - Added exact build/runtime identity contracts.
  - Added registry-vs-removed-judge scheduler contract.
  - Added startup failure and required-job runtime failure observability checks.

## 3. Verification matrix

| Gate | Result | Evidence |
|---|---|---|
| `py_compile` | PASS | `python -m py_compile scp/api_server.py scp/api_server_parts/lifespan.py tests/T01_boot/test_flow_01_boot_background_scp_standard.py tests/T02_contract/test_god_split_semantic_parity.py` |
| Focused T01/T02 | PASS | `65 passed` |
| Focused T00 meta | PASS | `python -m pytest -q tests/T00_integrity/test_meta_audit.py --tb=short` → `39 passed` |
| Combined scoped T00/T01/T02 | PASS | `python -m pytest -q tests/T00_integrity/test_meta_audit.py tests/T02_contract/test_god_split_semantic_parity.py tests/T01_boot/test_flow_01_boot_background_scp_standard.py --tb=short` → `104 passed` |
| `git diff --check` | PASS | no whitespace errors |
| Test-skill contract | PASS_WITHIN_SCOPE | `python tools/verify_scp_test_skill_contract.py`; exact SHA printed as `ff08b8772f3473c49bb74d361ed681a593963c77` |
| Negative identity build | PASS (fail-closed behavior) | `docker build --build-arg SCP_GIT_SHA=` exited `1`; Dockerfile identity guard failed |
| Exact-SHA image build | PASS | `docker build --no-cache --build-arg SCP_GIT_SHA=ff08b8772f3473c49bb74d361ed681a593963c77`; image digest `sha256:86aab992cca36004f24527aac59968edcca82cbdb039c6bb9cc9de27b74b2182` |
| Compose identity config | PASS | `docker compose -f compose.yml config` showed identical build arg/runtime SHA and loopback port mapping |
| Isolated Docker startup | PASS | container name `scp-n1n2-ff08…`, dedicated volume `scp-n1n2-data-ff08…`, host port `127.0.0.1:18080` |
| `/health` | PASS | HTTP 200; `service_identity.commit` exactly `ff08b8772f3473c49bb74d361ed681a593963c77`; source identity check `ok` |
| `/readiness` | PASS | Initial HTTP 503 with `judge=pending`; then HTTP 200 `status=ready`, checks `judge=ok`, `background_scheduler=ok`, `source_identity=ok`, `reason=null` |
| Scheduler logs | PASS within observed scope | Registry start plus `kernel_lease_expiry` and `kernel_orphan_reconcile` first execution lines; no traceback in captured log |
| Golden functional request | PASS within fail-closed semantics | `/ask` HTTP 200 with real `run_id`, `trace_id`, `ledger_status=OK`, `governance_decision=ESCALATE`, `verdict=FAIL`; isolated volume contained kernel trace/request ledger and scheduler artifacts |
| Teardown/cleanup | PASS | `docker stop`, `docker rm`, volume removal; `port_18080_open=False`; audit container/volume names absent afterward |

### Docker evidence paths

Raw evidence was captured outside the source tree at:

`C:\Users\check\Downloads\scp\.tmp-docker-n1n2-ff08b8772f3473c49bb74d361ed681a593963c77\`

Notable files:

- `build-candidate.log`
- `image-identity-crosscheck.log`
- `probe-health-readiness-candidate.log`
- `container-inspect-candidate-start.log`
- `container-live-plus35s.log`
- `golden-request.log`
- `data-inventory-live.log`
- `teardown-stop.log`
- `teardown-rm.log`
- `teardown-volume-rm.log`
- `cleanup-verification.log`
- `failure-probe/build-missing-sha.log`

Archive checksums:

- pristine `git archive HEAD`: `e68019322aadfbe6580094b71b5c0c8b45879d57301c6e924633038d687e36d5`
- candidate context archive after scoped patch: `e6dab4e1ec26bcb6a73eb4effff0e4e3aefb549173572d773be461b64e7ec311`

The candidate context was assembled from the exact HEAD archive and the scoped working-tree patch. The runtime image therefore proves the candidate tree used for this worker gate; it is not a release snapshot of the entire dirty repository.

## 4. Missing evidence and limits

- The full mandatory test suite was not used as a green release claim. A broader `tests/T00_integrity tests/T01_boot tests/T02_contract tests/T11_release` run had one unrelated failure in `tests/T02_contract/test_flow_03_openai_compat_scp_standard.py::TestFlow03OpenAICompat::test_streaming_response_translation` (expected 503, observed 200 in the response object while telemetry captured a 503). This was recorded and not changed because it is outside N1/N2.
- The Docker golden request exercised the real API/kernel/ledger path and captured verifier-related trace artifacts, but the deny-egress runtime returned an honest `FAIL/ESCALATE`, not a verified factual answer. It must not be presented as a successful semantic answer.
- The runtime gate was isolated and used a dedicated port/volume; it did not prove production TLS, external provider availability, multi-node behavior, or release/customer-handoff readiness.
- Full release acceptance remains `PENDING`/`CANDIDATE_NOT_PROVEN`, not `ACCEPT_WITH_LIMITATIONS`.

## PHÁT HIỆN MỚI (NEW FINDINGS)

1. `tests/T02_contract/test_flow_03_openai_compat_scp_standard.py:535-565` — **MEDIUM**, observed in broader targeted run: the OpenAI streaming boundary test receives a response object with HTTP 200 although OTel captured an HTTP 503 policy-denied response. Next slot: OpenAI compatibility/streaming owner; reconcile response-vs-telemetry behavior without weakening the fail-closed assertion.
2. `scp/api_server.py:571-582` and container runtime — **LOW/MEDIUM**, `service_identity.config_hash` remains `unknown` in the isolated image because no explicit `SCP_CONFIG_HASH` is supplied and `.env` is excluded from the image. Next slot: release identity/config provenance owner; decide whether release images require a separate non-secret config hash. Not changed in N1/N2 because the requested finding was exact Git SHA and no secrets may be embedded.
3. Docker build emitted a BuildKit warning for existing `ENV OPENROUTER_API_KEY` (`Dockerfile:29`) — **MEDIUM**, no secret value observed or printed, but the Dockerfile still models a secret-shaped value as an image ENV. Next slot: capability/secret-handling owner; migrate provider secret injection to runtime-only configuration. Not changed because it is outside the requested identity/scheduler scope.
4. `scp/api_server_parts/lifespan.py:379-405` and runtime logs — **LOW**, optional `deep_audit_scheduler`/`attack_mode_monitor` registration logs are present, while the required watchdog jobs provide the observed first execution evidence. Next slot: background job registry owner; add a separate explicit first-execution/readiness contract for optional jobs if their execution is required for a future release profile. No readiness weakening was introduced.
5. `scp/api_server_parts/lifespan.py:413-416` — **LOW**, the runtime observer currently revokes readiness when a required job reports its first error; the registry itself resets the error counter after a successful later cycle. Next slot: scheduler policy owner; decide and document whether transient required-job errors should immediately block readiness or require a bounded consecutive-error threshold. The N1/N2 worker gate intentionally keeps the stricter fail-closed behavior.
