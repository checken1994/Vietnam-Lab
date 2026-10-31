# L0a — Cách ly dữ liệu test khỏi kho học sống + provenance (DONE, maker Claude)

- Lane: `l0a-test-isolation` · branch `claude/l0a-test-isolation` · worktree `D:/scp/.claude/worktrees/l0a-test-isolation`
- Parent: `dee3ee5e` (main). SHA commit: xem báo cáo cuối (một file đã commit không thể tự chứa SHA của chính nó).
- Trạng thái: PASS trong phạm vi đã kiểm (23 test L0a, 18 mutant, broad 7 chunk, probe uvicorn thật). KHÔNG có nghĩa là mọi writer của SCP đều đã cách ly: xem "Còn hở".
- KHÔNG đụng: `D:/scp/data` (chỉ đọc qua bản sao), `capability_state.json` thật (vẫn `revoked:true, epoch 15, reason "test"`, mtime 30/10 21:11:35, chưa mở khóa: owner/hub quyết), `scp/learning/lesson_ledger.py`, `tools/seed_lesson.py`, `ledger_stage.py`, `scp/forecast/**`, `scp/calibration/**`, `verification_stage.py`, `delivery_rules.py`, `answer_relevance.py`, `tests/conftest.py`, `doubt_cron.py`, `why_gate.py`, config/container production.

## 1. Gốc lỗi + probe (đỏ-trước, FA-09)
Gốc: writer tự tính đường dẫn kho học/trạng thái (`PROJECT_ROOT/data`, `"data"` theo cwd, `os.environ.get("SCP_DATA_DIR","data")`). Chúng không biết bản ghi sinh ra từ tiến trình nào (pytest, probe hay server), và reader không phân biệt được nguồn ⇒ test ghi vào kho sống, rồi SCP "học" lại từ chính test của nó.

Probe tái hiện trên `dee3ee5e` (worktree base riêng, "kho runtime giả" = `SCP_DATA_DIR` tạm + cwd/data, `DEFAULT_DB_PATH` cũng trỏ vào kho giả ⇒ KHÔNG chạm data thật):
- `ErrorStore()` ghi `error_store.jsonl` và `error_store_rejected.jsonl` theo cwd, bỏ qua `SCP_DATA_DIR` (FA-11 ErrorStore).
- `get_failure_ledger()` ghi vào `PROJECT_ROOT/data/failure_ledger.sqlite3`.
- `get_kb_store()` ghi vào `data/kb_evolve.sqlite` theo cwd.
- `FalsificationEngine().record_refutation` ghi cùng ErrorStore. Đây cũng là đường của math_lessons, routing_lessons và answer_relevance lessons.
- Reader `lookup_lesson` / `get_anti_patterns_for_module` / `ErrorStore.get_recent` trả cả bản ghi cũ lẫn bản ghi test.
- Bằng chứng: `red_before_dee3ee5e.log` cho 23 failed (ModuleNotFoundError ×9 với nhánh cần API mới; 14 AssertionError hành vi như "test process wrote into the runtime learning store / failure ledger / kb_evolve store / v91 audit", "runtime reader consumed a legacy_untrusted lesson", "probe process wrote into the runtime learning store"…). sha256 toàn bộ `data/` của worktree base trước/sau lần chạy đỏ: KHÔNG đổi (test được viết để đỏ TRƯỚC mọi thao tác ghi vào đích thật).

## 2. Bổ sung phạm vi: `capability_state.json` (Hands production bị KHÓA)
- **File:** `D:/scp/data/security/capability_state.json` = `{"actor":"operator","epoch":15,"reason":"test","revoked":true,...}` (đọc, không sửa).
- **Test thủ phạm (VERIFIED):** `tests/T03_capability/test_flow_04_control_hands_scp_standard.py::TestFlow04ControlHands::test_hands_capabilities_revoke_requires_token`, tức `POST /v3/hands/capabilities/revoke {"reason":"test"}`. Route gọi `_hands.revoke_capabilities(payload.reason, "operator")`: actor `operator` được viết cứng, khớp đúng chữ ký của file sống.
- **Cơ chế:** `scp/api/routes/hands_routes.py:27` khai báo `_hands = HandsExecutor()`, một singleton tạo lúc import. `security_dir` mặc định của nó là `<repo>/data/security` (bỏ qua `SCP_DATA_DIR`). Fixture `app_with_pc_token` chỉ cách ly `pc_controller_routes._controller`, KHÔNG cách ly `hands_routes._hands`. Epoch 15 ⇒ đã revoke/restore nhiều lần qua nhiều lần chạy suite từ cây main.
- `/v3/pc/kill` (`test_api_token_boundary.py:43,47`, `test_flow_04:222,227`) KHÔNG ghi capability_state: nó ghi `KILL_SWITCH` của controller đã được fixture cách ly. Nghi vấn của orchestrator ở điểm này đã được loại trừ bằng đọc code đường `engage_kill_switch`.
- **Đỏ-trước:** `cap_red_before.txt`. Trên base, đã xóa `data/security/capability_state.json` của worktree base rồi chạy đúng test đó với `SCP_DATA_DIR=<tmp>`. Kết quả: 1 passed, và sinh ra `<repo>/data/security/capability_state.json = {actor:operator, reason:test, revoked:true, epoch:1}`, NGOÀI thư mục tạm (trong tmp chỉ có `request_runs.jsonl`).
- **Sửa:** `HandsExecutor` mặc định lấy `default_state_dir("hands"|"security")`: runtime giữ nguyên `<repo>/data/...`, test/probe/fixture sang thư mục cách ly. `CapabilityAuthority._persist` gọi `guard_write` trước khi ghi byte nào, nên tiến trình không phải runtime KHÔNG BAO GIỜ revoke/restore được trạng thái sống: nó nhận `LearningIsolationError` (một PermissionError).
- **Sau sửa:** test thủ phạm chạy xanh và ghi vào thư mục cách ly. sha `data/security` của worktree trước/sau không đổi; file thật không đổi.
- Các writer khác orchestrator nêu, đã sửa: attack predictions `v91_upgrade_audit.jsonl` (predictor.py), `regression_watch.jsonl` (RegressionWatcher), `tier3_auto_audit.jsonl` / `autofix_audit.jsonl` (AutoFixEngine, Tier3AutoConfig), `hands/audit.jsonl` (HandsExecutor). CHƯA sửa: `why_gate_audit.jsonl` (1.800 dòng ddos cũng nằm ở đây). Lý do: `why_gate.py` thuộc PROTECTED_PATHS và mặt trận WHY. Đường ghi: `test_security_store_bounds.py::test_attack_predictor_history_is_bounded` (120 lần gọi) → `predictor.predict_cyber_attack` → `get_why_gate()` (singleton, `data_dir="data"`) → `why_gate_audit.jsonl`. Ghi chú: STRONGLY SUPPORTED bằng đọc code + đếm dữ liệu, không chạy riêng để không ghi thêm vào data.

## 3. Thiết kế (tổng quát, không regex đường dẫn, không suy từ nội dung)
`scp/core/learning_provenance.py`:
- `current_provenance()` ∈ {runtime, test, probe, fixture, unknown}, xác định từ NGỮ CẢNH TIẾN TRÌNH:
  - tín hiệu harness: pytest tự đặt `PYTEST_CURRENT_TEST` và nạp `_pytest` vào `sys.modules`; tiến trình con của test thừa kế env này;
  - khai báo `SCP_PROVENANCE`.
  - Harness thắng khai báo: pytest không thể tự xưng `runtime`. Khai báo lạ ⇒ `unknown`, không phải runtime (fail-closed).
- Gốc runtime = `<repo>/data` (đích bind-mount) + `SCP_DATA_DIR` **lúc tiến trình khởi động**, đọc từ `nt.environ`/`posix.environ`, bản chụp lúc boot mà `monkeypatch.setenv` không sửa được. Một test tự trỏ `SCP_DATA_DIR` về `tmp_path` thì không biến tmp thành kho runtime; còn tiến trình được KHỞI CHẠY với `SCP_DATA_DIR=X` thì X được bảo vệ.
- Writer mặc định: runtime ghi `runtime_data_dir()/file` (tôn trọng `SCP_DATA_DIR`, sửa FA-11); không phải runtime ghi `SCP_ISOLATED_DATA_DIR` hoặc `%TEMP%/scp-isolated-learning/<prov>/pid-<pid>` (thư mục cách ly nằm trong gốc runtime ⇒ raise).
- `guard_write(path)`: không phải runtime + đích trong gốc runtime ⇒ `LearningIsolationError`, raise trước mọi I/O.
- Bản ghi mang `provenance` có cấu trúc: cột SQLite `provenance TEXT NOT NULL DEFAULT 'legacy_untrusted'` (thêm bằng ALTER, giữ nguyên dòng cũ) và trường `provenance` trong JSONL.
- Reader chỉ trả bản ghi CÙNG provenance với tiến trình đọc: runtime chỉ đọc runtime; `legacy_untrusted` không bao giờ được trả.
- Danh tính bản ghi gồm provenance: fingerprint của failure_ledger, `_scoped_id` cho lesson/pattern của kb. Nhờ vậy quan sát runtime không cộng dồn vào (và chìm trong) dòng legacy/test, và `INSERT OR REPLACE` không ghi đè dữ liệu cũ.

## 4. Writer kho học/trạng thái: trước → sau
| Writer | Trước (đường tự tính) | Sau | Reader lọc provenance |
|---|---|---|---|
| `epistemic/failure_ledger.py` FailureLedger | `PROJECT_ROOT/data/failure_ledger.sqlite3` (bỏ qua SCP_DATA_DIR) | `learning_store_path` + guard ở `__init__`/record/resolve + cột provenance | `get_anti_patterns_for_module`, `get_unresolved_bugs`, `count_failures`, `resolve_failure` |
| `meta/kb_evolve.py` KBAccumulationStore | `"data"` theo cwd | như trên; lessons + evolved_patterns có provenance; `_scoped_id` | `lookup_lesson` (why_gate +0.1), `get_patterns` (scanner), `record_pattern_detection`, `stats` |
| `brain/error_store.py` ErrorStore (+ `_SPAM_REJECTED_PATH`) | `data/error_store.jsonl` theo cwd (FA-11) | `learning_store_path`; guard trong `add`/`clear`/trim; trường provenance | `get_recent`, `get_by_domain`, `get_by_error_type`, `stats_by_domain`, `common_errors`, `count`, `stats` |
| FalsificationEngine refutation, `math_lessons`, `routing_lessons`, `answer_relevance` lessons | qua `ErrorStore()` | tự động theo ErrorStore (KHÔNG sửa file của các lane khác) | như ErrorStore |
| `security/capability_epoch.py` CapabilityAuthority | ghi bất kỳ đâu | `_persist` có guard (fail-closed) | n/a |
| `hands/hands_executor.py` (capability_state, audit, ledger, backups) | `<repo>/data/hands`, `<repo>/data/security` | `default_state_dir(...)` (runtime không đổi) | n/a |
| `security/predictor.py` v91 audit | `SCP_DATA_DIR` hoặc cwd `data` | `default_data_dir()` | n/a |
| `autofix/runner_phases/auto_rollback.py` regression_watch | `"data"` theo cwd | `default_data_dir()` | n/a |
| `autofix/engine.py` (autofix_audit, tier3, v91) + `tier3_mixin.Tier3AutoConfig` | `"data"` theo cwd | `default_data_dir()` | n/a |
| calibration (`ledger_stage.py`, `calibration/**`) | `_data_dir/calibration.sqlite` | **KHÔNG SỬA**: lane prediction-p0 | — |
| `doubt_cron.py` doubt_ledger/why_gate_audit reader | `Path("data")` | **KHÔNG SỬA**: lane journal-chain giữ; đề xuất dùng `default_data_dir()` ở phần writer | — |
| `why_gate.py` why_gate_audit | `"data"` theo cwd | **KHÔNG SỬA**: PROTECTED + WHY lane | — |
| `knowledge/learning_db` / `lesson_ledger.py` | — | **KHÔNG SỬA** (AI khác). Đề xuất: lesson_ledger ghi qua `learning_store_path` + `guard_write`, cột `provenance`, reader `WHERE provenance = current_provenance()` | — |
| `brain/error_store.py` KnowledgeStore, `brain/error_store_index.py` | `data/knowledge_store.jsonl`, `data/error_store.jsonl` theo cwd | CHƯA sửa. ErrorStoreIndex chỉ được dùng trong self-test của chính nó (đọc code) | — |

## 5. Test đỏ-trước / xanh
- File: `tests/T07_learning/test_l0a_learning_provenance_isolation.py`, 23 test.
- `dee3ee5e`: **23 failed**. Fix: **23 passed**.
- Nhánh runtime và "tiến trình khởi động với SCP_DATA_DIR" được kiểm bằng TIẾN TRÌNH CON thật, không có tín hiệu harness (không mock provenance). Mỗi tiến trình con assert rằng nó import đúng `scp` của checkout đang test (dev venv có sitecustomize ⇒ đặt `SCP_DEV_MAIN_ROOT`).

## 6. Mutant (18, khôi phục bằng sha256): 18/18 KILLED, sha256_restored=True cho cả 18
M01 bỏ tín hiệu harness (12F) · M02 khai báo runtime thắng harness (1F) · M03 guard không bao giờ chặn (3F) · M04 SCP_DATA_DIR lúc boot không phải gốc runtime (3F) · M05 mặc định của non-runtime = kho runtime (10F) · M06 reader failure_ledger bỏ lọc (2F) · M07 kb lookup bỏ lọc (2F) · M08 ErrorStore thấy mọi provenance (1F) · M09 ErrorStore quay về cwd data (8F) · M10 dữ liệu cũ bị gắn nhãn runtime (1F) · M11 kb gắn cứng runtime (2F) · M12 danh tính failure bỏ provenance (1F; ban đầu SỐNG SÓT ⇒ đã bổ sung test dùng đúng fingerprint cũ để giết) · M13 Hands security dir về repo (3F) · M14 bỏ guard `_persist` (1F) · M15 predictor về cwd (1F) · M16 RegressionWatcher về cwd (1F) · M17 Hands data dir về repo (1F) · M18 AutoFixEngine về cwd (1F).

## 7. Broad `dee3ee5e` vs fix (foreground, 7 chunk: T07, T02_contract, T03, T04_kernel, T05+T00+T02_security, 17 thư mục nhỏ, tests/test_*.py)
- base: 6894 passed / 50 failed · fix: 6918 passed / 33 failed (fix có thêm 23 test L0a; base chạy bản 16 test cũ của file mới).
- `comm -13` (fail mới ở fix) = **0 nodeid test**. File `.fail` của chunk root có 1 dòng rác log OTel ("Failed to export spans…"), không phải test.
- `comm -23` = 17: 16 test L0a đỏ trên base + `T00/test_meta_audit.py::test_meta_audit_bare_scp_imports_must_resolve_to_real_production_symbols` (base fail, fix pass; dao động giữa các lần chạy). `T03/test_flow_12…::test_why_engine_verifies_past_decisions` cũng dao động: fail ở cả base và lần chạy fix cuối.
- 33 fail tồn tại sẵn, giống nhau ở cả hai cây: goldset router ×2, dashboard proxy ×11, critical_chain_severed ×5, knowledge_reader_tiers ×3, dashboard middleware, b70 loopback, l0_dekeyword k8, sandbox dead proxy, gd2 structural, p1 boundary, live_cluster e2e, startup launcher, tier2/tier3 chatbot, flow_12.
- Một lần chạy fix trung gian đã bắt lỗi ARC-01 (pc_controller.py vượt 800 LOC do bản vá). Xử lý: rút bản vá khỏi pc_controller.py (KHÔNG gỡ dòng trắng để lách trần). File đó vẫn được bảo vệ bởi guard ở `CapabilityAuthority._persist`. Broad đã chạy lại toàn bộ trên code cuối.

## 8. Probe Reality (FA-12)
- **Runtime thật:** uvicorn `scp.api_server:app` ở `127.0.0.1:18781`, `SCP_DATA_DIR=<scratchpad>/probe_rt/data` (tạm), secret giả sinh tạm (không đọc .env). Lấy JWT qua `/auth/token`, rồi gửi 4 `POST /ask` thật: cả 4 đều 200, verdict FAIL vì không có provider. Kết quả: `probe_rt/data/error_store.jsonl` có 2 bản ghi `math_verify_mismatch_value` với **`provenance: "runtime"`**, nằm ĐÚNG trong `SCP_DATA_DIR` (FA-11 đã sửa). Chuỗi kiểm: `/ask` → `math_verification.run_math_verification` → `record_math_lesson` → `FalsificationEngine._get_error_store()` → `ErrorStore()` → `learning_store_path` → dòng JSONL thô đã đọc lại.
- **pytest ⇒ kho runtime giả:** chạy 114 test (L0a, flow_04 gồm cả test thủ phạm, security_store_bounds, falsification, failure_ledger, lesson_truthfulness) với `SCP_DATA_DIR` lúc boot = `probe_rt/data`: 114 passed. sha256 của kho học `error_store.jsonl` và `calibration.sqlite` KHÔNG đổi. `data/security`, `data/hands` của worktree KHÔNG đổi.
- **Phát hiện thật (còn hở):** cùng lần chạy đó, pytest VẪN ghi 3 file KHÔNG phải kho học vào `SCP_DATA_DIR` của runtime: `request_runs.jsonl` (traced_request ledger), `human_confirmations.jsonl` (confirmation_store), `ask_task_kernel.sqlite3` (kernel; checkpoint WAL). Thuộc lane journal-chain/kernel và lane khác; CHƯA sửa.

## 9. Báo cáo CHỈ-ĐỌC dữ liệu cũ (`tools/learning_provenance_report.py --data-dir D:/scp/data`)
Cách đọc: chụp file + -wal + -shm sang thư mục tạm rồi mở bản sao; sha nguồn trước/sau bằng nhau (`source_unchanged_by_report=True`); sha `D:/scp/data` trước/sau cả lane bằng nhau.
- `failure_ledger.sqlite3/failure_records`: 235, chưa có cột provenance ⇒ 235 legacy_untrusted; heuristic chuỗi "pytest" (chỉ thông tin, không phải cổng) = 204.
- `kb_evolve.sqlite/lessons`: 30 ⇒ 30 legacy_untrusted; heuristic pytest 30. `evolved_patterns`: 2 legacy_untrusted.
- `error_store.jsonl`: KHÔNG có trong `D:/scp/data`. `error_store_rejected.jsonl`: 25 ⇒ 25 legacy_untrusted.
- Khớp số của checker (235/204–205, 30/30). Lane quarantine sau có thể dùng tool này làm lineage.

## 10. Causal graph (FA-12) + Coverage matrix (FA-13)
```mermaid
graph TD
  P[Tiến trình] -->|PYTEST_CURRENT_TEST / _pytest / SCP_PROVENANCE| CP[current_provenance]
  CP -->|runtime| RD[runtime_data_dir / repo data]
  CP -->|test,probe,fixture,unknown| ISO[isolated_data_dir]
  BOOT[SCP_DATA_DIR lúc boot + repo/data] --> RR[runtime_roots]
  ISO -->|nằm trong RR| X1[LearningIsolationError]
  W[writer: FL/KB/ES/Cap/Hands/Predictor/Autofix] --> G[guard_write]
  G -->|non-runtime & RR| X2[raise trước I/O]
  G -->|ok| ROW[bản ghi + provenance]
  OLD[dòng cũ] -->|ALTER DEFAULT| LEG[legacy_untrusted]
  RDR[reader] -->|WHERE provenance = current| OUT[chỉ cùng nguồn]
  LEG -.x.-> OUT
```
| Nhánh | Test |
|---|---|
| harness ⇒ test, mặc định → isolated (ES/FL/KB/refutation/predictor/autofix) | `test_default_*`, `test_math_routing_relevance_*`, `test_attack_predictor_*`, `test_autofix_default_*` |
| khai báo probe/fixture/test; runtime bị harness chặn; khai báo lạ ⇒ unknown | `test_declared_provenance_cannot_escalate_to_runtime[5]` |
| non-runtime boot trên kho runtime: mặc định isolated + ghi tường minh bị chặn | `test_nonruntime_process_booted_on_runtime_store_never_writes_it` (tiến trình con) |
| isolated nằm trong gốc runtime ⇒ raise | `test_isolated_dir_configured_inside_runtime_root_is_refused` |
| repo/data luôn là gốc; SCP_DATA_DIR do test monkeypatch thì không | `test_repo_data_dir_is_always_a_runtime_root` |
| runtime ghi đúng SCP_DATA_DIR + provenance=runtime, KHÔNG suy từ nội dung (đường pytest trong nội dung vẫn ra runtime) | `test_runtime_process_writes_*` (tiến trình con) |
| reader runtime bỏ legacy + bản ghi test cài sẵn, KHÔNG xóa dữ liệu cũ, danh tính tách khỏi legacy | `test_runtime_reader_ignores_*` (tiến trình con) |
| reader của test cũng không thấy legacy | `test_test_process_reader_never_sees_legacy_rows` |
| capability: route thủ phạm, HandsExecutor mặc định, guard `_persist` | `test_hands_*` ×3, `test_capability_authority_refuses_*` (tiến trình con) |
| tool báo cáo chỉ-đọc | `test_legacy_report_tool_is_read_only_*` |
| UNPROVEN_BRANCH | `_boot_env` nhánh POSIX (`posix.environ`) chưa chạy trên Linux/container; nhánh except fallback; `record_pattern_detection` / `get_patterns` lọc provenance chỉ được phủ gián tiếp; `ErrorStore._load` trim >50k khi không ghi được; migration ALTER trên DB WAL đang mở bởi container production |

## 11. File
- Mới: `scp/core/learning_provenance.py`, `tools/learning_provenance_report.py`, `tests/T07_learning/test_l0a_learning_provenance_isolation.py`, `docs/evidence/l0a-test-isolation-done.final.md`.
- Sửa: `scp/brain/error_store.py`, `scp/epistemic/failure_ledger.py`, `scp/meta/kb_evolve.py`, `scp/hands/hands_executor.py`, `scp/security/capability_epoch.py`, `scp/security/predictor.py`, `scp/autofix/engine.py`, `scp/autofix/engine_parts/tier3_mixin.py`, `scp/autofix/runner_phases/auto_rollback.py`.
- Bằng chứng (scratchpad `.../scratchpad/l0a/`): `red_before_dee3ee5e.log`, `green_fix.log`, `mutants.log`, `broad/*.log|*.fail`, `probe_uvicorn.out`, `cap_red_before.txt`, `legacy_report.json`, `evidence_fake_before|after.sha`.

## 12. Ghi chú triển khai / rollback (hub cần biết)
- Lần đầu runtime mở `failure_ledger.sqlite3` / `kb_evolve.sqlite` thật, code sẽ `ALTER TABLE ADD COLUMN provenance DEFAULT 'legacy_untrusted'`: chỉ thêm cột, không xóa dòng. Hệ quả có chủ đích: why_gate mất +0.1 từ 30 lesson bị đầu độc; autofix không còn nhận 235 anti-pattern từ test.
- Rollback về code cũ SAU khi đã migrate: `kb_evolve` cũ dùng `INSERT INTO lessons VALUES (11 ?)` sẽ lỗi số cột (save_lesson trả False: không học thêm, fail-closed). `failure_ledger` cũ dùng INSERT có tên cột nên vẫn chạy, dòng mới mặc định legacy_untrusted.
- Rủi ro bảo mật còn lại: tiến trình server có `SCP_PROVENANCE`/`PYTEST_CURRENT_TEST` trong env sẽ dùng capability_state CÁCH LY (mặc định không revoke). Cùng mức đặc quyền với override `SCP_SECURITY_DATA_DIR` đã có sẵn; KHÔNG phải quyền mới, nhưng nên có kiểm tra cấu hình production từ chối các biến này.
- Mỗi tiến trình pytest để lại một `%TEMP%/scp-isolated-learning/test/pid-*` (đã dọn những thư mục do lane này tạo). Đề xuất (lane conftest): đặt `SCP_ISOLATED_DATA_DIR` về basetemp của pytest.

## 13. Còn hở (FA-11, chỉ báo, không lén sửa)
1. `why_gate_audit.jsonl` (why_gate singleton `data_dir="data"`) vẫn nhận ghi từ test: protected/WHY lane.
2. ~70 constructor khác còn `data_dir="data"` theo cwd (grep đếm 73 trước L0a). Đề xuất: lane sau chuyển dần sang `default_data_dir()`, mỗi writer kèm test đỏ-trước.
3. pytest với `SCP_DATA_DIR` runtime lúc boot vẫn ghi `request_runs.jsonl`, `human_confirmations.jsonl`, `ask_task_kernel.sqlite3` (probe §8).
4. `PCController()` mặc định vẫn ĐỌC capability_state sống (ghi đã bị guard chặn). `pc_controller/audit.jsonl` + `KILL_SWITCH` mặc định vẫn ở repo data.
5. `scp/history/migration.py` đọc `SELECT * FROM lessons` không lọc provenance và có thể đưa lesson test thành `candidate_only`. Cần lane riêng; test của nó dựng schema không có provenance.
6. `KnowledgeStore` / `ErrorStoreIndex` vẫn dùng đường cwd.
7. Không probe trong container (Linux) ⇒ nhánh `posix.environ` UNPROVEN.

## Chuỗi Tại Sao
1. **Gốc:** Tại sao SCP học từ test? Vì writer tự đoán đường kho bằng cwd/PROJECT_ROOT, kho sống là bind-mount của chính repo, và bản ghi không mang nguồn ⇒ pytest và server ghi cùng một file, reader không tách được. Tại sao không tách được? Vì "nguồn" chưa từng là dữ liệu có cấu trúc. Phương án lọc theo nội dung ("pytest" trong đường dẫn) chỉ là đoán. *(Kiểm: probe đỏ 23F trên dee3ee5e; đếm dữ liệu thật 235/30.)*
2. **Tại sao cách sửa đúng bằng Reality:** provenance lấy từ ngữ cảnh tiến trình do harness đặt (`PYTEST_CURRENT_TEST`, `_pytest`), không từ nội dung. Test `runtime_process_writes_*` cố ý dùng nội dung giống test (`/tmp/pytest-of-x/...`) mà vẫn ra `runtime`. Gốc runtime lấy từ env LÚC BOOT nên test không tự "đổi kho" được. Ghi bị chặn trước I/O. Reader lọc bằng SQL `provenance = ?`. *(Kiểm: uvicorn thật ghi provenance=runtime đúng SCP_DATA_DIR; pytest boot trên kho đó thì sha kho học không đổi.)*
3. **Tại sao tin / lineage:** bằng chứng đến từ các lineage khác nhau: tiến trình con thật (không mock), server uvicorn thật, dữ liệu thô JSONL/SQLite được đọc lại, base worktree tách biệt, 18 mutant bị giết, broad comm-13 = 0. Lỗ hổng của chính test đã bị mutant M12 phát hiện (sống sót lần đầu) và được vá. Maker = checker là cùng lineage (Claude) ⇒ CẦN checker khác lineage.
4. **Điều gì đổi kết luận / anti-Goodhart:** nếu container production đặt `SCP_PROVENANCE` hoặc có `_pytest` trong process ⇒ học runtime bị chuyển sang kho cách ly (mất học, KHÔNG đầu độc: hướng an toàn). Nếu một reader không đi qua class (như `history/migration.py`) thì lọc bị vòng qua. Nếu test tương lai viết tay đường `D:/scp/data/...` từ tiến trình con không mang env pytest thì guard không thấy được: tiến trình con xóa `PYTEST_CURRENT_TEST` bị coi là runtime. Không tối ưu theo con số test: không có assertion nào bị nới, không skip test nào, và pc_controller bị rút khỏi bản vá thay vì lách trần LOC.
5. **UNKNOWN_WITH_REASON:** (a) hành vi trên Linux/container của `posix.environ` (chưa chạy); (b) ALTER trên DB WAL đang được container giữ lock (chưa probe trên kho thật, vì cấm ghi data thật); (c) why_gate_audit, ~70 writer `data_dir="data"` và 3 writer non-learning còn ghi kho runtime; (d) ai/khi nào đã revoke epoch 1→15 trên file thật (chỉ biết chữ ký khớp test; không có journal riêng cho capability).

## Lần sau SCP học được gì từ lỗi này?
- Bài học (HYPOTHESIS cho tới khi có checker): "Một kho học không ghi NGUỒN gốc thì không phân biệt được Reality với chính bài kiểm tra của nó." Quy tắc rút ra: mọi sink học/trạng thái sống phải (1) lấy đường qua `learning_store_path` / `default_data_dir` / `default_state_dir`, (2) `guard_write` trước I/O, (3) có trường `provenance`, (4) reader lọc theo provenance. Writer mới không theo quy tắc này nên bị T00 meta-audit chặn (đề xuất lane enforcement: cấm literal `"data"` làm default trong `scp/`).
- Trạng thái sống nhạy cảm (capability, kill switch) cần journal ai/khi nào/tiến trình nào đã đổi; nếu không, lần sau vẫn chỉ đoán được thủ phạm từ chữ ký.
- Singleton tạo lúc import (`_hands = HandsExecutor()`) thoát khỏi fixture: fixture cách ly phải bao MỌI singleton của router được include, hoặc product phải tự an toàn theo provenance như hiện nay.
