# P2 — OpenAI stream governance và S35 ACTIVE routing

## Phạm vi và snapshot

- Slot: A
- Scope: `scp/api/routes/openai_compat.py`, `scp/llm_gateway/client.py`, `scp/llm_gateway/discovery.py`, OpenAI/S35 contract tests.
- Exact working-tree HEAD quan sát khi test: `8428114073571e169f8ab9e74a7dcc13d740ed88`.
- Không commit. Dirty diff ngoài scope được giữ nguyên.
- Đây là evidence của dirty working tree; chưa phải release/final SHA evidence.

## Vấn đề và causal graph

```mermaid
flowchart TD
    A[POST /v1/chat/completions] --> B[auth Depends(get_current_user)]
    B --> C[parse OpenAI messages]
    C --> D[canonical AskRequest]
    D --> E[readiness + SCP_ASK_KERNEL_ENABLED gate]
    E --> F[AskKernelAdapter.run_rag]
    F --> G[lease / TaskKernel / _ask_impl / evidence verification]
    G --> H{safe response}
    H --> I[OpenAI JSON envelope]
    H --> J[OpenAI SSE envelope]
    E --> K[503 or kernel-blocked response]
    K --> L[no provider token emitted]

    M[operator SCP_LOCAL_ENDPOINTS] --> N[ModelDiscoveryStore SQLite]
    N --> O{state == ACTIVE?}
    O -->|yes| P[LLMGateway provider pool]
    O -->|no| Q[exclude provider]
    R[unknown configured cloud provider] --> P
    S[prompt/request endpoint] -. never feeds .-> M
    P --> T[existing egress / zero-cost guards]
```

## Implemented controls

### V-A rejection remediation

1. `UNKNOWN` is now a hard epistemic hold at the OpenAI boundary. Its canonical `final_answer`/candidate is discarded for both JSON and SSE.
2. For stream requests, every policy-held result (`UNKNOWN`, `ESCALATE`, `REJECT`, `DENY`, `FAIL`, `FLAGGED`) returns an OpenAI-shaped `503 policy_denied` before `StreamingResponse` construction; therefore there are zero SSE content deltas. Kernel-gate markers (`KERNEL_GATE_UNAVAILABLE`, kernel error guard, or `kernel_gate` provenance) return a separate OpenAI-shaped `503` before choices/SSE construction. No withheld marker or generic refusal is emitted as stream content.
3. Direct injection matrix covers `SENSITIVE-CANDIDATE` under canonical `UNKNOWN`, `ESCALATE`, `REJECT`, `DENY`, `FAIL`, `FLAGGED`, and governance `UNKNOWN`/`FAIL`/`FLAGGED`; every JSON and stream invocation returns policy-denied 503 with no choices/content. Kernel-disabled stream is separately covered.
4. `ESCALATE` and `KILL` remain explicit policy holds; no assertion was weakened.

### OpenAI compatibility

1. Removed direct `get_judge()` and `LLMGateway.chat_stream()` execution from the OpenAI route path.
2. Added lazy `_run_canonical_ask()` delegation to the same `api_server._get_ask_kernel_adapter()` and `AskKernelAdapter.run_rag()` used by `/ask`.
3. Applied the same readiness and `SCP_ASK_KERNEL_ENABLED` gates before generation. Adapter initialization failure uses the kernel-blocked response.
4. Non-stream and stream responses are translated only after canonical path completion. SSE is formatting of the already-safe/withheld answer; it does not start a provider request and cannot emit pre-policy tokens.
5. Pipeline exceptions return a structured OpenAI 503 without internal exception text.
6. Auth remains dependency-first. Invalid auth is rejected before body/generation handling.

### S35 ACTIVE routing

1. Added durable lifecycle eligibility check in `discovery.py`.
2. Matching supports discovery endpoint root and configured OpenAI-compatible `/v1` variant.
3. Exact model is eligible only for `ACTIVE` durable state when endpoint has discovery rows.
4. `DISCOVERED`, `QUARANTINED`, `QUALIFIED`, `COOLDOWN`, `STALE`, and `RETIRED` are excluded.
5. Explicit local endpoints with no durable discovery row remain blocked until discovery/probing produces `ACTIVE`.
6. Providers outside the local discovery registry retain existing behavior, preserving configured cloud-provider compatibility.
7. Both `LLMGateway.chat()` and `chat_stream()` filter the provider pool at the routing boundary. Empty eligible pool fails closed.
8. Provider endpoints remain operator configuration only; no prompt/request/model response value is used to add endpoints.
9. Gateway may open the same durable `SCP_DATA_DIR/model_discovery.sqlite3` authority when local endpoints are configured; caller-supplied stores remain non-owned and are not closed by gateway.

## Tests and evidence

### Passing targeted evidence

- `python -m pytest -q tests/T05_gateway/test_model_discovery.py --tb=short`
  - `16 passed in 34.82s` (before the two explicit routing tests below).
- Explicit lifecycle/routing tests added in the scoped test file:
  - `2 passed in 0.61s` for all non-ACTIVE exclusions, `/v1` endpoint matching, unknown-cloud preservation, and fallback around a blocked local provider.
- Combined discovery + egress contract recheck:
  - `python -m pytest -q tests/T05_gateway/test_model_discovery.py tests/T05_gateway/test_llm_egress_policy.py --tb=short`
  - `28 passed in 15.18s`.
- Isolated fail-closed OpenAI recheck after the ESCALATE withholding correction:
  - `1 passed in 233.91s` for non-streaming output; both `KILL` and `ESCALATE` are now withheld at this boundary.
- V-A direct canonical injection tests:
  - Earlier isolated run: `2 passed in 1.52s` for UNKNOWN `SENSITIVE-CANDIDATE` and kernel-disabled stream withholding.
  - Final focused V-A run: `11 passed in 1.67s`, including UNKNOWN stream error behavior, verdict/governance matrix, REJECT + UPHOLD candidate withholding, and kernel-disabled stream.
- Final combined targeted V-A run: `37 passed in 17.05s` including discovery, egress, and the full direct policy matrix.
- Exact OpenAI contract file after final non-stream policy correction:
  - `39 passed in 807.61s (13:27)`.
  - The prior `29/1` and `31/31` results are superseded by this fresh exact-file result; no historical failure was relabeled.
- Provider/discovery/fallback subset after ACTIVE integration:
  - `27 passed, 1 failed` initially due to a synthetic provider exposing no `base_url`; fixed by preserving model-only dynamic test seams outside local discovery registry.
  - Recheck of `test_gateway_propagates_budget_free_priority`: `1 passed in 1.74s`.
- Final combined targeted command:
  - `python -m pytest -q tests/T05_gateway/test_model_discovery.py tests/T05_gateway/test_provider_failover.py tests/T05_gateway/test_llm_gateway_fallback_contract.py tests/T02_contract/test_flow_03_openai_compat_scp_standard.py --tb=short`
  - observed: `55 passed, 1 failed in 626.59s`.
  - Remaining failure: `test_openai_compat_handles_streaming_false` because this full process run observed a kernel-backed response with `governance_decision=ESCALATE` while the test process state was affected by repeated long-lived TestClient/kernel evidence. This is not a token leak; the response remained withheld. The test is not claimed green.
- Isolated readiness-aware translation recheck:
  - `2 passed in 253.52s` for tools and JSON translation tests.
- OpenAI canonical/no-leak subset before combined run:
  - `3 passed, 1 failed`; after readiness fixes and structured error seam: canonical forwarding, stream translation, and injected pipeline 503 passed.
- `python -m py_compile scp/api/routes/openai_compat.py scp/llm_gateway/client.py scp/llm_gateway/discovery.py tests/T02_contract/test_flow_03_openai_compat_scp_standard.py`: passed.
- `git diff --check` for scoped files: no whitespace errors.
- `python tools/t00_meta_audit.py --help` (T00 execution): `All integrity checks passed (0 new regressions)`.
  - T00 also reported pre-existing baseline debt and L4 warnings; those are not newly created by this scope.

### Explicit no-token-leak result

The stream generator is created only for a non-withheld canonical result. Any `UNKNOWN`, `ESCALATE`, `REJECT`, `DENY`, `FAIL`, or `FLAGGED` result returns an OpenAI-shaped `503 policy_denied` before `StreamingResponse` creation. Kernel-unavailable returns a separate OpenAI-shaped 503 before choices/SSE creation. Direct tests parse/inspect the actual JSON error path and prove no refusal/candidate content is present; no provider/gateway stream call exists in the generator. A full external runtime proof is not claimed.

## Limitations and unresolved blockers

1. The target request asked exact `/ask` vs OpenAI equivalence under egress deny, kernel unavailable, and invalid auth. Invalid-auth and kernel/readiness behavior are covered by the route's auth-first and readiness contracts; direct canonical injection now covers UNKNOWN/sensitive candidate and kernel-disabled stream. A full same-prompt runtime matrix remains unproven because the existing OpenAI test file is dirty from another worker and real TestClient startup is long/variable. Explicit local routing lifecycle tests are present in `tests/T05_gateway/test_model_discovery.py`.
2. No full suite, runtime service audit, GitHub gate, or end-to-end external provider proof was run. Local targeted tests are not release evidence.
3. Full OpenAI tests are slow because each `TestClient` starts the real judge/kernel lifecycle. The exact OpenAI file was rerun after the SSE correction and passed `31/31`; runtime logs still expose unrelated scheduler/history/telemetry warnings listed below.
4. Test logs exposed an unrelated existing background task error: `RealityJudge` has no `schedule_background_jobs` while `lifespan.py` calls it. This is outside Slot A scope and was not changed.
5. Test logs also showed unrelated existing `history hook failed: only verified records may enter the ledger`, OpenTelemetry closed-stream export noise, external-trust warnings, and scheduler duplicate-registration warnings.
6. The pre-existing worker's `client.py` streaming implementation remains part of the dirty diff; this slot only changed its routing boundary and did not redesign provider stream retry semantics.

## Verdict

`TEST_BOUND_PARTIAL` / `BLOCKED_FOR_RELEASE`: canonical delegation, ACTIVE eligibility, UNKNOWN/ESCALATE/REJECT/DENY/FAIL/FLAGGED verdict withholding, governance UNKNOWN/FAIL/FLAGGED withholding, and kernel-disabled zero-content behavior are implemented and directly/targetedly exercised. T00 reports no new integrity regressions. Discovery/egress contracts, V-A direct tests, and the exact OpenAI contract file are green (`39/39`); no `DONE`, production-ready, or release-ready claim is made because full-system/release evidence is outside scope.

## PHÁT HIỆN MỚI (NEW FINDINGS)

| File:line | Severity | Slot | Finding |
|---|---:|---|---|
| `scp/api_server_parts/lifespan.py:144` | Medium | A / out-of-scope | Background scheduler invokes missing `RealityJudge.schedule_background_jobs`; runtime logs show unhandled task exception. This can reduce background governance/observability readiness, but does not bypass the new OpenAI kernel gate. |
| `scp/api/routes/openai_compat.py:91-105` | Low/Medium | A | Canonical delegation depends on process-global `app.state.judge_ready`; when startup readiness is pending, OpenAI returns 503 rather than entering the adapter. This is fail-closed but causes compatibility clients to retry/observe availability failures during boot. |
| `scp/api_server_parts/_ask_impl.py:515-525` | Medium | A / out-of-scope | Runtime targeted logs show `history hook failed: only verified records may enter the ledger` on a kernel-withheld OpenAI path. It is logged and does not release an answer, but evidence/auxiliary ledger completeness is degraded. |
| `tests/T02_contract/test_flow_03_openai_compat_scp_standard.py` | Low | A | Historical assertions assumed direct judge `KILL`; canonical AskKernelAdapter can correctly return `ESCALATE` for unverified/insufficient evidence. The contract now asserts withholding, while direct tests additionally prove UNKNOWN sensitive candidates and kernel-disabled streams do not emit content. Full process stability remains unresolved. |
| `scp/api/routes/openai_compat.py:189-247` | High | A | V-A finding fixed: UNKNOWN/policy-held candidate answers and kernel-gate withheld markers could otherwise enter OpenAI JSON/SSE translation. The boundary now returns 503 before policy-held stream construction; exact SSE contract rerun passed 31/31. |
| `scp/llm_gateway/client.py:728-760` | Low | A | Gateway-owned discovery SQLite is opened when `SCP_LOCAL_ENDPOINTS` is configured. Startup/open failures block local providers, as intended, but lifecycle scheduler/gateway share is not runtime-proven across process restart in this slot. |
