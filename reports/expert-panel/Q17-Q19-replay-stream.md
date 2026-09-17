# Q17 + Q19 — Evidence Replay and Stream Boundary

## Scope and snapshot

- Worker: Slot B (Q17 + Q19).
- Candidate snapshot used for local work: `16836f87c6200b07cc196a5e980b17325051865a`.
- No commit was created.
- Files changed in scope: `scp/autofix/evidence_replay.py`, `scp/autofix/runner_phases/semantic_equiv.py`, `scp/api/routes/stream_routes.py`, and scoped Q17/Q19 tests.
- Existing unrelated workspace changes were preserved and not used as authority.

## Why chain and missing piece

1. Q17 was not a verifier: `EvidenceReplay.verify()` returned hard-coded `{"ok": True, "status": "VERIFIED"}`, `compute_bug_signature()` returned `mock_signature`, and classification returned a mock role without executing an observation.
2. A PASS from a test is not sufficient evidence unless Buggy/Candidate/Gold states are actually observed and tied to the exact task attempt and source identity.
3. Receipt cryptography existed for TaskKernel, but replay did not bind its result to that receipt or to exact `task_id`, `attempt_id`, `source_sha`, and provenance.
4. Q19 directly called `RealityJudge.judge()` and emitted `final_answer` before the canonical `/ask` adapter's lease, verifier, signed receipt, and governance boundary.
5. The missing safety property was therefore an observable, fail-closed boundary: no receipt/identity mismatch means no `VERIFIED`; no canonical policy verdict means no candidate content.

## Implementation

### Q17

`scp/autofix/evidence_replay.py` now:

- Uses the existing `scp.sandbox_evaluator.evaluate()` only; it does not execute replay source in the agent process or fall back to direct `subprocess` execution.
- Evaluates Buggy (B), Candidate (S), and Gold (G) in isolated evaluator workspaces.
- Records bounded observable results: evaluator identity, executed flag, verdict, return code, reason, stable output digest, stdout/stderr excerpts and duration.
- Rejects missing/unsafe test commands, missing source states, unsafe paths, missing tests, missing identity, and provenance whose `task_id`, `attempt_id`, or `source_sha` does not exactly match.
- Computes a deterministic observation digest over exact identity, provenance, command digest and B/S/G observations.
- Verifies HMAC `VerifierReceipt` with exact task, attempt, evidence reference, verdict and observation digest. Missing/expired/unsigned/tampered/mismatched receipts return `UNVERIFIED`; there is no manufactured `VERIFIED` fallback.
- Removes the environment-variable gold seeding path and the old mock signature/role implementation.

`semantic_equiv.py` now:

- Parses both ASTs and fails closed on missing/invalid source.
- Requires an explicit target function when comparing changed source.
- Marks deleted/missing target functions `critical`.
- Detects changes to sibling functions/classes and statements outside the declared bug location as `over_broad`.
- Returns `equivalent=True` only for the narrow structural contract (changes confined to the declared target); runtime behavior remains the sandbox/reality verifier's responsibility.

### Q19

`scp/api/routes/stream_routes.py` now:

- No longer calls a route-local `RealityJudge` directly.
- Delegates generation and verification to the canonical `AskKernelAdapter.run_rag()` path used by `/ask` and OpenAI compatibility.
- Keeps the SSE transport shape and auth boundary.
- Emits metadata progress frames only before the canonical result.
- Emits a single terminal `complete` event only when the canonical result is not held.
- Emits `status=withheld`, `candidate=null`, and `final_answer=null` for `UNKNOWN`, `FAIL`, `ESCALATE`, `KILL`, `REJECT`, `DENY`, `FLAGGED`, or unavailable kernel/readiness. Internal exception text and candidate text are not sent.
- Parses/serializes each SSE frame as one complete JSON event; no token splitting or token-boundary loophole is used.

## Tests

Scoped tests run on the candidate working tree:

```text
72 passed in 50.89s
```

Profile included:

- `tests/T03_capability/test_flow_07_autofix_scp_standard.py`
- `tests/T03_capability/test_flow_10_streaming_scp_standard.py`
- `tests/T03_capability/test_m2_architecture_backlog.py`
- `tests/T03_capability/test_q17_q19_replay_stream.py`

Direct Q17/Q19 tests cover:

- real B/S/G sandbox observations;
- missing receipt and mismatched attempt/digest rejection;
- positive signed receipt verification;
- raw SSE parsing with complete frames;
- all withheld verdict vocabulary values and candidate absence;
- authenticated and unauthenticated stream boundaries.

A pre-fix scoped run exposed five stream assertions that expected an always-ready legacy direct-judge path. They were updated to assert the new lower-assurance policy: readiness/kernel holds are terminal `withheld` events with no candidate. No test was skipped, xfailed, deleted, or loosened into accepting candidate content.

## Docker proof

Docker proof used an exact-SHA image build:

- Image: `scp-q17q19:16836f8`
- Build arg: `SCP_GIT_SHA=16836f87c6200b07cc196a5e980b17325051865a`
- Archive SHA-256: `31ee1f0de3a94b22da62ee451fae52336e6ffeaa514427c07d3adc305eaacd73`
- Isolated host port: `127.0.0.1:18080 -> container:8000`
- Container: `scp-q17q19-proof`
- Runtime secrets were generated only in shell and are not included in this report or artifacts.

Observed Docker lifecycle:

1. First start failed closed because `SCP_CAPABILITY_SECRET` was absent. This is a real startup guard, not suppressed.
2. Second start failed closed because boot contract required `SCP_JWT_SECRET` and `SCP_ADMIN_KEY`.
3. Third start supplied all required secret boundaries and reached `Application startup complete`.
4. `/health` returned `200` with exact service identity SHA.
5. `/readiness` returned `200` and `status=ready`; judge/background scheduler/source identity checks were `ok`.
6. Authenticated `POST /v105/ask/stream` returned valid SSE frames and a final `status=withheld`, `verdict=FAIL`, `governance_decision=KILL`, with both `candidate` and `final_answer` null. The canonical kernel path was unavailable for that request and the stream withheld content rather than falling back to direct judge output.
7. Unauthenticated stream returned `401`.
8. OpenAI boundary probe without a valid bearer credential returned `401`.
9. The proof container and image were removed after the run.

Raw redacted artifacts and hashes are under:

```text
C:\Users\check\Downloads\scp\.docker-q17q19-16836f87c6200b07cc196a5e980b17325051865a\
```

Important Docker limitation: the proof established real service startup/readiness/auth/SSE withholding, but the positive stream content path was not proven in Docker. The canonical adapter returned a fail-closed hold (`ledger_status=BLOCKED`) rather than a verified answer; this is evidence of safe withholding, not evidence that a positive candidate flow succeeds.

## Evidence map

| Claim | Evidence | Level | Verdict |
|---|---|---:|---|
| Replay is not hard-coded VERIFIED | source + negative/positive direct tests | A/B | VERIFIED within scope |
| B/S/G are observed in the existing sandbox evaluator | direct test result includes three executed evaluator observations | C | VERIFIED within test workload |
| Signed receipt binds task/attempt/source/provenance/observation | direct positive and tamper tests | B/C | VERIFIED within test workload |
| Semantic equivalence rejects deletion/over-broad changes | direct function probes + scoped test import | B | VERIFIED within tested cases |
| Stream uses canonical adapter boundary | source inspection + 72 scoped tests + Docker route trace | B/C | VERIFIED within tested workload |
| Withheld verdicts do not emit candidate | direct SSE parser tests + Docker raw SSE | C | VERIFIED within tested vocabulary |
| Positive Docker stream candidate flow | no successful positive canonical adapter response observed | C | INSUFFICIENT |

## Remaining limits / blockers

- `post_fix_verify.py` still contains legacy comments and exception handling describing replay/semantic phases as fail-open in places outside the assigned file scope. This worker did not broaden scope into that orchestrator; its caller should be audited before a release claim.
- The existing gold dataset contract is not a cryptographically signed external authority. A signed receipt authenticates the observation binding; it does not independently prove that the Gold source is substantively correct.
- Docker startup logs showed pre-existing permission warnings for Windows-style `C:` paths and a data-partitioner database-open warning. They did not prevent readiness, but they remain runtime findings for the next slot.
- Full repository test suite and release gate were not run in this worker because the repository contained concurrent unrelated changes and the task required scoped Slot B work.

## PHÁT HIỆN MỚI (NEW FINDINGS)

| ID | File:line | Severity | Observation | Next slot |
|---|---|---|---|---|
| NEW-Q17-01 | `scp/autofix/runner_phases/post_fix_verify.py:489-524` | HIGH | Replay import/runtime errors are still labeled `fail-open` and mark `UNVERIFIED` for escalation. The new replay module itself fails closed, but the caller can still treat missing replay as a non-rollback path. | Next AutoFix/learning slot: reconcile orchestrator policy so missing/mismatched replay cannot promote a fix; preserve human-review/rollback semantics. |
| NEW-Q19-01 | `scp/ask_kernel_adapter.py:440-449` | HIGH | Canonical `_safe_response` preserves an existing non-empty `final_answer` when verification fails if it already does not start with `[SCP:`. Q19 stream clears it at its boundary, but other adapters/compatibility paths require an independent audit. | Next TaskKernel/API boundary slot: prove every public response shape clears candidate fields on failed/unknown verification. |
| NEW-RUNTIME-01 | Docker runtime logs | MEDIUM | Candidate container reached readiness but logged `PermissionError: [Errno 13] Permission denied: 'C:'` in free-discovery/evolution background paths and a cache DB open warning. | Next runtime/startup slot: isolate path configuration and make nonessential background failures observable without affecting readiness semantics. |

## Final verdict

`Q17`: VERIFIED within the direct sandbox/replay/receipt test scope; missing/mismatched evidence is fail-closed.

`Q19`: VERIFIED within direct parser, scoped route tests and Docker negative withholding/auth/readiness scope. Positive Docker content flow is `INSUFFICIENT`, not claimed.

Overall release/customer-handoff status: **NOT CLAIMED**. No commit was created.
