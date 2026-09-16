# S1 — openai_compat streaming: 3 stale-contract tests (503 vs 200)

## Claim under test
`tests/test_m2_adversarial_challenger.py` has 3 streaming tests that fail with a
503 where they expect a successful stream. Determined: real product bug, or stale
test assuming the pre-canonical proxy contract?

## Verdict
**Scenario (b): code is correct; the tests encode a stale (pre-canonical)
contract.** This is a PRE-EXISTING condition at HEAD, not a wiring regression,
and **not a security bug**. The 3 tests were repaired at the sanctioned
harness seam; the fail-closed assertions of the current contract are unchanged
and now provably cover the *verified→200 SSE* branch, which the complementary
*unavailable→503 / withheld→503* branches (T02/T03) do not.

## Scope / provenance
| Field | Value |
|---|---|
| Repo | `C:\Users\check\Downloads\scp` |
| Branch | `audit/runtime-guard-AUDIT-20260909` |
| HEAD | `018ed2176a4a17ff3993e5c6cd3555b05943d520` |
| Wiring commit (root cause) | `5e54de3` — "fix(api): enforce fail-closed OpenAI policy boundary and ACTIVE model routing [P2]" (2026-09-15 02:16 +07, ancestor of HEAD) |
| Files touched | `tests/test_m2_adversarial_challenger.py` only (sha256 `229417707e53a2d571f3fccb56ed692645e7c9ec4107f57482a808032db64ee4`) |
| Files NOT touched | `scp/api/routes/openai_compat.py` (unchanged — code correct) |
| DNA skills loaded (sha256) | scp-dna `4aada0be4873…`, scp-reality-verifier `a9d65ce53b18…`, scp-web-orchestration-safety `2a4c98023709…` |
| Timestamp (evidence capture) | 2026-09-15T21:18Z |
| Commit/push | NOT performed (audit-first, per instruction) |

## Reality discrepancy vs instruction (DNA #26)
The finding labeled the failures "503-vs-203". Reality (observed, not assumed):
no `203` occurs. The three failures are **503 vs 200 / (200,400,422)**:
- `test_streaming_missing_or_null_model:282` → `assert 503 == 200`
- `test_streaming_invalid_temperature:309` → `assert 503 in (200, 400, 422)`
- `test_stream_aborted_midway:449` → `assert 503 == 200`

## Causal map (why 503)
1. The m2 `api_client` fixture builds a **bare** `FastAPI()` app, includes only
   `openai_router`, and monkeypatches the **LLM gateway provider chain**
   (`gw._provider_chain`) → the pre-`5e54de3` contract where `/v1/chat/completions`
   `stream=True` was a thin proxy over `provider.chat_stream` (loopback SSE), which
   returns 200.
2. Since `5e54de3`, `openai_chat` routes **every valid request** through
   `_run_canonical_ask` (`openai_compat.py:62-112`), i.e. the canonical
   run_rag/kernel/governance path shared with `/ask`. That boundary is
   deliberately **fail-closed**:
   - `app.state.judge_ready` False → `503 judge_initializing` (`:94-103`);
   - kernel disabled / adapter `None` → kernel-gate unavailable (→ 503 server_error);
   - withheld verdict/governance → `503 policy_denied` **before** any SSE (`:216-227`, `:244-252`).
3. The m2 bare fixture **never runs the api_server lifespan**, so the global
   `scp.api_server.app.state.judge_ready` is False and no adapter is registered.
   The provider-chain patch is now **dead code** for the route path. Net effect:
   any valid stream request → `_run_canonical_ask` → 503 → translated to
   `_openai_error("Upstream judge pipeline unavailable", server_error, 503)`.
4. The only valid-body stream tests in the file are these 3; the 400-level tests
   (`missing messages`, malformed JSON, non-dict root, wrong messages shape)
   return **before** `_run_canonical_ask`, so they stayed green. The 3 provider-level
   async tests (`test_empty_stream_graceful_handling`, `test_empty_deltas_ignored`,
   `test_multi_token_burst_intact`) call `provider.chat_stream` **directly**
   (bypass the route), so they are independent of the canonical path and stayed green.

This matches the Q12 evidence: reverting the wiring file to HEAD still fails → the
test predates the wiring, confirming (b).

## Security analysis (no leak, fail-closed preserved)
The route constructs the SSE body **only from `final_answer` after** the
withheld/kernel checks. A policy-denied / UNKNOWN / kernel-gate answer is emitted
as a 503 `policy_denied`/`server_error` **before** `StreamingResponse` exists, so a
client cannot observe refusal or candidate text as content deltas. This is already
independently pinned by:
- `T02 .../test_flow_03…::test_unknown_candidate_is_withheld_nonstream_and_stream`
  and `::test_withheld_verdict_or_governance_returns_policy_error`
  (assert `"SENSITIVE-CANDIDATE" not in body`, both stream and non-stream);
- `T02 …::test_kernel_disabled_stream_returns_no_content_tokens`;
- `T03 …/test_m2_architecture_backlog.py::test_openai_compat_stream_endpoint`
  (isolated app, adapter unavailable → asserts **503** + `"Tell me a story" not in response.text`).

So the failures do **not** reveal a leak; they reveal that the m2 file was the last
consumer still asserting the obsolete *proxy→200* happy path without supplying a
non-withheld canonical result.

## The fix (harness, strictness-preserving)
In `tests/test_m2_adversarial_challenger.py` only:
1. Added `from scp.api.routes import openai_compat` and a module helper
   `_inject_verified_canonical(monkeypatch, answer=…)` that stubs
   `openai_compat._run_canonical_ask` with a non-held canonical result
   (`verdict=PASS`, `governance_decision=UPHOLD`, non-empty `final_answer`).
   This is the **same seam the T02 contract suite uses** (`_inject_canonical`), so
   the judge/kernel (out of scope) is excluded while openai_compat's own boundary
   logic (model defaulting, temperature tolerance, withheld check, SSE
   translation, client-abort lifecycle) still runs for real.
2. Applied it to the 3 tests. Assertions and payloads are **unchanged**:
   - `test_streaming_missing_or_null_model` → still expects 200 + `text/event-stream`
     for missing and `null` model (proves model default at `:153` + SSE shape).
   - `test_streaming_invalid_temperature` → still expects `(200,400,422)` and `!=500`
     for `"super_hot"` and `-2.5`. Note: the route does not forward `temperature`
     to the canonical call, so a bad value is gracefully ignored → 200. The "never
     500" intent is preserved.
   - `test_stream_aborted_midway` → injects a multi-word verified answer so the
     first SSE frame is a content delta; still reads one line, aborts, and asserts a
     subsequent request returns 200 (proves statelessness after client disconnect).

No delete/skip/xfail, no lowered threshold, no fail-open conversion. Nothing else
in the file changed (27 previously-green tests untouched).

## Verification (evidence)
| Check | Command | Result |
|---|---|---|
| Targeted (all 3 green) | `pytest -q tests/test_m2_adversarial_challenger.py` | **30 passed**, exit 0 |
| openai_compat contract | `pytest -q tests/T02_contract/test_flow_03_openai_compat_scp_standard.py` | **39 passed**, exit 0 (136s, runs real judge) |
| Sibling streaming test | `pytest -q …T03…::test_openai_compat_stream_endpoint` | 1 passed (complementary 503 branch, unmodified) |
| Guardrails | `python tools/t00_meta_audit.py` | "All integrity checks passed (**0 new regressions**)", exit 0 |
| Skill contract | `python tools/verify_scp_test_skill_contract.py` | `"status": "PASS_WITHIN_SCOPE"`, exit 0 |

`PASS_WITHIN_SCOPE`: within the stated test scope no failure was observed. This is
not a claim that the OpenAI-compat subsystem, the streaming path end-to-end, or the
release is production-ready.

## NEW FINDINGS
1. **Reality vs instruction**: the reported "503-vs-203" is actually **503-vs-200
   /(400,422)**; no 203 exists. (DNA #26 — corrected against observed output.)
2. **Contract coverage is now split across files, and both branches are pinned**:
   m2 (fixed) pins *verified→200 SSE / abort*; T02 pins *withheld/UNKNOWN/kernel→503
   no-leak*; T03 pins *adapter-unavailable→503 no-leak*. A future edit to
   `_run_canonical_ask` must keep all three green — worth recording as a causal-branch
   guard for this boundary.
3. **Governance — the m2 test file is UNTRACKED in git.** `git ls-files`/`git log`
   cannot find `tests/test_m2_adversarial_challenger.py`; it exists only on disk
   (mtime 2026-09-14). If it is intended as a mandatory gate it is not in HEAD and
   may not run in CI. Flagging, **not acting** (out of S1 scope; needs owner decision).
4. **`temperature` is effectively a no-op passthrough** on the canonical
   OpenAI-compat path (not forwarded to run_rag). m2 asserts only "never 500", which
   holds, but the OpenAI `temperature`/sampling params are silently dropped versus
   the old proxy behavior. Recorded as an observation/known-gap; not changed (out of
   scope).
5. `t00` prints an L4 warning for a modified `.agents/ORIGINAL_REQUEST.md` — that is
   a **pre-existing working-tree change, not mine** (it was dirty at session start);
   t00 still reports 0 new regressions and exit 0. No forbidden file in my scope was
   touched.

## Remaining open questions / scope limits
- No end-to-end proof against a real judge + real upstream: only the deterministic
  verified/withheld envelopes were exercised here.
- The 503 translation for `judge_initializing` (JSON `detail=judge_initializing`)
  is collapsed by the route to `server_error`/"Upstream judge pipeline unavailable";
  whether operators/PyRIT want the readiness reason surfaced distinctly is a product
  contract question, not in scope.
- Whether the untracked m2 file should be committed and bound to a release gate is
  an owner decision (finding #3).
