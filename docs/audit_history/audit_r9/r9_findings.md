# SCP DNA Audit — Round 9 (R9) Findings

> **DNA #22 (PASS ≠ TRUE) applied recursively (level 3):** R8 reported 7 fixes "applied" + 7 NEW bugs found. R9 (Subagent B) does NOT trust R8's 7 was exhaustive — it re-audits the SCP codebase for NEW root-cause bugs R8 MISSED, using strengthened patterns + world-tool signatures.
>
> **DNA #1 (Hỏi TẠI SAO đến gốc):** Each finding below has a root_cause (WHY), not just a symptom. "blocks event loop" is a symptom; "chat_sync called from async def ask() internally calls future.result() which blocks the calling thread" is the root cause.
>
> **DNA #26 (Reality > Model):** Every finding quotes EXACT code at the EXACT line (verified by Read tool). Every bug has a runtime consequence. No style nits, no fiction.
>
> **DNA #23 (Coverage limits, honest disclosure):** see footer — grep-only audit, no ruff/bandit/semgrep/pyright/CodeQL binaries, no test execution.

**Subagent:** B (Task ID 1-b)
**Scope:** `/home/z/my-project/scp/` (371 .py files, R8-patched baseline)
**Method:** Targeted grep scans (15 bug-class patterns) + deep-read of 10 highest-risk files (api_server.py, runtime/judge.py, runtime/judge_parts/judgecore_mixin.py, autofix/engine.py, runtime/healing_v14.py, runtime/storage_manager.py, meta/why_engine.py, api/routes/v105_routes.py, api/routes/import_routes.py, api/routes/v102_v103_routes.py, llm_gateway/client.py, runtime/notifications.py).
**Date:** 2026-08-08 (R9)

---

## Methodology

### Step 1 — Read R8's 7 findings + 7 patches

R8 covered: R8-1 (attack-mode monitor dead SQLite query), R8-2 (Tier-3 timeout re-arm), R8-3 (_rotate_large_files 1-generation), R8-4 (cold-start 12h WARN), R8-5 (single .tier3bak), R8-6 (healing_history race), R8-7 (lazy hasattr lock). R9 explicitly AVOIDS re-reporting these.

### Step 2 — Targeted grep scans (world's-best-tool signatures)

Patterns run (via Grep tool, NOT bash grep):

- `yaml\.load\(` (bandit B506) → 0 production hits (only scanner definitions)
- `pickle\.loads?\(` (bandit B301) → 0 production hits (only smart_cache.py comment + scanner defs)
- `subprocess\..*shell\s*=\s*True` (bandit B602) → 0 production hits
- `os\.system\(` (bandit B605) → 0 production hits
- `[^_]eval\(|[^_]exec\(` (bandit B307/B102) → only `eval(args_str, {"__builtins__": {}}, ...)` in hypothesis_scanner.py (restricted namespace — safe)
- `hashlib\.(md5|sha1)\(` (bandit B324) → only in `.tier3bak` backup file (not active code)
- `random\.(random|randint|choice)\(` (bandit B311) → all annotated `# noqa: S311` (non-security contexts)
- `\.execute\(f["']` / `\.execute\(.*\.format` / `\.execute\(.*\+` (semgrep SQLi) → only in scanner defs + `storage_manager.py:272` (VACUUM INTO with path validation — already safe per R8)
- `password\s*=\s*['"][^'"]+['"]` / `api_key\s*=\s*['"]…['"]` (CWE-798) → 0 production hits
- `requests\.(get|post|...)\(` inside async functions (blocking-in-async) → covered in R9-3
- `time\.sleep\(` inside async functions (blocking-in-async) → only in daemon threads (safe)
- `except\s*:` (bare except, bandit B110) → 0 production hits
- `datetime\.(now|utcnow)\(\)` without tz → many hits, mostly in logging/isoformat (R8 mentioned, low-priority)
- `chat_sync\(` called from async def → **R9-4 found**
- `judge\.judge\(` called from async def WITHOUT asyncio.to_thread → **R9-1, R9-3 found**
- `run_deep_audit\(` called from async def → **R9-2 found**

### Step 3 — Deep-read 10 highest-risk files

1. `api_server.py` (1178 lines — full read of /ask handler + lifespan + attack-mode monitor)
2. `runtime/judge.py` (1324 lines — get_stats + schedule_v100_background_jobs + R8-4 patch area)
3. `runtime/judge_parts/judgecore_mixin.py` (3240 lines — judge() body, verdict_history append site)
4. `autofix/engine.py` (1493 lines — _should_auto_approve_tier3 + _auto_approve_tier3, R8-2/R8-5 patch areas)
5. `runtime/healing_v14.py` (378 lines — full file, R8-6 patch area, monitor() vs get_stats())
6. `runtime/storage_manager.py` (516 lines — _rotate_large_files R8-3 patch area, _vacuum_db)
7. `meta/why_engine.py` (1085 lines — execute_pending_plans R8-7 patch area)
8. `api/routes/v105_routes.py` (408 lines — rollback endpoint R8-5 patch area, run-audit endpoint)
9. `api/routes/import_routes.py` (170 lines — 3 judge.judge() call sites)
10. `api/routes/v102_v103_routes.py` (122 lines — crawl_all + check_and_maintain call sites)
11. `llm_gateway/client.py` (709 lines — chat_sync function, OllamaProvider)
12. `runtime/notifications.py` (273 lines — _recent list, notify() mutation)

### Step 4 — Rigorous documentation

For each finding: verify exact line via Read, quote exact code, explain root cause (TẠI SAO), propose a real fix, identify which world-class tool catches it, hypothesize repro, explain why R8 missed it.

---

## Summary Table

| ID | File | Line | Bug Class | Severity | One-liner |
|----|------|------|-----------|----------|-----------|
| R9-1 | `api/routes/import_routes.py` | 48, 102, 149 | blocking-in-async (sync `judge.judge()`) | CRITICAL | 3 `import/*` endpoints call `judge.judge()` synchronously inside `async def` → each question blocks event loop 5-30s; a 100-question batch blocks ALL other requests for 8-50 min |
| R9-2 | `api/routes/v105_routes.py` | 168 | blocking-in-async (sync `run_deep_audit()`) | HIGH | `/v105/autofix/run-audit` calls `run_deep_audit()` synchronously inside `async def` → AST scan + N×LLM-fix can take 10+ min, blocks event loop entire duration |
| R9-3 | `api/routes/v102_v103_routes.py` | 106 + 66 | blocking-in-async (sync `crawl_all()` + `check_and_maintain()`) | HIGH | `/v103/attacks/crawl` calls `_attack_crawler.crawl_all()` (3 external HTTP sources, 15-45s) synchronously; `/v103/storage/maintain` calls `sm.check_and_maintain()` (VACUUM + rotate + archive, 10-60s) synchronously — both block event loop |
| R9-4 | `api_server.py` | 652 | blocking-in-async (`chat_sync()` via `future.result()`) | HIGH | `async def ask()` calls `chat_sync()` (sync wrapper). chat_sync detects async context, spawns ThreadPoolExecutor, then BLOCKS on `future.result(timeout=90)` — event loop frozen for entire Ollama generation (60-90s) per chatbot request |
| R9-5 | `runtime/judge.py` + `runtime/judge_parts/judgecore_mixin.py` | 1169 + 1692-1693 | race condition (concurrent list mutation) | LOW | `judge.judge()` (worker thread) appends + reassigns `self.verdict_history`; `judge.get_stats()` (event loop thread) iterates it WITHOUT lock → CPython `RuntimeError: list changed size during iteration` on admin endpoint. Same bug class as R8-6, different file — R8-6 fixed `healing_v14.healing_history` but missed `judge.verdict_history` |
| R9-6 | `runtime/healing_v14.py` | 370 + 174-175 | race condition (concurrent dict mutation) | LOW | `monitor()` (worker thread) mutates `self.error_patterns[issue["type"]] += 1`; `get_stats()` (event loop thread) reads `dict(self.error_patterns)` WITHOUT lock — R8-6 fixed `healing_history` in same file but missed `error_patterns` 6 lines below the patched block |
| R9-7 | `runtime/notifications.py` + `api_server.py` | 174 + 367-372 | race condition (R8-1 regression — list iteration without lock) | MEDIUM | R8-1's fix replaced dead SQLite query with direct iteration of `_notif._recent` list — but `_recent` is mutated by `notify()` (worker thread) WITHOUT lock. The R8-1 patch introduced a NEW race: `sum(1 for _n in _recent ...)` in `_attack_mode_monitor` (daemon thread) races with `_recent.append()` → `RuntimeError: list changed size during iteration` crashes attack-mode monitor silently (caught by `except Exception: logger.debug`) |

**Total: 7 NEW root-cause bugs across 3 bug classes (4 blocking-in-async + 3 race conditions).**

**Severity breakdown:** 1 CRITICAL, 3 HIGH, 1 MEDIUM, 2 LOW.

---

## Per-Finding Detail

### R9-1 — `import_routes.py` 3 sites: synchronous `judge.judge()` inside `async def` (CRITICAL)

**File:** `api/routes/import_routes.py:48` (and duplicates at `:102`, `:149`)
**Bug class:** blocking-in-async (sync long-running call inside async handler)
**World tool:** ruff ASYNC100 + semgrep `python.lang.performance.audit.async-blocking-call` + manual review

**Root cause (TẠI SAO):** The `/import/jsonl`, `/import/excel`, `/import/batch` endpoints are declared `async def` but call `judge.judge(...)` synchronously (no `await asyncio.to_thread(...)`). `judge.judge()` is a long-running synchronous method (SLM HTTP calls 5-15s each + WHY engine external source queries + V98 pipeline + governance). Each call blocks the asyncio event loop for 5-30+ seconds. A batch import of N questions blocks ALL other HTTP requests (`/ask`, `/health`, `/dashboard`, WebSocket pings) for N × 5-30s — a 100-question import = 8-50 minutes of total event-loop blockage.

The dev ALREADY knew about this pattern — `api_server.py:788` and `api/routes/openai_compat.py:54` use `await asyncio.to_thread(judge.judge, ...)` correctly, and `openai_compat.py:51-53` even has a comment: "TẠI SAO: was calling judge.judge() synchronously in async def → blocks event loop when SLM/API slow. PyRIT/garak parallel requests → server hang." But the same fix was NOT applied to `import_routes.py` (3 sites) — the dev's own fix was incomplete.

**Before code (`import_routes.py:39-57`):**
```python
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = _json.loads(line)
            question = data.get("question", "")
            ai_answer = data.get("ai_answer", data.get("answer", ""))
            data.get("domain", "general")

            v = judge.judge(question=question, ai_answer=ai_answer, cycle_count=0)  # ← BLOCKS
            results.append({
                "line": i + 1,
                "question": question[:100],
                "verdict": v.verdict,
                "confidence": v.confidence,
                "falsification": v.evidence.get("falsification_status"),
                "governance": v.evidence.get("governance_decision"),
                "elapsed_ms": v.evidence.get("v100_phase_timings", {}).get("total_ms", 0),
            })
        except Exception as e:
            results.append({"line": i + 1, "error": str(e)})
```
(Identical pattern at `:102` for `/import/excel` and `:149` for `/import/batch`.)

**Suggested fix:** Wrap each `judge.judge(...)` call in `await asyncio.to_thread(...)`. Add `import asyncio` at top of file:
```python
import asyncio  # add at top
...
# At each of the 3 call sites (lines 48, 102, 149):
v = await asyncio.to_thread(
    judge.judge,
    question=question, ai_answer=ai_answer, cycle_count=0
)
```
(Optionally: run questions concurrently via `asyncio.gather(*[asyncio.to_thread(judge.judge, q) for q in batch])` for a 5-10× speedup on multi-core hosts — but the minimal fix is `asyncio.to_thread`.)

**Repro hypothesis:**
1. Start SCP server.
2. Open a second terminal: `watch -n 1 'curl -s http://localhost:8000/health | jq .status'` — should return "ok" every second.
3. In a third terminal: `curl -X POST http://localhost:8000/import/jsonl -H "Authorization: Bearer $TOKEN" -d @questions.jsonl` where `questions.jsonl` has 20 questions.
4. Observe the `/health` watch freezes for 100-600 seconds (20 questions × 5-30s each). During this time, NO other request can be served. WebSocket clients disconnect.

**Why R8 missed:** R8 deep-read `api_server.py` + `judge.py` + `healing_v14.py` + `storage_manager.py` + `autofix/engine.py` + `slms.py`. `api/routes/import_routes.py` was NOT in R8's deep-read list (Step 3 of R8 findings lists 6 files + 5 "plus" files; import_routes.py not mentioned). R8's grep scans covered `except:`, SQLi, command injection, weak crypto — but did NOT grep for `judge\.judge\(` without `await asyncio.to_thread` (the sync-in-async pattern). The bug is invisible to grep-only scans unless the scanner specifically looks for "sync call inside async def".

---

### R9-2 — `v105_routes.py:168`: synchronous `run_deep_audit()` inside `async def` (HIGH)

**File:** `api/routes/v105_routes.py:168`
**Bug class:** blocking-in-async (sync long-running call inside async handler)
**World tool:** ruff ASYNC100 + semgrep `python.lang.performance.audit.async-blocking-call`

**Root cause (TẠI SAO):** The `/v105/autofix/run-audit` endpoint is declared `async def` but calls `run_deep_audit()` synchronously. `run_deep_audit()` (= `run_once(ast_scan=True)`) AST-scans all 371 .py files (5-15s), then for each finding calls `AutoFixEngine.process_bug()` which may invoke the LLM fix path (deepseek-r1:8b via Ollama — 30+ seconds per fix). A typical audit finds 5-20 fixable bugs → 2.5-10 minutes total. The entire duration blocks the event loop.

The dev's own code at `api_server.py:317-333` runs `_deep_audit_loop` in a daemon thread (NOT in the event loop) — explicitly to avoid blocking. The 24h-scheduled audit thread does the right thing. But the manual `/v105/autofix/run-audit` endpoint does NOT — it calls `run_deep_audit()` inline from `async def`.

**Before code (`v105_routes.py:166-171`):**
```python
    try:
        from scp.autofix.runner import run_deep_audit
        results = run_deep_audit()  # ← BLOCKS event loop for minutes
        return {"audit_complete": True, "results": results}
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e
```

**Suggested fix:** Wrap in `await asyncio.to_thread(...)`:
```python
    try:
        from scp.autofix.runner import run_deep_audit
        import asyncio
        results = await asyncio.to_thread(run_deep_audit)
        return {"audit_complete": True, "results": results}
    except Exception as e:
        raise HTTPException(500, f"Error: {e}") from e
```

**Repro hypothesis:**
1. Start SCP. Set `SCP_AUTO_APPROVE_TIER3=1`.
2. `curl http://localhost:8000/health` → returns "ok" in <50ms.
3. `curl -X POST http://localhost:8000/v105/autofix/run-audit -H "Authorization: Bearer $TOKEN"` (triggers full audit + fix cycle).
4. While the audit is running (2-10 min), `curl http://localhost:8000/health` hangs — no response until the audit completes.

**Why R8 missed:** R8 deep-read `api/routes/v105_routes.py` (the rollback endpoint R8-5) but only around lines 336-345 (the rollback hash-check). The `run-audit` endpoint at line 148-171 was not in R8's audit scope. R8's grep patterns did not include "long-running sync call inside async def".

---

### R9-3 — `v102_v103_routes.py`: synchronous `crawl_all()` + `check_and_maintain()` inside `async def` (HIGH)

**File:** `api/routes/v102_v103_routes.py:106` (`/v103/attacks/crawl`) + `:66` (`/v103/storage/maintain`)
**Bug class:** blocking-in-async (sync I/O-heavy call inside async handler)
**World tool:** ruff ASYNC100 + semgrep `python.lang.performance.audit.async-blocking-call`

**Root cause (TẠI SAO):** Two endpoints call synchronous I/O-heavy functions directly inside `async def`:

1. **`:106` `_attack_crawler.crawl_all()`** — `crawl_all()` (`security/attack_crawler.py:99`) sequentially calls `_crawl_github()`, `_crawl_huggingface()`, `_crawl_reddit()` — each makes HTTP requests with 5-15s timeouts. Total: 15-45 seconds blocking the event loop.

2. **`:66` `sm.check_and_maintain()`** — `check_and_maintain()` (`storage_manager.py:112`) does: disk space check, file rotation (compress 100MB+ files — 1-5s each), SQLite VACUUM (10-60s on a large DB), archival (compress + move old files — 5-30s), old-archive deletion. Total: 30-120 seconds blocking the event loop.

**Before code (`v102_v103_routes.py:101-110`):**
```python
@router.post("/v103/attacks/crawl")
async def v103_force_crawl(_admin: bool = Depends(verify_admin)):
    """V103 NEW: Force crawl tấn công mới ngay lập tức. ..."""
    if _attack_crawler is None:
        return {"error": "AttackCrawler not initialized"}
    new_attacks = _attack_crawler.crawl_all()  # ← BLOCKS 15-45s
    return {
        "new_attacks": len(new_attacks),
        "stats": _attack_crawler.stats(),
    }
```

**Before code (`v102_v103_routes.py:61-74`):**
```python
@router.post("/v103/storage/maintain")
async def storage_maintain(_admin: bool = Depends(verify_admin)):
    """Trigger storage maintenance (rotate + vacuum + archive)."""
    from scp.runtime.storage_manager import StorageManager
    sm = StorageManager(data_dir="data")
    stats = sm.check_and_maintain()  # ← BLOCKS 30-120s
    return {
        "total_size_mb": stats.total_size_mb,
        ...
    }
```

**Suggested fix:** Wrap both in `await asyncio.to_thread(...)`:
```python
import asyncio  # add at top

# Site 1 (line 106):
new_attacks = await asyncio.to_thread(_attack_crawler.crawl_all)

# Site 2 (line 66):
stats = await asyncio.to_thread(sm.check_and_maintain)
```

**Repro hypothesis:**
1. Start SCP with attack_crawler initialized.
2. `curl http://localhost:8000/health` → fast.
3. `curl -X POST http://localhost:8000/v103/attacks/crawl -H "Authorization: Bearer $TOKEN"`.
4. While crawl is running (15-45s), `/health` hangs.

**Why R8 missed:** R8 did not deep-read `v102_v103_routes.py` (not in R8's Step 3 file list). The bug class (sync-in-async) was not in R8's grep pattern set.

---

### R9-4 — `api_server.py:652`: `chat_sync()` called from `async def ask()` blocks event loop (HIGH)

**File:** `api_server.py:652` (call site) + `llm_gateway/client.py:581-587` (chat_sync internals)
**Bug class:** blocking-in-async (sync wrapper that blocks on `future.result()`)
**World tool:** ruff ASYNC100 + manual review (ruff/semgrep do not trace cross-function blocking semantics)

**Root cause (TẠI SAO):** When a user sends a chat message WITHOUT `ai_answer` (chatbot mode — the default for the chat UI), `async def ask()` at line 549 calls `chat_sync(...)` at line 652. `chat_sync` is a synchronous function that detects it's being called from an async context (`asyncio.get_running_loop()` succeeds) and does this:

```python
# llm_gateway/client.py:581-587
if _in_async:
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=self.ollama_default.timeout + 30)  # ← BLOCKS calling thread
```

`future.result(timeout=90)` is a **synchronous blocking call** — it waits for the worker thread to complete. The calling thread is the asyncio event loop thread. So while Ollama generates the response (60-90 seconds), the event loop is COMPLETELY blocked:
- Other `/ask` requests can't be processed
- `/health` hangs
- WebSocket clients can't send/receive
- Background tasks (`schedule_v100_background_jobs`) can't run

The `chat_sync` function's docstring claims it's a "Sync wrapper for background threads" — but it's being called from the event loop thread (async def ask), not a background thread. The ThreadPoolExecutor indirection does NOT make it non-blocking — it just moves the HTTP call to a worker thread while the event loop thread waits synchronously.

Additionally, the `timeout=90` parameter is misleading: if `future.result(timeout=90)` raises `TimeoutError`, the `with ... as pool:` block calls `pool.shutdown(wait=True)` on exit, which BLOCKS until the worker finishes. So the timeout is not actually enforced — the event loop waits for the worker regardless.

**Before code (`api_server.py:648-660`):**
```python
    _ai_answer = req.ai_answer
    if not _ai_answer or not _ai_answer.strip():
        try:
            from scp.llm_gateway import chat_sync
            _ollama_answer, _provider = chat_sync(  # ← BLOCKS event loop 60-90s
                req.question,
                context="",
                system_prompt="Bạn là SCP — một trợ lý AI thông minh. Trả lời ngắn gọn, chính xác, bằng tiếng Việt.",
                task="chat",
            )
            if _ollama_answer:
                _ai_answer = _ollama_answer
                logger.info(f"[CHATBOT] Ollama ({_provider}) generated answer: {_ollama_answer[:80]}...")
        except Exception as _ollama_err:
            logger.warning(f"[CHATBOT] Ollama call failed: {_ollama_err}")
```

**Suggested fix:** Call the async `chat()` method directly via `await` — no `chat_sync` wrapper needed:
```python
    _ai_answer = req.ai_answer
    if not _ai_answer or not _ai_answer.strip():
        try:
            from scp.llm_gateway import get_gateway
            _gateway = get_gateway()
            _ollama_answer, _provider = await _gateway.chat(  # ← async, non-blocking
                req.question,
                context="",
                system_prompt="Bạn là SCP — một trợ lý AI thông minh. Trả lời ngắn gọn, chính xác, bằng tiếng Việt.",
                task="chat",
            )
            if _ollama_answer:
                _ai_answer = _ollama_answer
                logger.info(f"[CHATBOT] Ollama ({_provider}) generated answer: {_ollama_answer[:80]}...")
        except Exception as _ollama_err:
            logger.warning(f"[CHATBOT] Ollama call failed: {_ollama_err}")
```
(The `LLMGateway.chat` method is already async — uses `httpx.AsyncClient` internally, no thread needed.)

**Repro hypothesis:**
1. Start SCP with Ollama running.
2. Open 2 terminals, both running: `watch -n 1 'curl -s http://localhost:8000/health | jq .status'`.
3. In a third terminal, send a chatbot-style request (no `ai_answer`): `curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Viết một bài thơ về mùa thu"}'`.
4. Observe both `/health` watches freeze for 30-90 seconds (Ollama generation time). They resume only after `chat_sync` returns.

**Why R8 missed:** R8 deep-read `api_server.py` but focused on the attack-mode monitor (R8-1 area, lines 354-385) and lifespan. The `chat_sync` call at line 652 looks superficially correct — it's wrapped in try/except, and `chat_sync`'s docstring claims it handles async contexts. The blocking behavior is only visible if you trace INTO `chat_sync` and notice that `future.result()` is synchronous. Ruff ASYNC100 detects direct `time.sleep()` / `requests.get()` in async def, but does NOT detect "sync function call that internally blocks" — that requires inter-procedural analysis.

---

### R9-5 — `judge.verdict_history` race: append+reassign vs iterate (LOW)

**File:** `runtime/judge.py:1169` (iterate in `get_stats`) + `runtime/judge_parts/judgecore_mixin.py:1692-1693` (mutate in `judge()`)
**Bug class:** race condition (concurrent list mutation + iteration without lock)
**World tool:** semgrep `python.lang.security.audit.race-condition` + ruff RUF006 + manual review

**Root cause (TẠI SAO):** `judge.judge()` runs in a worker thread (via `asyncio.to_thread(judge.judge, ...)` at `api_server.py:788`). `judge.get_stats()` is called by admin endpoints from the asyncio event loop thread (e.g., `engine.get_report()` at `runtime/engine.py:558` → `judge.get_stats()`). They share `self.verdict_history` WITHOUT a lock:

```python
# judgecore_mixin.py:1692-1693 (worker thread, inside judge()):
self.verdict_history.append(verdict)
self.verdict_history = self.verdict_history[-100:]  # reassigns list reference
```

```python
# judge.py:1169-1172 (event loop thread, inside get_stats()):
for v in self.verdict_history:  # iterates list
    verdicts[v.verdict] = verdicts.get(v.verdict, 0) + 1
return {
    "total_verdicts": len(self.verdict_history),
    ...
}
```

CPython's list iterator caches `ob_size` at creation. If `judge()` calls `.append()` (changing `ob_size`) or reassigns `self.verdict_history = self.verdict_history[-100:]` while `get_stats()` is mid-iteration, the iterator raises `RuntimeError: list changed size during iteration`. The asyncio event loop propagates this as a 500 error to the admin client.

The reassignment `self.verdict_history = self.verdict_history[-100:]` is ALSO racy — if `get_stats()` reads `self.verdict_history` between the append and the reassignment, it gets one list; if it reads after, it gets the new (shorter) list. Not a crash, but inconsistent stats (e.g., `total_verdicts` says 100 but the `for` loop only saw 95 because the reassignment happened mid-iteration).

This is the EXACT SAME bug class as R8-6 (healing_history race), just in a different file. R8-6 fixed `healing_v14.healing_history` but missed `judge.verdict_history`.

**Before code (`judge.py:168` init, `:1169-1172` read, `judgecore_mixin.py:1692-1693` write):**
```python
# judge.py:168 (init — NO lock)
self.verdict_history: list[JudgeVerdict] = []

# judgecore_mixin.py:1692-1693 (worker thread — NO lock)
self.verdict_history.append(verdict)
self.verdict_history = self.verdict_history[-100:]  # [FIX LEAK] Cap to 100

# judge.py:1169-1172 (event loop thread — NO lock)
for v in self.verdict_history:
    verdicts[v.verdict] = verdicts.get(v.verdict, 0) + 1
return {
    "total_verdicts": len(self.verdict_history),
    ...
}
```

**Suggested fix:** Add a `threading.Lock` in `__init__`, guard the mutation in `judgecore_mixin.py`, and snapshot in `get_stats()` before iterating:
```python
# judge.py:168 (init — add lock)
import threading
...
self.verdict_history: list[JudgeVerdict] = []
self._verdict_history_lock = threading.Lock()

# judgecore_mixin.py:1692-1693 (worker thread — guard with lock)
with self._verdict_history_lock:
    self.verdict_history.append(verdict)
    if len(self.verdict_history) > 100:
        self.verdict_history = self.verdict_history[-100:]

# judge.py:1169-1172 (event loop — snapshot under lock, iterate snapshot)
with self._verdict_history_lock:
    history_snapshot = list(self.verdict_history)
for v in history_snapshot:
    verdicts[v.verdict] = verdicts.get(v.verdict, 0) + 1
return {
    "total_verdicts": len(history_snapshot),
    ...
}
```

**Repro hypothesis:** Run SCP under load: 10 concurrent `/ask` requests (each triggers `judge.judge()` in a worker thread → appends to `verdict_history`) + admin polling `/v100/status` (calls `get_stats()` from event loop). Eventually a `RuntimeError: list changed size during iteration` appears in the API response (500) or server logs.

**Why R8 missed:** R8-6 fixed `healing_v14.healing_history` (the SAME bug class) but only in `healing_v14.py`. R8 did not grep for other `self.X_history.append(...)` patterns across the codebase. The `verdict_history` mutation is in `judgecore_mixin.py` (not `judge.py` itself) — it was extracted to a mixin file during a refactoring task (per the comment at `judge.py:1181-1194`). R8 deep-read `judge.py` but did not trace into `judge_parts/judgecore_mixin.py`.

---

### R9-6 — `healing_v14.error_patterns` race: dict mutation vs snapshot (LOW)

**File:** `runtime/healing_v14.py:174-175` (mutate in `monitor()`) + `:370` (read in `get_stats()`)
**Bug class:** race condition (concurrent dict mutation + snapshot without lock)
**World tool:** semgrep `python.lang.security.audit.race-condition` + manual review

**Root cause (TẠI SAO):** R8-6 added a `_history_lock` to `healing_v14.py` and used it to guard `healing_history` mutations + snapshots. But `error_patterns` (a `defaultdict(int)` at line 57) — which lives in the SAME file, 6 lines below the patched block — was NOT guarded:

```python
# healing_v14.py:174-175 (monitor(), called from judge.judge() worker thread — NO lock)
for issue in issues:
    self.error_patterns[issue["type"]] = self.error_patterns.get(issue["type"], 0) + 1
```

```python
# healing_v14.py:370 (get_stats(), called from admin endpoint event loop thread — NO lock)
"error_patterns": dict(self.error_patterns),  # snapshots dict while monitor() mutates it
```

`dict(self.error_patterns)` while another thread mutates the dict can raise `RuntimeError: dictionary changed size during iteration` (in older CPython) or produce inconsistent reads (in newer CPython). Even if no crash, the snapshot may include a partially-applied increment (e.g., key present but value not yet written).

R8-6's fix was INCOMPLETE — it patched `healing_history` but missed `error_patterns` in the same file, despite the two being accessed by the same pair of threads (worker thread via `monitor()` + `heal()`; event loop thread via `get_stats()`).

**Before code (`healing_v14.py:66` init, `:174-175` write, `:370` read):**
```python
# :66 (init — R8-6 added _history_lock, but only for healing_history)
self._history_lock = threading.Lock()

# :174-175 (monitor() — NO lock for error_patterns)
for issue in issues:
    self.error_patterns[issue["type"]] = self.error_patterns.get(issue["type"], 0) + 1

# :370 (get_stats() — NO lock for error_patterns)
"error_patterns": dict(self.error_patterns),
```

**Suggested fix:** Extend the existing `_history_lock` to also guard `error_patterns` (or add a separate `_patterns_lock`). Snapshot under lock in `get_stats()`:
```python
# :174-175 (monitor() — guard with lock)
with self._history_lock:
    for issue in issues:
        self.error_patterns[issue["type"]] = self.error_patterns.get(issue["type"], 0) + 1

# :370 (get_stats() — snapshot under lock)
with self._history_lock:
    error_patterns_snapshot = dict(self.error_patterns)
return {
    ...
    "error_patterns": error_patterns_snapshot,
    ...
}
```
(Or rename `_history_lock` to `_state_lock` to reflect it now guards both fields.)

**Repro hypothesis:** Run SCP under load with healing issues triggering (e.g., high error rate → `monitor()` called from worker thread). Poll `/v98/status` or any admin endpoint that calls `healing.get_stats()`. Eventually `dict(self.error_patterns)` raises `RuntimeError` or returns inconsistent counts.

**Why R8 missed:** R8-6 specifically traced the `healing_history` list mutation pattern (append + truncate) and added a lock for it. The `error_patterns` dict mutation is on a different line (174-175 vs 213-218 for `healing_history`) and uses a different access pattern (`self.error_patterns[k] = v` vs `self.healing_history.append(...)`). R8-6's grep was for `\.append\(` + `for.*in self\.healing_history` — did not catch `dict(self.error_patterns)` 6 lines below the patched block.

---

### R9-7 — `notifications._recent` race: R8-1's fix introduced a NEW race (MEDIUM)

**File:** `runtime/notifications.py:174` (mutate in `notify()`) + `api_server.py:367-372` (iterate in `_attack_mode_monitor`)
**Bug class:** race condition (concurrent list mutation + iteration without lock) — **regression introduced by R8-1's fix**
**World tool:** semgrep `python.lang.security.audit.race-condition` + manual review

**Root cause (TẠI SAO):** R8-1 found that `_attack_mode_monitor` queried a non-existent SQLite `notifications` table (query raised `sqlite3.OperationalError`, swallowed by `except Exception: logger.debug`). R8-1's fix replaced the SQL query with direct iteration of the in-memory `_notif._recent` list:

```python
# api_server.py:367-372 (R8-1 patch — daemon thread, iterates _recent WITHOUT lock)
_recent = getattr(_notif, "_recent", []) or []
kill_count = sum(
    1 for _n in _recent
    if _n.get("timestamp", 0) > _cutoff
    and _n.get("event_type") == "governance_kill"
)
```

But `_recent` is mutated by `notify()` (called from `judge.judge()` in a worker thread) WITHOUT a lock:

```python
# notifications.py:174 (worker thread, mutates _recent WITHOUT lock)
if self.config.dashboard_enabled:
    self._recent.append(notification)
```

The R8-1 patch introduced a NEW race: the `sum(1 for _n in _recent ...)` generator expression iterates `_recent` while `notify()` may be appending to it concurrently. CPython's list iterator caches `ob_size` at creation; if `notify()` appends (changing `ob_size`) mid-iteration, the iterator raises `RuntimeError: list changed size during iteration`. The `_attack_mode_monitor`'s outer `except Exception as e: logger.debug(f"[AUTO] Attack mode monitor: {e}")` swallows this — so the monitor silently dies and attack mode NEVER auto-enables. **This is the SAME functional failure R8-1 was supposed to fix** — R8-1's fix replaced one silent failure (dead SQL query) with another silent failure (race-induced crash swallowed by except).

Additionally, `_recent` is also accessed by `get_recent()` (line 259: `return self._recent[-limit:]`) and `stats()` — both called from admin endpoints (event loop thread). So `_recent` has 3 concurrent accessors (worker thread via notify, daemon thread via monitor, event loop thread via admin endpoints), ALL without a lock.

**Before code (`notifications.py:95` init, `:174` write, `api_server.py:367-372` read):**
```python
# notifications.py:95 (init — NO lock)
self._recent: list[dict[str, Any]] = []

# notifications.py:174 (worker thread — NO lock)
if self.config.dashboard_enabled:
    self._recent.append(notification)

# api_server.py:367-372 (daemon thread — NO lock, R8-1 patch)
_recent = getattr(_notif, "_recent", []) or []
kill_count = sum(
    1 for _n in _recent
    if _n.get("timestamp", 0) > _cutoff
    and _n.get("event_type") == "governance_kill"
)
```

**Suggested fix:** Add a `threading.Lock` to `UserNotificationSystem.__init__`, guard the mutation in `notify()`, and expose a thread-safe count method that the attack-mode monitor can call:
```python
# notifications.py:95 (init — add lock)
import threading
...
self._recent: list[dict[str, Any]] = []
self._recent_lock = threading.Lock()

# notifications.py:174 (worker thread — guard with lock)
if self.config.dashboard_enabled:
    with self._recent_lock:
        self._recent.append(notification)
        if len(self._recent) > 1000:  # cap to prevent unbounded growth
            self._recent = self._recent[-500:]

# notifications.py — add thread-safe count method
def count_recent_by_type(self, event_type: str, cutoff_ts: float) -> int:
    """Count recent notifications of a type since cutoff_ts. Thread-safe."""
    with self._recent_lock:
        return sum(
            1 for n in self._recent
            if n.get("timestamp", 0) > cutoff_ts
            and n.get("event_type") == event_type
        )

# api_server.py:367-372 (daemon thread — use thread-safe method instead of direct iteration)
kill_count = _notif.count_recent_by_type("governance_kill", _cutoff) if _notif is not None else 0
```

**Repro hypothesis:**
1. Start SCP. Set up a script that sends 100 `/ask` requests concurrently, each triggering a KILL verdict (e.g., attack prompts that get governance-KILL'd → `notify(event_type="governance_kill")` called from worker threads).
2. The `_attack_mode_monitor` daemon thread polls every 5 minutes (line 385: `_time.sleep(300)`). When it polls, it iterates `_recent` via `sum(1 for _n in _recent ...)`.
3. If `notify()` appends to `_recent` during the monitor's iteration → `RuntimeError: list changed size during iteration` → swallowed by `except Exception: logger.debug(...)` → monitor continues but `kill_count` is wrong (or the iteration aborts early).
4. Check debug logs: `RuntimeError: list changed size during iteration` repeating. Attack mode never auto-enables (kill_count stays at 0 or returns partial count).

**Why R8 missed:** R8-1's fix was focused on replacing the dead SQL query with a working in-memory read. The fix verified that `_recent` exists and contains the right data — but did NOT consider thread safety. R8-1's "repro hypothesis" was "send 50 attack prompts, check eng.in_attack_mode" — which would PASS if the monitor happened to iterate when no `notify()` was in flight (the race window is small). The race only crashes under concurrent load, which R8-1's hypothesis test did not exercise. **This is DNA #22 (PASS ≠ TRUE) applied to R8 itself: R8-1's fix PASSED its hypothesis test but introduced a NEW silent failure.**

---

## Why R8 Missed These (Analysis)

R8 found 7 bugs across 6 bug classes. R9 found 7 MORE bugs across 3 bug classes (4 blocking-in-async + 3 race conditions). The gap analysis:

1. **Blocking-in-async (R9-1, R9-2, R9-3, R9-4):** R8's grep patterns scanned for `time.sleep()`, `requests.get()` inside async — but did NOT scan for "synchronous function call inside async def" generally. The 4 sites found by R9 call SCP-internal synchronous functions (`judge.judge()`, `run_deep_audit()`, `crawl_all()`, `check_and_maintain()`, `chat_sync()`) — these don't match the standard library patterns R8 grepped for. Detecting them requires either (a) inter-procedural analysis (does this function block?), or (b) a checklist of known-blocking SCP functions. R8 had neither.

2. **Race conditions (R9-5, R9-6, R9-7):** R8-6 found ONE race (`healing_v14.healing_history`) and fixed it. But R8 did NOT then grep for "other `self.X_history` / `self.X_patterns` / `self.X_recent` lists/dicts mutated + iterated across threads" — the same bug class exists in `judge.verdict_history`, `healing_v14.error_patterns`, and `notifications._recent`. R8-6 was a point fix, not a class fix. R9-7 is especially notable: R8-1's fix INTRODUCED a new race (replacing dead SQL with direct list iteration) — R8-1 verified the data flow but not the concurrency safety.

3. **File scope:** R8 deep-read 6 files + 5 "plus" files. R9 deep-read 12 files (including `api/routes/import_routes.py`, `api/routes/v102_v103_routes.py`, `runtime/judge_parts/judgecore_mixin.py`, `llm_gateway/client.py`, `runtime/notifications.py`). 4 of R9's 7 findings are in files R8 did NOT deep-read.

4. **Cross-file tracing:** R9-4 (chat_sync blocking) required tracing from `api_server.py:652` INTO `llm_gateway/client.py:581-587` to see that `future.result()` blocks. R9-5 required tracing from `judge.py:1169` INTO `judgecore_mixin.py:1692` to find the mutation site. R9-7 required tracing from `api_server.py:367` INTO `notifications.py:174`. R8's grep-only approach (without inter-file trace) could not find these.

---

## Coverage Limits (DNA #23 — honest disclosure)

1. **Grep-only audit:** `ruff`, `bandit`, `semgrep`, `vulture`, `pyright`, `CodeQL` binaries are NOT installed in this sandbox. Patterns were replicated via the Grep tool (ripgrep-based). Bug classes requiring data-flow analysis (taint propagation, inter-procedural blocking detection) are UNDER-COVERED — only manual trace was used.

2. **No test execution:** Findings are based on static code analysis only. No runtime repro was performed. Repro hypotheses are reasoning, not verified failures.

3. **Deep-read scope:** 12 files deep-read (api_server.py, runtime/judge.py, runtime/judge_parts/judgecore_mixin.py, autofix/engine.py, runtime/healing_v14.py, runtime/storage_manager.py, meta/why_engine.py, api/routes/v105_routes.py, api/routes/import_routes.py, api/routes/v102_v103_routes.py, llm_gateway/client.py, runtime/notifications.py) + targeted grep on the other 359 .py files. Bugs in non-deep-read files that don't match the grep signatures are NOT covered.

4. **Dead code excluded:** `api/routes/stream_routes.py`, `api/routes/threat_routes.py`, `api/routes/audit_routes.py`, `api/routes/prediction_routes.py` are NOT wired into the FastAPI app (grep for `include_router` confirms only 9 routers are registered; these 4 are not among them). Bugs in these files were NOT reported (no runtime consequence — DNA #26).

5. **No frontend/dashboard audit:** This subagent's scope is Python only. The Next.js dashboard was audited by Subagent A (Round 10 Self-Audit).

6. **Concurrency bugs require stress testing to repro:** The race conditions (R9-5, R9-6, R9-7) have small windows. The repro hypotheses describe HOW to trigger them, but actual crash frequency depends on load + timing. They are real bugs (the mutation+iteration pattern is unsafe by CPython semantics) but may be rare in practice.

7. **Time-boxed:** This audit prioritized depth (7 rigorous findings with exact line numbers + repro hypotheses + concrete fixes) over breadth. A more thorough audit might find additional bugs in `core/`, `security/`, `meta/`, `data_sources/` modules that were grep-scanned but not deep-read.

---

## Conclusion

R9 (Subagent B) found **7 NEW root-cause bugs** that R8 missed, across 3 bug classes:

- **4 blocking-in-async bugs** (R9-1 CRITICAL, R9-2/R9-3/R9-4 HIGH): synchronous long-running calls inside `async def` handlers block the asyncio event loop. The dev ALREADY knew about this pattern (fixed it in `api_server.py:788` + `openai_compat.py:54` + `chat.py:123`) but did NOT apply the fix consistently — 6 more sites were missed. R9-1 (import_routes) is CRITICAL because a batch import can block the server for 8-50 minutes.

- **3 race conditions** (R9-5/R9-6 LOW, R9-7 MEDIUM): concurrent list/dict mutation + iteration without lock. R9-5 and R9-6 are the SAME bug class as R8-6 (which fixed `healing_v14.healing_history`) — but R8-6 was a point fix, not a class fix; the same pattern existed in `judge.verdict_history` and `healing_v14.error_patterns`. R9-7 is a **regression introduced by R8-1's fix** — R8-1 replaced a dead SQLite query with direct list iteration, creating a new race that silently crashes the attack-mode monitor (the exact failure R8-1 was supposed to fix).

**DNA #22 (PASS ≠ TRUE) applied recursively (level 3):** R8 reported 7 fixes applied + 7 NEW bugs found. R9 found 7 MORE bugs in the SAME codebase that R8's scanners + fixes did not catch. R8's PASS was not TRUE — there were (at least) 7 more root-cause bugs lurking. Critically, R8-1's fix INTRODUCED a new bug (R9-7) — proving that even fixes need re-auditing.

**Recommendation for orchestrator (Subagent D, Task 2):** Patch R9-1 through R9-7 in real Python. The fixes are concrete (exact code provided in each finding's `suggested_fix`). Priority order:
1. R9-1 (CRITICAL — import_routes blocks server for minutes)
2. R9-4 (HIGH — chat_sync blocks event loop for every chatbot request)
3. R9-2 + R9-3 (HIGH — admin endpoints block event loop)
4. R9-7 (MEDIUM — R8-1 regression, attack-mode monitor silently dies under load)
5. R9-5 + R9-6 (LOW — race conditions, rare but real)

For each patch: `python3 -c "import ast; ast.parse(open('FILE').read())"` to verify syntax, then grep-verify the fix is present at the patched line.
