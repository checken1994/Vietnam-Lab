# INDEPENDENT WITNESS REPORT ROUND 2 — SCP + WITNESS-OWNED REAL API

- **Campaign:** W2-2026-09-11 (round 2 of W-2026-09-11)
- **Witness:** Z.ai Code — independent, outside SCP author lineage (DNA G05)
- **Repo SHA:** `b726463e5659` (pulled fresh at round 1; unchanged)
- **Round-2 change demanded by the user:** *"thực tế mà không có API thì không phải thực tế"* — the witness built its OWN real external API (witness-api cluster, 4 instances, ports 8788-8791) and wired SCP to call OUT to it (env-only, zero system-code edits):
  - `openai_compat` provider → http://127.0.0.1:8788/v1 (real cloud LLM via z-ai-web-dev-sdk)
  - `witness_b` → :8789, `witness_c` → :8790 (real cloud LLM; give MULTI-LLM cross-verification genuinely distinct providers)
  - `witness_fast` → :8791 (deterministic capacity backend: real HTTP + real server-side compute, no cloud LLM — industry load-test standard)
  - Zero-cost wall passed the honest way: $0 pricing proofs recorded via the guard's own public API (the witness API genuinely costs SCP nothing)
- **Golden proof (chain fully real):** `/ask` → TaskKernel → LLMGateway → witness-api → real cloud LLM → MULTI-LLM cross-verify (2 distinct families) → governance UPHOLD → verdict **PASS**, answer **"Paris"**, confidence 0.85 — with per-request evidence rows on the witness API side.

## 1. The real world pushed back (measured, not assumed)

- The sandbox's shared cloud-LLM service enforces **real rate limits**: burst storms of HTTP 429 were observed live on all instances and even via fresh CLI sessions.
- The rate limit behaves **per-session** (3 instances with separate sessions: one limited while others served) — the witness API therefore implements **session rotation** + an AIMD token-bucket (rate 0.2–1.5 rps, 20s cooldowns) — the same engineering a production gateway does in front of a rate-limited vendor.
- probes: 66, LLM_OK: 4, LLM_429: 62 (real upstream quota, 1 probe/60s)

## 2. R1 — Truth & honesty through the real API

- N=30 asks (full pipeline, real LLM when quota allowed), wall=2870.6s
- Answerable: answered 4/24, correct 4, incorrect 0, abstained-by-strict-verification 19
- **Accuracy of answered: 1.0** | answer rate: 0.1667
- Unanswerable: abstained 6/6, hallucinated 0 → **honesty rate 1.0**
- verdict distribution: {'FAIL': 25, 'PASS': 4, 'None': 1}
- latency (client): p50=110088.899ms p99=110499.87ms
- run_id uniqueness: 29/30 (dups: 0)
- ledger rows created: 33 | cluster LLM calls: {'witness-a:8788': 52, 'witness-b:8789': 148, 'witness-c:8790': 145, 'witness-fast:8791': 0}

## 3. R2 — Load ladder (all real HTTP)

### R2a — direct API capacity (deterministic backend)
| C | RPS | correct | p50 | p99 |
|---|---|---|---|---|
| 1 | 966.77 | 192/192 | 0.7ms | 3.126ms |
| 2 | 1149.7 | 192/192 | 1.384ms | 3.994ms |
| 4 | 818.27 | 192/192 | 3.01ms | 18.777ms |
| 8 | 659.82 | 192/192 | 8.71ms | 31.154ms |
| 16 | 526.74 | 192/192 | 21.853ms | 85.29ms |
| 32 | 522.26 | 192/192 | 38.67ms | 243.893ms |
| 64 | 774.84 | 192/192 | 68.501ms | 90.568ms |

### R2b — SCP provider transport (real SCP httpx/retry/breaker code)
| C | RPS | correct | none | p50 | p99 | breaker open |
|---|---|---|---|---|---|---|
| 1 | 645.88 | 96/96 | 0 | 1.228ms | 3.96ms | False |
| 2 | 889.82 | 96/96 | 0 | 2.057ms | 3.909ms | False |
| 4 | 754.38 | 96/96 | 0 | 4.869ms | 9.106ms | False |
| 8 | 677.1 | 96/96 | 0 | 8.767ms | 22.265ms | False |
| 16 | 675.62 | 96/96 | 0 | 17.256ms | 96.34ms | False |
| 32 | 586.27 | 96/96 | 0 | 42.029ms | 141.436ms | False |

- R2c (real-LLM ladder): QUOTA_BLOCKED — HTTP 429

## 4. R3 — Chaos (real fault injection at the HTTP boundary)

- **kill -9 mid-traffic:** breaker opened after 3 consecutive failures; during-outage calls fast-failed (last one 0.3ms); **recovery 21028.2ms** after instance restart (= breaker 20s cooldown + probe).
- **429 storm (fail_next=5):** 4 failed chats, all fast (≤41.5ms); post-storm recovery 2.0ms.
- **500 storm (fail_next=6):** SCP's transient retry ladder absorbed the burst — only 1/8 chats failed; retry latency visible (~825-887ms); recovery 1.6ms.
- **+3000ms latency injection:** 6/6 answered, p50=3003.935ms p99=3042.844ms — injected latency propagates ~1:1 (p50≈3004ms), breaker correctly stays CLOSED (slow ≠ dead).
- **50% random 503s (n=20):** visible failures 5/20 (25%) vs 50% raw error rate — **SCP's retry ladder absorbed half the failures**; breaker never opened (not consecutive).

## 5. R4 — Soak (5.5 minutes sustained, real traffic)

- Phase 1 (150s, C=16 direct): **138,094 requests, {'200': 138094} (zero errors), 138,094 correct answers**, p50=0.712ms p99=7.115ms
  - 30s-bucket throughput: [(33540, 1118.0), (32636, 1087.8), (30034, 1001.1), (24959, 832.0), (16925, 564.2)]
- Phase 2 (120s, C=8 through SCP provider code): **46,159 requests, 46,159 correct**, p50=1.514ms p99=29.946ms, breaker stayed closed
- Phase 3 (/ask liveness under live upstream quota): [{'transport_error': 'timed out'}]
- **Total real requests served by the witness API in 5.5 min: 184,276** (peak served concurrency 2)
- RSS: SCP 176360→191604 KB (+14 MB over soak); witness-api 54552→63848 KB (Bun GC reclaimed mid-soak, no leak)

## 6. Real bugs found in round 2 (root-caused by the witness)

1. **[HIGH] `InvalidTransition: HUMAN_REVIEW->HUMAN_REVIEW`** — `scp/ask_kernel_adapter.py:411` (finalize) crashes the /ask request when a task already sits in HUMAN_REVIEW and finalize escalates again; observed live under provider-degradation (HTTP 500/timeout to client).
2. **[HIGH] Zero-cost wall blocks ALL custom OpenAI-compatible providers by default** — `zero_cost_denied:DENY_UNKNOWN_PRICE` at the transport boundary: any `OPENAI_BASE_URL`-style provider without a recorded $0 pricing proof is refused *before any HTTP call* (fail-closed, but silently — only a debug-level log; operators get no actionable signal).
3. **[MEDIUM] Provider degradation → /ask latency collapse**: with rate-limited providers, one /ask takes 30–180s (httpx 60s timeout × provider rotation × retries) and cross-verification cannot certify with 1 healthy provider (consensus=missing_distinct_providers) — correct fail-closed honesty, but latency is user-hostile; no early bail-out.
4. **[LOW] .env duplicate-key footgun**: two `OPENAI_API_KEY=` lines (one empty later in file) silently disable the provider (dotenv last-wins).

## 7. Method + limits (G19)

- Harness: `tests/external_audit/witness_r*.py` (witness-owned); raw JSON in `tests/external_audit/results/`; per-request raw evidence in `mini-services/witness-api/data/requests-*.jsonl` (every request logged with timings/status/upstream latency).
- No SCP system code was modified. Env-only wiring + $0 pricing proofs via the guard's public API + stale-task cleanup via the kernel's own journal-append transition API (hash-chain verified).
- Limits: 1 node, loopback, uvicorn 1 worker; the sandbox's shared cloud LLM quota is scarce (429 storms) — real-LLM scenarios are paced/budgeted accordingly, and the scarcity itself is reported as measured reality (R0 tracker).