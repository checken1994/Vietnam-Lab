# Original User Request

## 2026-09-08T12:26:38Z

# Teamwork Project Prompt — R2, R3, R6 Remediation

Dự án SCP (Agent OS) đang cần vá 3 lỗ hổng kiến trúc nghiêm trọng cuối cùng (R2, R3, R6) để đạt trạng thái Autonomous 24/7.

Working directory: c:\Users\check\Downloads\scp
Branch hiện tại: omega/gap-01-remediation (hoặc main tuỳ bạn checkout, hãy tạo nhánh mới nếu cần, ví dụ: remediation/R2-R3-R6).

MANDATORY BINDING: You are strictly bound by Zero-Trust and Fail-Closed principles. You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Any code modifications must explicitly enforce boundaries at the Database/Hardware level, not via RAM/Variables.

## Lỗ hổng cần vá

### R2: Execution Bypass (PCController)
- Vấn đề: `PCController` có các phương thức thực thi trực tiếp (VD: chạy command) mà thiếu boundary kiểm tra token hợp lệ từ Unified Broker.
- Yêu cầu: Thêm token boundary vào `PCController`. Bất kỳ request thực thi nào cũng phải có token (HMAC-SHA256 signature hợp lệ) được cấp phát đúng thẩm quyền. Chặn fail-closed nếu thiếu/sai token.

### R3: Provenance Forgery (Verifier receipts)
- Vấn đề: Các biên lai `verification.passed` (verifier receipts) đang thiếu độc lập (cryptographic provenance). Worker có thể tự giả mạo biên lai thành công để lừa Kernel.
- Yêu cầu: Thêm chữ ký số (cryptographic signature/HMAC) vào Receipt. Kernel phải verify chữ ký này trước khi commit trạng thái `COMPLETED`.

### R6: AutoFix Rollback (Cognitive loop perfect isolation)
- Vấn đề: Vòng lặp Cognitive/AutoFix thiếu cơ chế perfect isolation và rollback. Nếu AI áp dụng code lỗi, hệ thống bị hỏng (catastrophic corruption).
- Yêu cầu: Xây dựng cơ chế snapshot/rollback (có thể dùng `data/shadow/` hoặc git stash/restore) cho file trước khi AutoFix áp dụng patch. Tự động rollback nếu Reality Test (pytest) thất bại sau khi patch.

## Yêu cầu Bắt buộc (FA-12, FA-13)
- Vẽ Causal Graph và tạo file báo cáo `EMERGENCY_GAP_REPORT.md` (nếu phát hiện lỗ hổng lân cận).
- Phủ test cho toàn bộ nhân quả (Causal-Driven Test Generation) trong thư mục `tests/`. Chạy `pytest` phải xanh.
- Cuối cùng, tổng hợp kết quả (Fix steps, Test outcomes) vào artifact báo cáo.

## 2026-09-13T21:15:02Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: [none — teamwork routes from the description]

Hoàn thiện, kiểm thử và hợp nhất (commit) toàn bộ công việc của S26 (xóa 13.8k dòng code cũ) và S24 (logic Question Router T2-first) trong một lần một cách an toàn.

Working directory: `c:\Users\check\Downloads\scp`

## Requirements

### R1. Tích hợp S26 (Expert Unification)
Hoàn thành việc loại bỏ các file legacy (`judge_parts`, `slm_impls`, `slms.py`) theo đúng những gì S26 đã chuẩn bị. Đảm bảo code không bị gãy dependencies.

### R2. Tích hợp S24 (T2-first Routing)
Tích hợp file `question_router.py` và các đoạn code móc nối dở dang trong `ask_kernel_adapter.py`. 

### R3. Sửa lỗi Test (Khắc phục di chứng S22)
Sửa lại hàm `_disable_openrouter` trong bài test `test_ws_chat_fail_closed_when_no_answer_source_available` (thuộc T02) để tắt toàn bộ các LLM Provider mới (Groq, Cerebras, Gemini, Nvidia) vốn được thêm vào từ S22, giúp bài test này xanh trở lại.

## Acceptance Criteria

### Verification & Commit
- [ ] Chạy lệnh `pytest tests/T02_contract tests/T03_capability tests/T07_learning -q` trả về 0 failures.
- [ ] Các lỗi "đỏ" do test cũ gọi vào file đã bị xóa (FA-02) được báo cáo rõ hoặc có cơ chế bypass hợp lệ khi commit.
- [ ] Sau khi test pass, thực hiện chốt commit toàn bộ S24 và S26 vào nhánh hiện tại (`audit/runtime-guard-AUDIT-20260909`).

## 2026-09-21T17:02:21Z

Deep comprehensive audit of the entire SCP (Secure Control Plane) system — a large Python + TypeScript codebase covering a governed AI agent platform with LLM gateway, policy enforcement, backward traceability, knowledge management, and desktop/web dashboard.

Working directory: D:\scp
Integrity mode: development

## System Profile

- **Branch**: `feature/autonomous-mode-antigravity-v2` (HEAD: `6d6256f`)
- **Scale**: ~221,000 Python LOC, 1,142 .py files, 43 active subsystems, 1,937 tests
- **Stack**: Python 3.12 FastAPI backend, Next.js/React dashboard, Bun TypeScript microservices (LLM Bridge, Loop Scheduler)
- **Key subsystems** (by size): `core/` (71 files), `data_sources/` (69), `security/` (44), `meta/` (37), `autofix/` (35), `knowledge/` (26), `runtime/` (13), `api/` (8), `llm_gateway/` (5), `governance/` (4), plus 33 smaller modules
- **Test suites**: T00 (integrity), T01 (boot), T02 (contract), T03 (capability/integrity), T04 (kernel), T05 (gateway), T06 (verifier), T07 (learning), T08 (runtime), T09 (golden_task), T10 (recovery), T11 (release), T12 (unified_chatbot)
- **Existing audit tool**: `python tools/t00_meta_audit.py` — enforces test integrity against baseline
- **Existing test runner**: `pytest tests/ -q` — all 1,937 tests currently pass

## Requirements

### R1. Silent Bugs & Logic Errors

Identify code paths that silently produce incorrect results without raising errors. Focus areas:
- Functions that swallow exceptions or return default/empty values on failure without logging
- Conditional branches that can never be reached (dead branches)
- Type coercion or comparison bugs (e.g., `str` vs `int`, `None` equality)
- Off-by-one errors in slicing, pagination, or range operations
- Async/await misuse (missing `await`, unawaited coroutines, fire-and-forget)
- Mutable default arguments in function signatures
- Variable shadowing that changes semantics

### R2. Security & Secret Handling

Audit all security-sensitive code paths:
- Hardcoded secrets, API keys, tokens, or credentials in source files (not just `.env`)
- Secret leakage through logging, error messages, trace records, or API responses
- Authentication/authorization bypass possibilities in API endpoints
- Egress control gaps — can code send data to arbitrary external URLs?
- SQL injection vectors in any raw SQL construction
- Path traversal in file operations
- CORS/CSRF misconfiguration in the FastAPI server
- `.env` file containing production secrets committed to git
- The `redact_attributes()` function in `trace_contract.py` — verify its redaction coverage is complete

### R3. Architectural Integrity & Dead Code

Assess structural health:
- Modules that are imported nowhere (orphaned files)
- Functions/classes defined but never called
- Circular import chains
- God files (>500 LOC) that should be split
- Duplicate logic across subsystems (copy-paste code)
- Inconsistent naming conventions across subsystems
- Deprecated code still referenced (e.g., remnants of `zero_cost_guard`, `zero_cost_runtime`)
- Uncommitted files that appear to be part of the feature (`_trace_impl.py`, `trace_store.py`, changes to `trace_contract.py`) — should these be committed?

### R4. Test Coverage & Test Quality

Evaluate the test suite:
- Subsystems with zero or minimal test coverage
- Tests that assert trivially (e.g., `assert True`, `assert module is not None`)
- Tests that mock so heavily they don't test real behavior
- Tests that depend on external state (network, file system, specific ports)
- Missing edge case coverage for critical paths (ask flow, LLM gateway routing, governance checks)
- Test files that import from deleted modules
- Flaky test patterns (timing-dependent, order-dependent)

### R5. Runtime Correctness & Concurrency

Examine runtime behavior:
- Race conditions in shared mutable state (especially in `ChatMemoryStore`, `TraceLedger`, `KernelStorage`)
- Thread safety of SQLite operations (WAL mode, connection sharing)
- Resource leaks (unclosed file handles, database connections, HTTP sessions)
- Graceful shutdown handling — do all services clean up properly?
- Error propagation — do errors in subsystems surface correctly to the API layer?
- Memory growth patterns — any unbounded caches or growing data structures?

### R6. Configuration & Deployment Consistency

Check operational health:
- Environment variables referenced in code but not documented in `.env.example`
- Port conflicts or hardcoded port numbers that could collide
- Docker/compose configuration consistency with actual code
- Startup scripts (`start-scp.bat`, `start-scp.sh`) — do they match the actual required services?
- Dashboard build artifacts — is `.next/standalone` correctly gitignored?
- `requirements.txt` vs actual imports — missing or unused dependencies

## Acceptance Criteria

### Completeness
- [ ] Every one of the 43 active subsystems under `scp/` is covered in the audit
- [ ] All 6 requirement domains (R1-R6) are addressed with specific findings
- [ ] Each finding includes: file path, line number(s), severity, description, and evidence

### Severity Classification
- [ ] Findings are classified as CRITICAL / HIGH / MEDIUM / LOW / INFO
- [ ] CRITICAL: security vulnerabilities, data loss risk, or silent correctness bugs in production paths
- [ ] HIGH: bugs that would cause incorrect behavior under realistic conditions
- [ ] MEDIUM: code quality issues that increase maintenance risk or technical debt
- [ ] LOW: style inconsistencies, minor dead code, documentation gaps
- [ ] INFO: observations and improvement suggestions

### Evidence Quality
- [ ] Each finding cites specific file(s) and line number(s)
- [ ] Findings include a brief code snippet or grep evidence demonstrating the issue
- [ ] No speculative findings without concrete evidence from the actual codebase
- [ ] The report distinguishes between confirmed issues and potential risks

### Report Structure
- [ ] Executive summary with finding counts by severity
- [ ] Findings grouped by domain (R1-R6), then by severity within each domain
- [ ] A final section listing the top 10 most critical findings across all domains
- [ ] Output as a single structured markdown file

## Verification

### Programmatic Verification
- Run `pytest tests/ --collect-only -q` to confirm 0 collection errors (validates no broken imports exist)
- Run `python tools/t00_meta_audit.py` to confirm the existing meta-audit passes
- Use `grep -rn` to verify specific claims (e.g., "this function is never called" → grep confirms 0 callers)

### Agent-as-Judge Verification
- Each finding must be independently verifiable by reading the cited source file and line numbers
- The report should not contradict the actual test results (1,937 passing tests)
- No finding should claim a module doesn't exist if it actually does in the file system




## 2026-09-21T22:14:24Z

Fix all 46 findings from a comprehensive system audit of the SCP (Secure Control Plane) codebase. The audit report at `D:\scp\COMPREHENSIVE_AUDIT_REPORT.md` documents every finding with exact file paths, line numbers, severity classifications, code evidence, and a prioritized remediation roadmap (P0→P1→P2). All fixes must be implemented, tested, committed, and verified against the existing test infrastructure.

Working directory: D:\scp
Integrity mode: development

## Reference Material

- **Audit Report**: `D:\scp\COMPREHENSIVE_AUDIT_REPORT.md` (1,002 lines, 46 findings with evidence)
- **Branch**: `feature/autonomous-mode-antigravity-v2` (HEAD: `6d6256f`)
- **Codebase**: ~221K Python LOC, 43 subsystems, 1,937 existing tests

## Requirements

### R1. P0 — Critical Security & Data Integrity Fixes (11 findings)

Fix all 11 CRITICAL-severity findings:

1. **R2-01**: Add authentication to `/v3/trace/{trace_id}` endpoint in `api_server.py` — it must require admin verification before returning trace data.
2. **R2-02**: Fix `redact_attributes()` in `trace_contract.py` — must redact sensitive string values (not just keys), handle tuple headers like `("Authorization", "Bearer sk-...")`, add missing sensitive keys (`auth`, `dsn`, `connection_string`, `proxy`, `cert`), and restore string/list size limits.
3. **R3-01**: Commit untracked trace files (`_trace_impl.py`, `trace_store.py`, modified `trace_contract.py`), mount `_trace_impl.router` in `api_server.py`, and fix T12 tests that bypass the HTTP endpoint.
4. **R1-01**: Implement lazy initialization for the 6 stubbed `Judge` properties (`counter_response`, `canary_monitor`, `error_store`, `attack_memory`, `falsification`, `governance`) so the `/v98/` admin endpoints return real data instead of HTTP 503.
5. **R5-01**: Fix `ChatMemoryStore` race condition — add thread lock and OS file lock around file append and `_prune_if_needed()`.
6. **R5-02**: Fix `TraceLedger.append()` — serialize with exclusive lock, replace whole-file read with incremental tail reader.
7. **R5-03**: Fix `db_manager.py` — hold `_db_lock`/`_read_lock` throughout `conn.execute()` and row fetching, not just during connection retrieval.
8. **R1-02**: Replace the fake `EvidenceReplay.verify()` that returns hardcoded `{"ok": True}` with real verification logic or an explicit `NotImplementedError` with clear documentation.
9. **R4-01**: Replace at least the 23 `test_subsystem_*.py` import-only tests with functional tests that exercise actual subsystem behavior.
10. **R4-02**: Replace `assert True` in `chaos_recovery.py` with real crash injection and TaskKernel state recovery validation.
11. **R6-01**: Document all 165 missing environment variables in `.env.example` with descriptions, grouped by subsystem.

### R2. P1 — High-Severity Reliability & Deployment Fixes (18 findings)

Fix all 18 HIGH-severity findings:

1. **R6-02**: Update `start-scp.bat` to launch LLM Bridge on port 8081.
2. **R6-03**: Fix dashboard fallback URL from port 11434 to 8081 in `scp-backend-url.ts`.
3. **R6-04**: Fix Dockerfile — change `EXPOSE 8080` to `EXPOSE 8000`, change `CMD ["--help"]` to `CMD ["8000"]`.
4. **R2-03**: Add `enforce_egress_policy(url)` in `BrowserSession.navigate_and_read` and `open_visible`.
5. **R2-04**: Fix SQL injection in `LearningDB.execute_insert()` — validate table/column names against allowlist.
6. **R2-05**: Fix path traversal in `batch_benchmark_routes.py` — validate `job_id` with regex.
7. **R2-06**: Add authentication to `/swe-bench/v1/chat/completions` and `/metrics` endpoints.
8. **R1-03**: Fix broken `None` comparison in `type_flow_verifier.py` — check `test.comparators[0]`.
9. **R1-04**: Add explicit `encoding="utf-8"` to all file operations missing it across `external_trust.py`, `compare_results.py`, `run_benchmark.py`.
10. **R5-04**: Fix async transaction collision in `KernelStorage` — use asyncio-safe connection management instead of `threading.local()`.
11. **R5-05**: Fix connection pool leak in `KernelStorage._all_conns` — add bounded pool with proper cleanup.
12. **R5-06**: Add size bounds to all unbounded in-memory collections (`_threat_history`, `_alert_history`, `_incidents`, `_auth_failures`, `_JOBS`, `_CID_CACHE`).
13. **R5-07**: Fix graceful shutdown — await cancelled tasks, add thread stop events, close database connections, invoke `checkpoint_wal()`.
14. **R4-03**: Add meaningful tests for at least 10 of the 22 under-covered subsystems.
15. **R4-04**: Convert at least 10 AST-only "reality tests" to behavioral execution tests.
16. **R3-02**: Remove at least 30 of the 63 identified orphaned modules (prioritize the largest ones).
17. **R3-03**: Break at least the top 5 most critical circular dependency cycles using interface protocols.
18. **R3-04**: Refactor at least 2 of the "split-and-stitch" monkeypatching patterns to use proper class composition.

### R3. P2 — Medium/Low Architectural Cleanup (17 findings)

Fix the remaining MEDIUM, LOW, and INFO findings:

1. **R1-05**: Add logging to the silent `except Exception: pass` blocks in `Judge.domain_experts`.
2. **R1-06**: Add error inspection to the fire-and-forget fact-check task callback.
3. **R1-07**: Fix Mojibake character corruption in user-facing API responses.
4. **R2-07**: Add token verification to the LLM Bridge microservice.
5. **R2-08**: Move WebSocket auth from query params to headers; validate token before `websocket.accept()`.
6. **R3-05**: Remove stale `zero_cost_guard`/`zero_cost_runtime` references from spec files and maintenance scripts.
7. **R3-06**: Move `scp/audit_r8` and `scp/audit_r9` to `docs/audit_history/`.
8. **R3-07**: Move `scp/tests/` to `tests/internal/` and update `pytest.ini`.
9. **R3-08**: Standardize file naming to PEP 8 conventions where renaming won't break imports.
10. **R4-05**: Replace at least 20 of the 46 hardcoded `time.sleep` calls with polling or event sync.
11. **R4-06**: Document the 14 permanently skipped tests and create tracking issues.
12. **R5-08**: Add lock synchronization to `ConversationManager._sessions` and `risk_routes._incidents`.
13. **R5-09**: Track and cleanup spawned browser processes in `BrowserSession.open_visible`.
14. **R6-05**: Add missing 23 third-party dependencies to `requirements.txt`.
15. **R6-06**: Commit the dirty working tree files and ensure clean git status.
16. Remaining orphaned module removal (beyond R2's 30).
17. Remaining circular dependency breaks (beyond R2's top 5).

## Acceptance Criteria

### Test Suite Integrity
- [ ] `pytest tests/ -q` passes with 0 failures
- [ ] `python tools/t00_meta_audit.py` passes with 0 new regressions
- [ ] `pytest tests/ --collect-only -q` shows 0 collection errors
- [ ] Total test count increases (baseline: 1,937) — new functional tests must outnumber any removed stubs

### Security Fixes Verified
- [ ] `GET /v3/trace/{trace_id}` returns 401/403 without valid admin token
- [ ] `python -c "from scp.core.trace_contract import redact_attributes; print(redact_attributes([('Authorization', 'Bearer sk-12345')]))"` outputs `[REDACTED]` or equivalent masked value
- [ ] `grep -rn "enforce_egress_policy" scp/web_control/browser_session.py` returns at least 1 match
- [ ] `job_id` parameter in batch benchmark routes is validated against `^[a-zA-Z0-9_-]+$`

### Concurrency Fixes Verified
- [ ] `ChatMemoryStore` file operations are wrapped in both thread lock and file lock
- [ ] `TraceLedger.append()` uses exclusive lock and does not read the entire file
- [ ] `db_manager.py` holds lock throughout query execution, not just connection retrieval
- [ ] All unbounded in-memory collections have explicit size caps

### Architecture Verified
- [ ] `grep -rn "from scp.llm_gateway.zero_cost" scp/ tests/` returns 0 results
- [ ] At least 30 orphaned modules are removed
- [ ] `scp/audit_r8` and `scp/audit_r9` no longer exist in `scp/`
- [ ] No `types.FunctionType` rebinding patterns in at least 2 formerly "split-and-stitch" files

### Configuration Verified
- [ ] `.env.example` documents at least 150 environment variables (up from 32)
- [ ] `start-scp.bat` launches 4 services including LLM Bridge
- [ ] `Dockerfile` exposes port 8000 and defaults to starting the server
- [ ] `requirements.txt` includes all 23 previously missing dependencies

### Git Cleanliness
- [ ] All changes committed with descriptive messages
- [ ] `git status` shows clean working tree
- [ ] Pushed to `feature/autonomous-mode-antigravity-v2`

## Verification Resources

The codebase includes existing verification infrastructure:

1. **Test runner**: `pytest tests/ -q` — runs all 1,937 tests
2. **Meta-audit**: `python tools/t00_meta_audit.py` — enforces test integrity against baseline
3. **Collection check**: `pytest tests/ --collect-only -q` — validates all imports resolve
4. **Grep verification**: Use `grep -rn` to verify removal of dead references and presence of new guards

## 2026-09-22T12:50:00Z

Execute the next-stage development and verification cycle for SCP across three integrated goals: (1) Boot and verify the full SCP cluster live end-to-end (API Server port 8000, LLM Bridge port 8081, Web Dashboard port 3000) with backward-traceable chatbot interactions, shutting down all spawned server processes cleanly upon test completion, (2) Substantially expand subsystem functional test coverage and stress testing across core engines, and (3) Advance SCP Agent OS autonomous execution capabilities with bounded tool invocation and ledger-backed audit provenance.

Working directory: D:\scp
Integrity mode: development

## Reference Material

- **Branch**: `feature/autonomous-mode-antigravity-v2`
- **Recent Baseline**: 2,049 tests collected, 0 meta-audit regressions, 46 audit findings resolved
- **Audit Reports**: `D:\scp\COMPREHENSIVE_AUDIT_REPORT.md` and `D:\scp\BAO_CAO_KIEM_TOAN_TOAN_HE_THONG.md`

## Requirements

### R1. Live Cluster E2E Boot, Verification & Clean Shutdown
- Spawn the SCP cluster services (API server on port 8000, LLM Bridge on port 8081, and Web Dashboard on port 3000).
- Execute an end-to-end automated probe against the running cluster:
  - Submit a multi-turn chat prompt via WebSocket/HTTP.
  - Verify that the response contains backward-traceable metadata.
  - Query the secured `/v3/trace/{trace_id}` endpoint with valid admin credentials and confirm ledger provenance.
- Cleanly terminate all spawned server processes and child processes, ensuring no orphaned background tasks or ports remain bound.

### R2. Subsystem Functional & Stress Test Expansion
- Add new comprehensive behavioral test suites targeting under-tested subsystems (such as Epistemic Engine, TaskKernel, Self-Model, and Observability).
- Include concurrency stress tests that validate thread safety and data integrity under rapid sequential and parallel queries.
- Ensure all tests use real assertions and respect fail-closed invariants without simulated or import-only stubs.

### R3. SCP Agent OS & Autonomous Tool Execution Capabilities
- Extend the autonomous execution pipeline with bounded tools (e.g. system inspection, workspace analysis, safe command runner).
- Enforce capability boundaries and egress policies on all autonomous tool invocations.
- Ensure every autonomous execution step and tool output is recorded into the append-only audit ledger with cryptographic verification hashes.

## Acceptance Criteria

### Live System & Traceability
- [ ] Automated probe verifies HTTP 200 health on port 8000 and port 8081 during execution.
- [ ] Live chat exchange produces a valid trace ID, and querying `/v3/trace/{trace_id}` yields verified, redacted event records.
- [ ] Ports 8000, 8081, and 3000 are completely free and no zombie Python/Node processes remain after verification completes.

### Test Integrity & Coverage Expansion
- [ ] Total collected tests increase from the current baseline of 2,049 with 0 collection errors (`pytest tests/ --collect-only -q`).
- [ ] Meta-audit passes cleanly with 0 new regressions (`python tools/t00_meta_audit.py`).
- [ ] All new tests pass cleanly when run via pytest.

### Agent OS Autonomous Tooling
- [ ] Autonomous tool invocations correctly register in the ledger and enforce permission boundaries.
- [ ] Working tree is clean and all changes are committed with descriptive messages.

## Verification Resources

1. **Test Runner**: `pytest tests/ -q`
2. **Meta-Audit**: `python tools/t00_meta_audit.py`
3. **Port Check**: PowerShell `Get-NetTCPConnection -LocalPort 8000, 8081, 3000 -ErrorAction SilentlyContinue`
4. **Trace Verification**: Direct automated HTTP client probe querying `/api/chat` and `/v3/trace/{trace_id}`
