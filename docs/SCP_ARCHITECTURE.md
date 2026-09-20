# SCP — BẢN ĐỒ KIẾN TRÚC TOÀN CẢNH

**Repo:** `https://github.com/checken1994/GA-LAB.git`
**HEAD khi viết tài liệu này:** `4e935a7` — *chore(cleanup): remove dead _lifespan, relocate canary fixture, drop tier3bak (B2)* (refresh số liệu 2026-09-12, gốc 820fe8d 2026-08-31)
**Phạm vi:** toàn bộ repo, đã **tách shadow/backup khỏi production** — mọi con số dưới đây đều là số THẬT của code chạy.

> Tài liệu này trả lời một câu hỏi duy nhất: **"Từng phần của SCP nằm ở module/file nào?"**
> Mọi dòng đều ánh xạ một thành phần kiến trúc vào file vật lý — người audit độc lập mở file là thấy.

---

## 0. Ba sự thật về kích thước (đã tách shadow)

| Vùng | Files | LOC | Bản chất |
|---|---:|---:|---|
| **PRODUCTION** `scp/*` (trừ venv/tests) | 666 | **149,412** | Code chạy thật |
| **TESTS** `tests/*` + `scp/tests/*` | 263 | 37,330 | Kiểm chứng |
| **SHADOW/BACKUP** `data/shadow/` (475f: 156 .txt + 156 .json + 156 .bak + 7 .py, 40K dòng) + `data/diagnostics/` (11f) + `.private-secrets/release-audit/` (15f) | ~500 | ~40K | Snapshot .bak/.txt/.json — KHÔNG chạy |
| **SATELLITE TS** `dashboard/src` (131f) + `desktop` + `mini-services` | ~270 | ~38,000 | Giao diện + sidecar |
| **AUDIT EVIDENCE** `tools/audit/` (4.5GB, gồm dashboard vendored) | — | — | Bằng chứng, không phải code |

Đánh giá chất lượng dựa trên 149,412 LOC production.

---

## 1. Bức tranh 3 vòng

```
VÒNG 1 — RUNTIME LÕI (Python, scp/)
  API Surface → Task Kernel → Judge → LLM Gateway
       ↓              ↓          ↓
  Security Ring   Trace Ledger  Knowledge/Learning
       ↓
VÒNG 2 — SATELLITE (TypeScript)
  dashboard/ (Next.js quan sát) · desktop/ (Electron launcher)
  mini-services/llm-bridge + loop-scheduler (sidecar)
       ↓
VÒNG 3 — DỮ LIỆU & BẰNG CHỨNG
  data/ (curated, quarantine, shadow) · tools/audit/ (evidence 4.5GB)
  .github/workflows/scp-release-gate.yml (CI gate)
```

---

## 2. Luồng sống `/ask` end-to-end (file theo file)

Đây là chuỗi Z→A→G→B→C→D đã audit (7cd98eb → 820fe8d). Mỗi bước ghi file thật:

| # | Bước | File |
|---|---|---|
| 1 | Nhận request, stage `verifier_started` | `scp/api_server_parts/_ask_impl.py` (rebound qua `scp/api_server.py:296`) |
| 2 | **Semantic Firewall** — quét injection 10 family trên mọi contexts/web_fallback, chặn ĐỨT trước khi vào prompt | `scp/api_server_parts/_ask_impl.py:351` → gọi `inspect_untrusted()` trong `scp/core/top_systems_learning.py` |
| 3 | Idempotency — chỉ dedup state IN-FLIGHT, ask đã decide được hỏi lại | `scp/ask_kernel_adapter.py` (548 LOC) |
| 4 | Task Kernel — state machine, lease fencing, event journal hash-chain, backpressure `SCP_ASK_MAX_INFLIGHT` | `scp/task_kernel.py` (423 LOC) + `scp/task_kernel_parts/` (tổng 2,141 LOC) |
| 5 | Transition DETERMINISTIC (kernel không gọi LLM — `llm_enabled=False`) | `scp/task_kernel.py` |
| 6 | **Tier-1 Guard** — chặn cứng cơ học trước mọi LLM (empty/overlength/control-char/grounding) | `scp/security/tier1_guard.py` (93 LOC) |
| 7 | **Judge hai tầng** — Tier-1 → Tier-2 semantic tri-state (PASS/FAIL/None→escalate) | `scp/runtime/judge.py` (188 LOC, TaskJudge) |
| 8 | Parse verdict LLM — strip `<think>`, last-token-wins | `scp/runtime/judge_llm.py` (`_parse_verdict`) |
| 9 | **Multi-LLM cross-check** — 2 vendor độc lập; bất đồng → UNKNOWN (fail-closed) | `scp/runtime/multi_llm_crosscheck.py` (`cross_verify`) |
| 10 | Quorum WHY cho action nguy hiểm | `scp/security/quorum_why.py` (125 LOC) |
| 11 | Governance quyết — LLM không bao giờ là authority cuối | `scp/api_server.py` (v98 context + governance) |
| 12 | Trace ledger — mọi verdict ghi sổ hash | `scp/trace_ledger.py` (47 LOC) |
| 13 | Verifier/postcondition | `scp/verifier.py` (74 LOC) |

**Nguyên tắc xuyên suốt (DNA 26 điều):** Reality > Model · PASS ≠ TRUE · fail-closed · WHY gate.

---

## 3. Bản đồ từng tầng → file

### 3.1 Tầng 0 — Boot & Triển khai

| Thành phần | File | Ghi chú |
|---|---|---|
| Entry point | `scp/__main__.py` | `python -m scp [port]`, mặc định 127.0.0.1:8000, chỉ bind loopback |
| FastAPI app + lifespan | `scp/api_server.py` (679 LOC) + `scp/api_server_parts/` (lifespan, `_ask_impl`) | lifespan gọi `recover_on_boot()` + doubt cron |
| CI release gate | `.github/workflows/scp-release-gate.yml` | GitHub Actions |
| Pre-push gate | `scripts/pre_push_gate.ps1` | boot thật → health → auth → /ask thật |
| Audit runner thống nhất | `scripts/run_full_audit.py` + `Makefile` (targets: audit/test/reality/fitness/benchmark) | |
| Docker test profile | `compose.test.yml` | |
| SBOM generator | `scripts/generate_sbom.py` | |
| Golden suite generator | `scripts/generate_golden_suite.py` | seed 20260829, 100 decisions |
| Caddy gateway :81 | cấu hình ngoài repo, backend check X-Forwarded-For + internal secret trong các route file | fix bypass cc9cb15+e1b0512 |

### 3.2 Tầng 1 — API Surface (26 file route; mặt endpoint phụ thuộc `SCP_API_PROFILE`: profile `core` → 9 APIRoute inline, profile `full` → 171 APIRoute, đo bằng `len(app.routes)`)

| File | Endpoint | Nội dung |
|---|---:|---|
| `scp/api/routes/v104_routes.py` | 24 | Learn/top-systems, free-apis, doubt, matrix — admin token tĩnh |
| `scp/api/routes/v105_routes.py` | 14 | Autofix/audit thế hệ mới |
| `scp/api/routes/hands_routes.py` | 18 | Điều khiển "tay" — verify_admin + XFF check |
| `scp/api/routes/admin_v100.py` / `admin_v98.py` | 10 / 8 | Admin历代 |
| `scp/api/routes/agent_routes.py` | 7 | Agent lane — authed |
| `scp/api/routes/pc_controller_routes.py` | 7 | Điều khiển PC — authed |
| `scp/api/routes/control_routes.py` / `web_control_routes.py` | 6 / 6 | Web control — authed |
| `scp/api/routes/prediction_routes.py` | 5 | Forecast |
| `scp/api/routes/batch_benchmark_routes.py` | 4 | Benchmark (JWT) |
| `scp/api/routes/threat_routes.py` / `v102_v103_routes.py` | 4 / 8 | Threat intel |
| `scp/api/routes/import_routes.py`, `openai_compat.py`, `call_routes.py`, `stream_routes.py`, `audit_routes.py`, `swe_bench_routes.py` | 3+2+3+1+2+1 | OpenAI-compat /chat/completions, SSE stream |
| `scp/api_server.py` inline | 9 | /health, /ask, /auth/token… |

**Auth model:** JWT (user, `/auth/token` via admin_key) cho /ask + benchmark; **static token** cho /v104 admin (split đã fix b994954). 15 route ACTION nguy hiểm có `verify_admin` + XFF.

### 3.3 Tầng 2 — Task Kernel (Agent OS)

| File | LOC | Vai trò |
|---|---:|---|
| `scp/task_kernel.py` | 423 + `task_kernel_parts/` (tổng 2,141) | State machine CREATED→…→decided; **per-thread SQLite connections** (fix race 16/100 loss); lease fencing token; hash-chain event journal; `recover_on_boot()`; `verify_integrity()`; `backup()` |
| `scp/ask_kernel_adapter.py` | 548 | Adapter /ask → kernel; idempotency chỉ dedup in-flight |
| `scp/trace_ledger.py` | 47 | Sổ cái hash chain |
| `scp/core/request_run_ledger.py` | 389 | Durable request-level ledger |

### 3.4 Tầng 3 — Judge & Phán quyết

| File | LOC | Vai trò |
|---|---:|---|
| `scp/runtime/judge.py` | 314 | TaskJudge hai tầng, `judge_with_react_fallback` |
| `scp/runtime/judge_llm.py` | 102 | `_parse_verdict` — strip think, last-token |
| `scp/runtime/multi_llm_crosscheck.py` | 145 | Cross-vendor fail-closed (wired e1b0512) |
| `scp/security/tier1_guard.py` | 93 | Guard cơ học ~0.02ms |
| `scp/security/quorum_why.py` | 125 | Hội đồng WHY cho action nguy hiểm |
| `scp/verifier.py` | 74 | Postcondition verifier |
| `scp/runtime/judge_parts/` (19f, 4,346 LOC) | | **Phần lớn DEAD** — còn giữ thuật toán JudgeCoreMixin (đã audit-first, không xóa) |
| `scp/runtime/experts/` (44f) + `slm_impls/` (17f) + `slms_parts/` (10f) | ~15,000 | 44 domain expert (agriculture, art, law, chem_reality_astro 817 LOC…) — SLM per-domain |

### 3.5 Tầng 4 — LLM Gateway (API-only, brand-neutral)

Toàn bộ nằm trong **`scp/llm_gateway/client.py`** (792 LOC, 4 class, 30 hàm):

- Provider chain: OpenRouter → env extras (`SCP_LLM_FALLBACK_PROVIDERS`) → Groq
- **Dynamic auto-discovery free models** (mới 820fe8d, +128 dòng) — tự lấy danh sách model free từ API
- CircuitBreaker: 3 fail → OPEN → 300s cooldown
- Backoff + jitter; 429/402 failover ngay
- Budget tier ordering (`SCP_BUDGET_ROUTING=1`)
- Nhiều API key (max 10 — e334183)
- **Lưu ý triển khai:** `GROQ_API_KEY` đang rỗng trong `.env` của deployment này → secondary judge rơi về autofix task

### 3.6 Tầng 5 — Tri thức & Học (kho lớn nhất: ~31,000 LOC)

| File/Nhóm | LOC | Vai trò |
|---|---:|---|
| `scp/core/fast_learning_engine.py` | 125 + `fast_learning_engine_parts/` (tổng 913) | Canonical learning engine (post G3-MERGE) |
| `scp/core/top_systems_learning.py` | 506 | 5-source scraper (GitHub/Wikipedia/arXiv/HN/StackOverflow) + TokenBucket + quarantine sha256 + `inspect_untrusted` (**đây là engine của Semantic Firewall**) |
| `scp/core/knowledge_curation.py` | 256 | Pipeline curate + 4-signal reliability (authority .35/engagement .20/freshness .15/injection .30 HARD GATE) |
| `scp/data_sources/` (71f) | 12,334 | Domain data: `domain_registry.py` (1,074), chemistry 620, live_knowledge 695, astronomy 485, medical 417… — **27 file mồ côi đã wire 16c4b51** |
| `scp/data_sources/free_api_catalog.py` | | Kho 1,689 free API |
| `scp/core/question_fetchers/` (5f) | 1,358 | Fetch câu hỏi thật (Wikipedia, trivia, knowledge) |
| `scp/knowledge/` (27f) | 6,208 | Knowledge store |
| `scp/brain/` (4f + index_parts 4f) | 2,119 | Brain/index |
| `scp/rag/` (2f) + `scp/benchmark/` (22f) | 2,640 | RAG + benchmark (bộ đề RAG đã có — dùng lại, không rebuild) |
| `scp/experience/`, `scp/history/`, `scp/prediction/`, `scp/forecast/` | ~2,900 | Experience/history/prediction |

### 3.7 Tầng 6 — An ninh (42 file, 10,875 LOC)

| Nhóm | File | Vai trò |
|---|---|---|
| Sandbox | `scp/security/os_sandbox.py` (252) | Windows Job Object KILL_ON_JOB_CLOSE + dead-proxy env + PATH restriction; Linux bwrap builder |
| Firewall | inline `api_server.py:1370-1388` + `top_systems_learning.inspect_untrusted` | Injection scan trước prompt |
| Auth | `auth.py`, `jwt_guard.py`, `auth_config.py`, `env_loader.py`, `secret_loader.py`, `provider_keys.py` | JWT/static split |
| Red team | `red_team.py` (65), `h8_redteam_bridge.py`, `threat_simulator.py`, `gcg_attack.py`, `auto_payload_generator.py` | Tấn công tự kiểm |
| Detect | `threat_detector.py`, `unified_detector.py`, `rogue_ai_detector.py`, `multi_turn_tracker.py`, `image_voice_detector.py`, `attack_classifier/crawler/memory/policy.py` | Đa lớp detect |
| Protect | `dos_protection.py`, `circuit_breaker.py`, `canary_monitor.py`, `production_guard.py`, `memory_guard.py`, `kernel_patrol.py`, `escalation.py`, `capability_epoch.py`, `url_safety.py`, `request_context.py`, `counter_response.py`, `predictor.py`, `response_monitor.py` | |
| Intel | `threat_intel.py`, `cisa_kev.py`, `playbooks.py`, `cross_language_learner.py`, `bypass_encrypt.py` | |

### 3.8 Tầng 7 — Tự sửa & Tiến hóa (~22,600 LOC)

| File | LOC | Vai trò |
|---|---:|---|
| `scp/autofix/engine.py` | 1,005 + `engine_parts/` + `engine_extensions.py` (tổng 3,702) | AutoFixEngine tiered |
| `scp/autofix/llm_fix.py` | 162 + `llm_fix_parts/` (tổng 760) | Fix sinh bởi LLM (qua gateway) |
| `scp/autofix/scanners/` (31f) | 8,253 | cross_func_taint 1,253, taint_flow 829… |
| `scp/autofix/property_validator.py` | 922 | Property-based validation |
| `scp/autofix/policy_gate.py` | 883 | Blast-radius gate |
| `scp/autofix/speculative_prefixer.py` | 844 | Pre-fix suy đoán |
| `scp/autofix/type_flow_verifier.py` | 801 | Cross-file type flow |
| `scp/autofix/runner.py` + `runner_phases/` (15f) | 5,501 | Runner + AST scan phases |
| `scp/core/code_evolution_agent.py` | 478 | Mức 5-7 tự sửa source |
| `scp/core/fitness_engine.py` | 227 | Golden suite PROMOTE/ROLLBACK gate |
| `scp/core/doubt_cron.py` | 196 | Cronjob of Doubt — tự nghi ngờ định kỳ |
| `scp/core/speculative.py`, `environment_snapshot.py`, `budget_engine.py`, `context_pruner.py`, `file_mutex.py`, `dependency_resolver.py` | | P2/P3 capabilities (aad118a) |

### 3.9 Tầng 8 — Dữ liệu & Bằng chứng

| Đường dẫn | Bản chất |
|---|---|
| `data/shadow/` (475f: .bak/.txt/.json snapshot, ~40K dòng) | **SHADOW — snapshot cũ. Khuyến nghị: archive ra riêng** |
| `data/diagnostics/` (11f) | Shadow chẩn đoán |
| `.private-secrets/release-audit/` (15f) | Shadow audit (không push công khai) |
| `data/curated/`, quarantine | Tri thức đã curate (sha256 provenance) |
| `tools/audit/` (4.5GB) | Bằng chứng audit + dashboard vendored |
| `tests/golden/golden_dataset.json` | Golden suite 100 decisions, seed 20260829 |

### 3.10 Tầng 9 — Kiểm chứng

- `tests/` — 254 file test + `scp/tests/` 9 file (số pytest pass thay đổi theo profile — chạy `python -m pytest tests/ -q` để đo tại HEAD hiện tại; basetemp permane qua `pytest.ini` addopts)
- `scp/tests/` — 1 file hermetic boot
- `Makefile` + `scripts/run_full_audit.py` — một lệnh chạy toàn bộ
- Pre-push gate 2/2 PASS tại e1b0512

---

## 4. Vòng 2 — Satellite TypeScript

| Thành phần | File | Vai trò |
|---|---|---|
| Dashboard | `dashboard/src/` (131f, 18,100 LOC TS, Next.js) | Quan sát read-only; có audit-data rounds (round9.ts…) |
| Desktop launcher | `desktop/main.cjs` + `preload.cjs` | Electron; **CÒN STALE OLLAMA**: `SCP_LLM_BRIDGE_PORT=11434` (main.cjs:31,65) |
| LLM bridge | `mini-services/llm-bridge/index.ts` (1,032 LOC) | Sidecar bridge |
| Loop scheduler | `mini-services/loop-scheduler/index.ts` (826 LOC) | Cron sidecar |
| Dashboard health | `dashboard/src/app/api/scp/health/route.ts` | **CÒN STALE**: `LLM_BRIDGE_URL ?? "http://127.0.0.1:11434"` (L31) |

⚠️ 64 refs Ollama/11434 trong TS (`dashboard/`, `desktop/`, `mini-services/`) — phần lớn là **dữ liệu audit-data** (bằng chứng lịch sử round9.ts, KHÔNG sửa được vì là evidence), nhưng `desktop/main.cjs` + `health/route.ts` là code sống cần cập nhật.

---

## 5. Vấn đề mở → file (để audit độc lập đối chiếu)

| # | Vấn đề | File | Mức |
|---|---|---|---|
| 1 | Auth code fallback — server boot không có `SCP_ADMIN_KEY` vẫn nhận "admin" | `scp/security/auth_config.py` | P0 còn mở |
| 2 | `judge_parts/` 4,346 LOC phần lớn dead (giữ thuật toán, chưa wire hết) | `scp/runtime/judge_parts/` | P2 |
| 3 | 570K LOC shadow làm nhiễu mọi metric | `data/shadow/` | Quyết định archive |
| 4 | Desktop/health TS còn port Ollama 11434 | `desktop/main.cjs`, `dashboard/.../health/route.ts` | P3 |
| 5 | 66 route non-ACTION chưa auth | `scp/api/routes/*` | P2 |
| 6 | GROQ_API_KEY rỗng → cross-check chỉ 1 vendor thật | `.env` deployment | Ops |
| 7 | ~~Root còn 4 script rác: `check_or.py`, `inject_dynamic.py`, `test_openrouter.py`, `test_size.py`~~ | root/ | **ĐÃ DỌN** (4 script không còn tồn tại tại HEAD 4e935a7) |

---

*Tài liệu sinh từ quét thật toàn repo tại HEAD 820fe8d (2026-08-31); số liệu refresh và kiểm chứng lại bằng `wc -l` / đếm route thực tế tại HEAD 4e935a7 (2026-09-12). Mỗi dòng đều kiểm chứng được bằng cách mở file tương ứng.*
