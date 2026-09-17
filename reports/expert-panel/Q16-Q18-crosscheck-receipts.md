# Q16 + Q18 Crosscheck / Receipt Contract Evidence

## Scope and authority

- Worker scope: `scp/runtime/multi_llm_crosscheck.py`, `scp/task_kernel_parts/taskkernel.py`, relevant T04/T05 tests, and this report.
- No commit was created. The repository HEAD used for the candidate proof is `16836f87c6200b07cc196a5e980b17325051865a` (`16836f8`), with unrelated pre-existing workspace changes preserved.
- Q16 does not modify the OpenAI route, CISA, S24 router, or FA04.
- Q18 preserves task state, lease, fencing, OCC, and journal behavior; it changes only completion evidence admission.

## Why chain / causal map

### Q16

`provider candidate discovery -> enabled check -> durable S35 eligibility -> family de-duplication -> provider.chat -> parse verdict -> consensus`

Previously the crosscheck path stopped at `enabled`, so a discovered local model in `STALE`, `RETIRED`, `COOLDOWN`, `QUARANTINED`, or another non-active state could still receive a direct `provider.chat` call. The patch calls the gateway's canonical `_provider_eligible` choke point when available and otherwise calls the durable `is_provider_model_eligible` authority. Providers with no endpoint/model lifecycle metadata retain the injected/cloud seam behavior; configured cloud providers outside the local registry remain eligible.

### Q18

`commit_completed/commit_verification_result -> receipt/profile contract -> lease/fencing re-check -> VERIFYING state/OCC check -> TASK_COMPLETED journal event -> release lease`

Previously `commit_completed(task, lease, "VERIFIED", evidence_ref)` could commit without a signed receipt. The patch disables this legacy form in explicit production/release profiles (`SCP_PRODUCTION_MODE`, `SCP_MODE`, `SCP_RELEASE_PROFILE`, or `SCP_API_PROFILE=core`). A signed `VerifierReceipt` must verify, and production requires `attempt_id`; any supplied attempt ID must equal the active lease attempt. Existing lease/fencing and version checks remain before the durable update.

## Changed files and line references

| File | Lines | Change |
|---|---:|---|
| `C:\Users\check\Downloads\scp\scp\runtime\multi_llm_crosscheck.py` | 15–79 | Lifecycle gate before direct `provider.chat`; unknown cloud/seam behavior preserved. |
| `C:\Users\check\Downloads\scp\scp\task_kernel_parts\taskkernel.py` | 1159–1180 | Production/release profile detection and receipt attempt extraction. |
| same | 1231–1273 | Signed receipt, production legacy denial, attempt binding, then existing lease/OCC commit path. |
| `C:\Users\check\Downloads\scp\tests\T04_kernel\conftest.py` | 12–17 | Isolates test profile from repository `.env`; explicit production tests opt in. |
| `C:\Users\check\Downloads\scp\tests\T04_kernel\test_verifier_receipt_provenance.py` | 600–732 | Production legacy/missing-attempt, valid receipt, forged/attempt mismatch negatives, compatibility boundary. |
| `C:\Users\check\Downloads\scp\tests\T05_gateway\test_multi_llm_crosscheck.py` | 139–210 | Six non-ACTIVE states, ACTIVE state, local exclusion, cloud fallback. |
| `C:\Users\check\Downloads\scp\tests\T05_gateway\conftest.py` | 33–34 | Explicit non-production/full test profile. |

## Targeted test evidence

Commands and observed results on the working tree:

```text
python -m pytest -q tests/T04_kernel/test_verifier_receipt_provenance.py \
  tests/T04_kernel/test_adversarial_kernel_flaws.py \
  tests/T04_kernel/test_lease_fencing_idempotency.py \
  tests/T04_kernel/test_transition_lease_fencing.py --tb=short
72 passed in 3.65s

python -m pytest -q tests/T05_gateway/test_multi_llm_crosscheck.py \
  tests/T05_gateway/test_multi_llm_crosscheck_concurrency.py \
  tests/T05_gateway/test_model_discovery.py --tb=short
33 passed in 14.42s

python -m pytest -q tests/T04_kernel tests/T05_gateway \
  --ignore=tests/T05_gateway/test_hedge_latency.py --tb=short
332 passed, 23 skipped in 92.62s
```

The complete T05 suite still has seven pre-existing hedge test failures in `tests/T05_gateway/test_hedge_latency.py` (`None`/`none` instead of expected fake-provider results). Re-running that file with the baseline T05 conftest reproduces the same `7 failed, 10 passed`; this task did not modify hedge code or that test. This is reported as an existing adjacent failure, not hidden or reclassified as a Q16 pass.

Portable reality runner result after the scoped product edits:

```text
75 PASS, 1 FAIL, 0 TIMEOUT, 0 ERROR (76 tests)
```

The single failure is unrelated `tests/reality-tests/reality_4-c-006.py` (dashboard LOC drift), outside this scope.

## Docker proof — exact candidate

The proof used a Docker build context assembled from repository HEAD plus only the scoped working-tree edits. The product files inside the image were hash-checked against the host:

```text
scp/runtime/multi_llm_crosscheck.py  be6d6c12298801441b4aa7108397b92de7b98501984b1b7d4d25dd498afc28d2
scp/task_kernel_parts/taskkernel.py fb236681b9fe31e7567d50c3ce6d5a1f09f92ee1a9569e2eda413b05e7479928
```

Candidate snapshot/archive identity:

```text
source HEAD: 16836f87c6200b07cc196a5e980b17325051865a
isolated context snapshot: a8c231e417bd284242598052a1b2167f1d461873
candidate-context.tar SHA-256: 8b00bfa0de945b8552e0bb21f8283ce605b7b91618fc90383f9e55d9180a3dff
image tag: scp-q16q18:16836f8
image digest: sha256:88eec45c61a8140373b8b92c75e93c7441518e773a3ad2b23d11ca0f9531b24c
```

The image was built with the exact source SHA build argument. It ran isolated on host port `127.0.0.1:18018`, with a named Docker volume `scp-q16q18-data`, read-only root filesystem, `tmpfs /tmp`, dropped capabilities, and `no-new-privileges`.

Observed service proof:

```text
GET /health: 200
GET /ready: 200
```

The in-container proof process used the actual image code and durable SQLite `ModelDiscoveryStore`/`TaskKernel` paths. It tested:

- `DISCOVERED`, `QUARANTINED`, `QUALIFIED`, `COOLDOWN`, `STALE`, and `RETIRED`: local provider calls = `0`, cloud calls = `1`, consensus = `missing_distinct_providers`, final = `None`.
- `ACTIVE`: local calls = `1`, cloud calls = `1`, consensus = `agree`, final = `PASS`.
- Production profile (`SCP_PRODUCTION_MODE=1`, `SCP_MODE=production`, `SCP_API_PROFILE=core`): legacy completion rejected; unsigned receipt rejected; signed but wrong-attempt receipt rejected; signed receipt bound to active attempt completed; journal hash chain = `true`.

Raw proof JSON is at `C:\Users\check\Downloads\scp\.tmp-q16q18\q16q18-proof.json`. Container startup, health/readiness, image identity, logs, final state, and teardown artifacts are under `C:\Users\check\Downloads\scp\.tmp-q16q18\`; the named container and volume were removed. `teardown-verify.log` is empty, which is the expected no-resource-left result.

## Missing pieces / limits

- Docker proof is a same-candidate in-container process proof, not a multi-node distributed recovery/chaos proof.
- The proof uses deterministic in-process provider seams; it does not call a real cloud LLM and does not claim external provider availability.
- The legacy compatibility API remains callable outside explicit production/release profiles. Release/production launch configuration must continue to set an explicit production/profile boundary; if an operator launches with a non-production profile, the compatibility contract intentionally remains available.
- `verifier_receipt.py` canonical signing format does not include `attempt_id`; this patch binds the signed receipt's attempt field to the durable active lease after signature verification. A future contract may need to include attempt ID in canonical bytes for stronger cryptographic binding.
- Full T05 remains blocked by seven adjacent pre-existing hedge failures; portable reality remains `75/76` because of unrelated dashboard LOC drift. No test was skipped/xfail-added or weakened.

## NEW FINDINGS

1. `C:\Users\check\Downloads\scp\tests\T05_gateway\test_hedge_latency.py:96,123,153,203,260,284,309` — **MEDIUM / adjacent existing failure** — the complete T05 run still returns seven fake-hedge failures even when the baseline T05 conftest is restored. Next slot: gateway/latency owner should reconcile `_provider_eligible`/provider seam metadata or the existing hedge harness contract; do not fold into Q16 without an independent reproduction and scope approval.
2. `C:\Users\check\Downloads\scp\tests\reality-tests\reality_4-c-006.py:155` — **LOW / adjacent existing failure** — portable reality runner reports dashboard LOC fallback drift outside Q16/Q18. Next slot: dashboard/reality-test owner should refresh the documented live/fallback LOC contract against current files.
3. `C:\Users\check\Downloads\scp\scp\core\verifier_receipt.py:75-110` — **MEDIUM / follow-up contract gap** — `attempt_id` is carried by `VerifierReceipt` but omitted from `canonical_receipt_bytes`; Q18 enforces active-lease equality after HMAC verification, but a future verifier-receipt slot should include attempt ID in canonical bytes and update compatibility/versioning deliberately.

## Handoff verdict

Q16/Q18 scoped changes are ready for an independent verifier review. No commit was made. The evidence supports `PASS_WITHIN_SCOPE` for the targeted tests and Docker proof only; it does not support a full release or production-readiness claim.
