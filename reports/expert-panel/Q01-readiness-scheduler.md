# Q01 — Readiness và Background Scheduler

- **Worker:** Q01 tuần tự, không commit.
- **Snapshot nguồn:** `16836f87c6200b07cc196a5e980b17325051865a` (`audit/runtime-guard-AUDIT-20260909`).
- **Candidate runtime:** exact HEAD archive + working-tree patch của scope Q01; patch fingerprint được ghi tại `.tmp/q01-docker/final-candidate-identity.txt`.
- **Scope:** `scp/api_server_parts/lifespan.py`, `scp/api/background_jobs.py`, `scp/api_server.py` (chỉ readiness response), T01/T02 contract tests. Không sửa Compose vì healthcheck hiện là liveness `/health`, còn `/readiness` là readiness contract riêng.
- **Verdict:** `CANDIDATE_NOT_PROVEN` / Q01 evidence **PASS_WITHIN_SCOPE**. Không phải release/customer-handoff verdict.

## 1. Problem statement và why-chain

### Readiness false-green

1. Vì sao `/readiness` có thể green sớm? Lifespan chỉ dựa vào scheduler registry đã `start_all()` và cờ started; trước đó cờ có thể phản ánh thread/task đã được tạo, không phải body đã chạy thành công.
2. Vì sao việc đó nguy hiểm? Required job có thể treo trước lần chạy đầu hoặc fail ngay sau khi thread được tạo trong khi endpoint đã báo ready.
3. Vì sao không đủ nếu chỉ kiểm tra thread? Thread sống không chứng minh execution success và không chứng minh job đã thực hiện side effect/guard cần thiết.
4. Correction: required job readiness có `first_execution_completed`; `/readiness` chỉ promotion sau khi mọi required job chạy thành công ít nhất một lần.

### Threshold policy không đồng bộ

1. Vì sao report cũ nói “five consecutive errors” nhưng observer revoke với `error_count > 0`? Registry lưu counter và reset sau success, nhưng policy threshold không có một hằng số contract chung.
2. Vì sao đây là missing piece? Test/report và runtime observer có thể kết luận khác nhau; failure sau first cycle có thể bị bỏ qua hoặc hiểu sai.
3. Policy được chọn: **fail-closed ngay tại 1 consecutive error của required job** (`REQUIRED_JOB_FAILURE_THRESHOLD = 1`). Đây là contract exact, phản ánh required job là một phần của readiness; không chờ retry để báo service ready.
4. Required job failure được giữ ở trạng thái revoked (`readiness_revoked=True`), có `last_error_type`, bounded `failure_id` và log. Success kế tiếp reset consecutive counter nhưng không tự xoá một readiness revoke đã quan sát; restart/restart registry là đường khôi phục rõ ràng.

### Method và scheduler ownership

1. `RealityJudge.schedule_background_jobs` không tồn tại trên canonical `RealityJudge`; không gọi method đó.
2. `BackgroundJobRegistry` là owner của required kernel watchdog jobs.
3. S23 `FreeDiscoveryScheduler` và S35 `ModelLifecycleScheduler` giữ nguyên là scheduler riêng; code không gộp chúng vào required registry và không dùng chúng để giả lập readiness.

## 2. Causal graph và coverage matrix

```text
registry.start_all()
  -> BackgroundJob.start()
     -> required fn() success/failure
        -> first_execution_completed / readiness_revoked / failure_id
           -> registry.status()
              -> lifespan monitor
                 -> app.state scheduler status
                    -> GET /readiness 503|200 + reason/jobs/failure_ids
                       -> runtime log + trace span + teardown evidence
```

| Nhánh nhân quả | Test/evidence | Trạng thái |
|---|---|---|
| Thread tạo nhưng first body chưa hoàn thành | `T01_boot::test_readiness_waits_for_required_first_execution` | COVERED |
| Required first execution success → readiness promotion | T01 cùng test + Docker readiness transition | COVERED |
| Required first execution failure → fail-closed | `T01_boot::test_required_scheduler_runtime_errors_are_observed` | COVERED |
| Failure threshold exact = 1 | T01 + T02 contract hằng số | COVERED |
| Failure metadata không chứa raw exception payload | T01 kiểm tra type/id; log chỉ type/id | COVERED |
| Failure hiện trong readiness state/log/trace | T01 + Docker injection | COVERED |
| Failure hiện trong log/trace | `caplog` T01 + Docker `final-v2-failure-injection.log` và OTel span trong output | COVERED trong scope |
| S23/S35 ownership không bị gộp | Existing lifespan path + Docker logs `[S23-DISCOVERY]`, `[S35-LIFECYCLE]` | COVERED static/runtime wiring |
| Required job được gọi lại sau interval thật | Không chờ interval 30/60 giây trong gate này | `UNPROVEN_BRANCH` |
| Recovery/restart sau revoked readiness | Chưa chứng minh bằng Docker restart; restart registry unit path chưa phải recovery proof | `UNPROVEN_BRANCH` |

## 3. Implementation correction

### `scp/api/background_jobs.py`

- Thêm `REQUIRED_JOB_FAILURE_THRESHOLD = 1` làm policy source-of-truth.
- `BackgroundJob` ghi nhận:
  - `first_execution_completed`
  - `error_count`
  - `failure_threshold`
  - `last_error_type`
  - random bounded `last_failure_id` (32 hex chars, không chứa exception text)
  - persistent `readiness_revoked`
- `readiness_status()` fail-closed nếu job chưa started, chưa có first success, hoặc required job đã bị revoke.
- Required watchdog jobs `kernel_lease_expiry` và `kernel_orphan_reconcile` chạy execution đầu đồng bộ với `initial_delay_seconds=0`; thread vẫn được tạo cho periodic cycles nhưng không còn là evidence duy nhất.
- Không duplicate execution đầu: loop bỏ qua cycle đầu sau synchronous first execution.
- Registry logs error type/failure id, không in exception payload.

### `scp/api_server_parts/lifespan.py`

- Sau `start_all()`, giữ scheduler state `starting`, không đặt `background_scheduler_started=True` ngay.
- Monitor 250ms:
  - pending khi required job chưa first-success;
  - promote khi mọi required job first-success;
  - revoke ngay khi `readiness_revoked` hoặc counter đạt threshold.
- Lưu `background_scheduler_failure_jobs` và `background_scheduler_failure_ids`; `readiness_reason` là `background_scheduler_pending_first_execution` hoặc `background_scheduler_failed`.
- Nếu judge chưa ready, monitor giữ reason `judge_initialization_pending` thay vì xoá reason khi scheduler đã sẵn sàng.

### `scp/api_server.py`

- `/readiness` vẫn yêu cầu judge + real registry + source identity.
- Khi lỗi, failure jobs/IDs được giữ trong app state và log/trace; readiness payload vẫn giữ contract tối thiểu hiện hữu và không đưa raw exception.

## 4. Test evidence

Môi trường local: Windows, Python 3.12.10, pytest 9.1.1.

| Gate | Kết quả | Evidence |
|---|---|---|
| Compile | PASS | `python -m py_compile scp/api_server.py scp/api_server_parts/lifespan.py scp/api/background_jobs.py tests/T01_boot/test_flow_01_boot_background_scp_standard.py tests/T02_contract/test_god_split_semantic_parity.py` |
| Targeted Q01 | PASS | `python -m pytest -q tests/T01_boot/test_flow_01_boot_background_scp_standard.py tests/T02_contract/test_god_split_semantic_parity.py --tb=short` → `67 passed` |
| T00 meta audit | PASS | `python -m pytest -q tests/T00_integrity/test_meta_audit.py --tb=short` → `39 passed` |
| Test-skill/DNA contract | `PASS_WITHIN_SCOPE` | `python tools/verify_scp_test_skill_contract.py`; profile SHA `31728d958b529bd5a86e3bb4c610d4ebc9a3405a092fd36ed7e1dc34df480bfe` |
| Missing SHA Docker negative | PASS fail-closed | `docker build --build-arg SCP_GIT_SHA=` exit `1`; evidence `.tmp/q01-docker/build-missing-sha.log` |
| Exact candidate build | PASS | Image `scp-q01-final-v2:16836f8`; image ID `sha256:405bec47d69121c02bab98db5d637003b06b8d8e902983aa3cec812866b91729`; build log `.tmp/q01-docker/build-final-v2.log` |

## 5. Mandatory Docker proof

### Candidate identity

- Exact source HEAD: `16836f87c6200b07cc196a5e980b17325051865a`.
- Clean candidate assembly: `git archive HEAD` + only the final Q01 patch for the scoped files.
- Archive hash: `sha256 5a1afb42f7de685b38357c605f26430668d4cae1d1369673f13eae005d4c621d`.
- Final patch hash before the last one-line required-name log refinement: `sha256 04f7de2d550b461a0bb9dc8bd2f79adea97492af9a626b569906ea68620c115a`; the final local patch was re-tested after that refinement. Docker runtime evidence below is from the immediately preceding equivalent candidate build and must be treated as candidate-overlay evidence, not a new image for the final one-line refinement.
- Identity record: `.tmp/q01-docker/final-candidate-identity.txt`.

### Isolated runtime

- Image built with `--build-arg SCP_GIT_SHA=16836f87c6200b07cc196a5e980b17325051865a`.
- Container: `scp-q01-final-v2-16836f8`.
- Host port: `127.0.0.1:18083` → container `8000`.
- Volume: `scp-q01-data-final-v2-16836f8` (dedicated, removed after proof).
- Test credentials were non-secret throwaway values passed only as environment variables; no raw repository secrets were printed or put in the report.

### Readiness transition

Observed from the final candidate:

```text
GET /readiness #1 -> HTTP 503
checks: judge=pending, background_scheduler=ok,
        background_scheduler_failure_jobs=[], background_scheduler_failure_ids={}
reason: judge_initialization_pending

GET /readiness #2 -> HTTP 200
status=ready, judge=ok, background_scheduler=ok, source_identity=ok, reason=null
```

The Docker log contains both required first execution evidence and promotion:

```text
[BackgroundJob] kernel_lease_expiry: first execution completed
[BackgroundJob] kernel_orphan_reconcile: first execution completed
[MACH1-FIX-1] Required background jobs completed first execution; scheduler readiness promoted
```

`/health` returned HTTP 200 and reported exact `service_identity.commit=16836f87c6200b07cc196a5e980b17325051865a`. S23/S35 evidence remained separate:

```text
[S23-DISCOVERY] FreeDiscoveryScheduler started ...
[S35-LIFECYCLE] not started (no operator-configured SCP_LOCAL_ENDPOINTS)
```

S23's catalog refresh was denied by the isolated egress/data policy (`PermissionError`); this is an observed optional subsystem limitation, not a readiness green-path substitution.

### Required-job failure injection

Inside the final candidate container, a real `BackgroundJob(required=True)` was injected with a function raising `RuntimeError`. Observed output (`.tmp/q01-docker/final-v2-failure-injection.log`):

```json
{
  "http_status": 503,
  "job_status": {
    "error_count": 1,
    "failure_threshold": 1,
    "first_execution_completed": false,
    "last_error_type": "RuntimeError",
    "last_failure_id": "3a16544b13304373ae1805de6bf3c1fb",
    "readiness_revoked": true,
    "ready": false,
    "required": true,
    "started": true
  },
  "readiness": {
    "checks": {
      "background_scheduler": "failed",
      "background_scheduler_failure_ids": {
        "docker_required_failure_probe": "3a16544b13304373ae1805de6bf3c1fb"
      },
      "background_scheduler_failure_jobs": ["docker_required_failure_probe"],
      "judge": "pending",
      "source_identity": "ok"
    },
    "reason": "background_scheduler_failed",
    "status": "initializing"
  }
}
```

The same failure was observable in logs with the same failure ID and the monitor emitted:

```text
[BackgroundJob] docker_required_failure_probe: error #1 — RuntimeError (failure_id=...)
[BackgroundJob] docker_required_failure_probe: 1 consecutive errors — readiness revoked
[MACH1-FIX-1] Required background job failed after startup; readiness revoked: docker_required_failure_probe (failure_ids={...})
```

The OTel `GET /readiness` span recorded HTTP 503; this supplies a trace-level independent observation of the fail-closed response.

### Teardown

`docker stop`, `docker rm`, and dedicated volume removal completed. Final cleanup verification recorded `cleanup=verified` in `.tmp/q01-docker/final-v2-cleanup-verification.log`; no final candidate container or volume remained.

## 6. Limitations / unresolved blockers

- This is not full `python -m pytest tests/ -v`; only Q01 targeted tests, T00 meta audit, and the mandatory contract verifier were run after the final patch. Therefore no full-suite or release claim is made.
- Docker proof used the exact source HEAD archive plus uncommitted scoped patch, not a commit SHA containing the patch. The image's embedded SHA identifies the source archive HEAD; the patch fingerprint separately identifies the candidate overlay.
- The failure injection executes an in-process TestClient inside the container, which proves the real registry/lifespan/readiness/log/trace path but is not an external HTTP injection endpoint. It deliberately avoids adding a production failure-injection route.
- The required watchdog periodic intervals (30s/60s) were not awaited in Docker; only startup first execution and immediate injected failure were proven.
- Recovery after revoked readiness (successful restart/registry reinitialization) was not proven; the chosen policy is fail-closed until restart, and no automatic recovery was introduced.
- S23 catalog refresh failure under isolated egress and S35 disabled-by-no-operator-endpoint are observed and expected within this proof profile; they are not required registry jobs.
- The existing Docker warning about `Dockerfile:29 ENV OPENROUTER_API_KEY` is outside Q01 and remains an independent secret-handling finding.
- T00 was re-run after final code and passed 39 tests. A prior baseline T00 run on the dirty workspace exposed an unrelated pre-existing syntax error in `scp/api/routes/stream_routes.py:47` (smart quote); it was outside Q01 and was not modified. The final Q01-scoped T00 run did not reproduce it due to test/import state.

## PHÁT HIỆN MỚI (NEW FINDINGS)

1. `scp/api/background_jobs.py:40-44, 64-112` — **HIGH (policy clarification applied)**: prior implementation/report described five consecutive errors, while readiness revoked on `error_count > 0`. Q01 now makes the fail-closed threshold exact and machine-readable as `REQUIRED_JOB_FAILURE_THRESHOLD = 1`; next slot: scheduler/reliability owner to approve whether a future profile may use a different threshold, with a separate readiness contract and proof.
2. `scp/api_server_parts/lifespan.py:434-466` — **MEDIUM**, Docker final proof observed S23 free catalog refresh denied under the isolated data/egress profile while S35 stayed disabled without operator endpoints. Next slot: S23/S35 owner; provide independent runtime proof for their optional refresh/lifecycle contracts without coupling them to `/readiness`.
3. `Dockerfile:29` and final image inspection — **MEDIUM**, BuildKit reports `SecretsUsedInArgOrEnv` for the existing `OPENROUTER_API_KEY` image ENV placeholder. No value was disclosed and Q01 did not modify it. Next slot: capability/secret-handling owner; move provider secret injection to runtime-only configuration.
4. `scp/api_server_parts/lifespan.py:407-466` — **MEDIUM**, required-job recovery after revoked readiness is intentionally not automatic and was not Docker-proven; a successful later cycle does not clear `readiness_revoked`. Next slot: runtime/recovery owner; define and prove an explicit restart/reconciliation contract before adding recovery.
5. `scp/api_server.py:650-672` — **LOW**, readiness failure identifiers are useful for correlating logs/traces but are process-local random IDs, not durable trace IDs across restart. Next slot: observability owner; decide whether a durable run/trace ledger correlation is required, without exposing raw exception payloads.
6. `tests/T00_integrity/test_meta_audit.py:544` and baseline `scp/api/routes/stream_routes.py:47` — **MEDIUM (out of scope)**, an earlier baseline T00 invocation failed import on a smart quote syntax error in a concurrently modified file. It was not introduced or fixed by Q01. Next slot: stream-route owner; reconcile the concurrent tree and repeat full T00 before any release claim.

## 7. Evidence classification

- **OBSERVED:** targeted test outputs, exact T00 output, contract verifier output, Docker image build, HTTP 503→200 readiness transition, first execution logs, injected required failure, HTTP 503 failure payload, OTel HTTP 503 span, and teardown.
- **SUPPORTED_INFERENCE:** readiness promotion is causally gated by required first execution because the Docker log ordering and transition show both; the test also blocks execution before probing.
- **UNPROVEN:** full suite, release gates, periodic 30/60s cycles, restart recovery, production TLS/provider behavior, multi-node behavior, and customer-handoff readiness.

**SCP status:** `CANDIDATE_NOT_PROVEN`, not `RUNTIME_PROVEN` and not release-ready.
