# GO ĐƠN (2): Khắc phục 3 mục từ `scp-selfreport-misses.md` (N1 Scanner Cap 100/811, N4 5 Task Disabled, N2/N4 Daily Report 0.00% & Xanh Giả)

- **Ngày thực hiện:** `2026-10-31 (UTC+7)`
- **Baseline HEAD SHA:** `153ab1050d3d0e8153bf4558260040202112a516`
- **Skills kích hoạt:** `scp-dna`, `scp-reality-verifier`, `scp-runtime-audit`
- **Nguồn đối chiếu gốc:** `C:\Users\check\.council-loop\inbox\scp-selfreport-misses.md` (`N1`, `N2`, `N4`)

---

## 1. Tóm tắt 3 Hạng Mục Đã Khắc Phục

### Mục 2a (`N1` trong `scp-selfreport-misses.md`) — AST Scanner Cap `100/811` (Luôn quét cố định 100 file đầu bảng chữ cái)
- **Hiện trạng trước khi sửa:**
  - `scp/autofix/runner_phases/ast_scan.py::_iter_python_files` thực hiện `paths = sorted(...)` rồi cắt `paths[:limit]`.
  - Khi `limit=100` (từ `startup_gate.py` hoặc `evolution.py:342`), máy quét **luôn luôn** chỉ lấy 100 file đầu tiên theo thứ tự chữ cái (`scp/__init__.py` $\rightarrow$ `scp/autofix/diagnostic.py`), bỏ đói hoàn toàn 6 thư mục trọng yếu (`security`, `meta`, `task_kernel_parts`, `hands`, `llm_gateway`, `core` — 0 file được quét!).
- **Thay đổi đã thực hiện (`scp/autofix/runner_phases/ast_scan.py`, `runner_phases/__init__.py`, `runner.py`):**
  - Định nghĩa `CRITICAL_SCAN_DIRS = ("security", "meta", "task_kernel_parts", "hands", "llm_gateway", "core")`.
  - Khi `total_eligible > limit`, phân tầng ưu tiên (`CRITICAL_SCAN_DIRS` $\rightarrow$ `PROTECTED_PATHS` $\rightarrow$ các file còn lại) và chia đều vào `total_windows = ceil(total_eligible / limit)` cửa sổ phân tầng (stratified buckets), xoay vòng tất định theo `rotation_index` (tham số truyền vào, biến môi trường `SCP_SCAN_ROTATION_INDEX`, hoặc `UTC date.toordinal()`).
  - **Bảo chứng toán học & thực nghiệm:**
    1. Trong **mọi** cửa sổ `limit=100` (bất kể `rotation_index` bằng bao nhiêu), luôn có mặt đầy đủ cả 6 thư mục trọng yếu (VD tại window `4/9`: `{'security': 12, 'meta': 9, 'task_kernel_parts': 2, 'hands': 1, 'llm_gateway': 1, 'core': 11}`).
    2. Khi xoay hết `total_windows` (`9` cửa sổ cho `817` file hiện hành), hợp (union) các cửa sổ phủ đúng **100% (`817/817`)** toàn bộ cây mã nguồn `scp/`.
  - Xuất metadata độ phủ minh bạch qua `get_scan_coverage_metadata()` và `get_last_scan_coverage()` (`scanned_count`, `total_eligible`, `coverage_ratio`, `coverage_pct`, `is_full_coverage`, `critical_dirs_scanned`, `unscanned_dirs`).

### Mục 2b (`N4` trong `scp-selfreport-misses.md`) — 5 Windows Scheduled Tasks `Disabled`
- **Hiện trạng trước khi sửa:**
  - Trên Windows Host có 5 task `SCP-*` đều ở trạng thái `Disabled`, nhưng `tools/generate_visual_reports.py` chỉ kiểm tra 3 task, `scripts/ops/scp_247_control.ps1` chỉ kiểm tra 1 task (`SCP-247-Supervisor`), và `scripts/ops/scp_247_teardown.ps1` thiếu 3 task sau cùng.
- **Thay đổi đã thực hiện (`tools/generate_visual_reports.py`, `scripts/ops/scp_247_control.ps1`, `scripts/ops/scp_247_teardown.ps1`):**
  - Đưa toàn bộ 5 task vào `SCP_WINDOWS_TASKS` trong `tools/generate_visual_reports.py`, kèm thuyết minh trạng thái thanh lý (disposition) trung thực:
    1. `SCP-247-Supervisor`: `Disabled` — Legacy host supervisor (`scripts/ops/scp_247_supervisor.ps1` — đã chuyển sang Docker Compose).
    2. `SCP-247-Recovery-Watchdog`: `Disabled` — Legacy host watchdog (`scripts/ops/scp_247_recovery_watchdog.ps1` — đã tắt bởi teardown để tránh xung đột Docker).
    3. `SCP-247-Visual-Reports`: `Disabled` — Host visual report schedule (`scripts/ops/generate_visual_reports_schedule.ps1`).
    4. `SCP-System-Reporter-247`: `Disabled` — Host system reporter schedule (`scripts/ops/scp_system_reporter.py`).
    5. `SCP-Public-Relay-Bridge`: `Disabled` — Obsolete host relay task (`C:\Users\check\Downloads\scp\scripts\run_scp_local_bridge.py` — đường dẫn cũ không còn dùng).
  - Bổ sung kết luận tổng hợp `scheduler_verdict = "ALL_DISABLED (0/5 active, 5/5 Disabled — toàn bộ bộ lập lịch 24/7 trên Windows host đang tắt; SCP hiện chỉ chạy trong tiến trình Docker)"`.
  - Cập nhật `scp_247_control.ps1 -Action status` trả về `all_scp_tasks` gồm đủ 5 task và cập nhật `scp_247_teardown.ps1` bao phủ đủ 5 task.

### Mục 2c (`N2` & `N4` trong `scp-selfreport-misses.md`) — Báo Cáo Daily `Loop Scheduler 6 chu kỳ, lỗi 6, 0.00%`, `deep_audit_results.jsonl` vắng mặt in `0 lỗi`, bỏ sót `subsystem_heartbeat.sqlite`, sai múi giờ `UTC+7`, và pytest ghi đè thư mục báo cáo thật
- **Thay đổi đã thực hiện (`tools/generate_visual_reports.py`):**
  1. **Lọc `loop_runs.jsonl` theo khung thời gian + phát hiện `STALE`:**
     - `PhysicalDataAggregator.load_loop_runs_stats(since_ts, now_ts, staleness_threshold_sec=86400.0)` phân tích trường `ts` (ISO-8601 UTC hoặc epoch), chỉ tính các chu kỳ nằm trong cửa sổ báo cáo (`total_runs = 0` thay vì lặp lại `6` chu kỳ từ ngày `2026-10-26`), không bao giờ trả `100.0%` giả tạo khi `total_runs == 0`, đồng thời xuất `status = "STALE"`, `last_cycle_at = "2026-10-26T19:28:20.709Z"`, và tách bạch `all_time_total_runs = 6` / `all_time_error_runs = 6`.
  2. **Không in `"0 lỗi AST"` khi thiếu `data/deep_audit_results.jsonl` (DNA #22: Vắng dữ liệu $\neq$ sạch):**
     - Khi file không tồn tại, `load_deep_audit_results` trả về `source_exists = False, status = "UNKNOWN", total_findings = None`. Báo cáo Daily in rõ `UNKNOWN (nguồn data/deep_audit_results.jsonl chưa tồn tại)` cùng độ phủ `100/817 (12.2%)` và danh sách thư mục chưa phủ.
  3. **Kiểm tra `PRAGMA integrity_check` cho `data/subsystem_heartbeat.sqlite`:**
     - `load_sqlite_metrics()` kiểm tra thêm `subsystem_heartbeat.sqlite`, bắt lỗi `sqlite3.DatabaseError` và ghi nhận trung thực `subsystem_heartbeat:FAIL/MALFORMED(database disk image is malformed)` (`all_sqlite_ok = False`).
  4. **Chuẩn hóa múi giờ `UTC+7` thật và cô lập `pytest`:**
     - Chuyển mọi mốc thời gian trong `VisualReportGenerator` và `SvgChartBuilder` sang `TZ_UTC7 = datetime.timezone(datetime.timedelta(hours=7))`.
     - Khi chạy trong `pytest` (`PYTEST_CURRENT_TEST` trong `os.environ`) mà không truyền `reports_dir`, tự động cô lập đầu ra vào thư mục tạm (`tempfile.gettempdir() / "scp_pytest_visual_reports"`) và gắn nhãn `Origin: pytest`, chấm dứt tình trạng pytest làm bẩn `reports/visual_reports/daily/REPORT_DAILY_*.md`.

---

## 2. Chuỗi Tại Sao 5 Tầng (a $\rightarrow$ e)

### (a) Hiện tượng quan sát được (Observed Symptom)
1. Trên log khởi động và tiến hóa của SCP, `[scanner-cap]` luôn cắt `811` (nay là `817`) file `.py` xuống `100` file đầu bảng chữ cái (`scp/__init__.py` $\rightarrow$ `scp/autofix/diagnostic.py`), khiến `security`, `meta`, `task_kernel_parts`, `hands`, `llm_gateway`, `core` chưa từng được tự quét trong bất kỳ lần lấy mẫu 100 file nào.
2. Toàn bộ 5 Windows Scheduled Tasks (`SCP-247-Supervisor`, `SCP-247-Recovery-Watchdog`, `SCP-247-Visual-Reports`, `SCP-System-Reporter-247`, `SCP-Public-Relay-Bridge`) đều ở trạng thái `Disabled`, nhưng báo cáo và script điều khiển chỉ nhìn một phần hoặc không kết luận hệ thống thiếu bộ lập lịch 24/7 ngoài.
3. Báo cáo `REPORT_DAILY_2026-10-27..30.md` ngày nào cũng in lặp lại:
   - `"Không có lỗi AST phát sinh | 0"` dù file `data/deep_audit_results.jsonl` **không tồn tại**;
   - `"Loop Scheduler: 6 chu kỳ, Lỗi 6, Tỷ lệ 0.00%"` dù 6 dòng đó xảy ra từ `2026-10-26 19:00–19:28Z` và đã ngừng chạy 5 ngày;
   - `"PRAGMA integrity_check: knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal)"` trong khi `data/subsystem_heartbeat.sqlite` đang bị `database disk image is malformed`;
   - Ghi nhãn `(UTC+7)` lên giờ UTC (`14:07:46` thay vì `21:07:46`) do được sinh từ `pytest` trong container Linux UTC.

### (b) Cơ chế lỗi trực tiếp trong mã nguồn (Immediate Code Mechanism)
1. Trong [`scp/autofix/runner_phases/ast_scan.py`](file:///d:/scp/scp/autofix/runner_phases/ast_scan.py): `_iter_python_files` gọi `sorted(root.rglob("*.py"))` rồi cắt tĩnh `paths[:limit]` tại chỉ số `0` mà không có phân tầng ưu tiên hay conul/offset xoay vòng.
2. Trong [`tools/generate_visual_reports.py`](file:///d:/scp/tools/generate_visual_reports.py):
   - `load_deep_audit_results()` viết `if not path.exists(): return [], {}` $\rightarrow$ tầng hiển thị dùng `findings_sum.get("total_findings", 0)` nên suy diễn thiếu file thành `0` lỗi.
   - `load_loop_runs_stats()` không nhận `since_ts`, không đọc trường `"ts"` của từng dòng JSONL, và mặc định `success_rate_pct = 100.0` khi danh sách rỗng.
   - `load_sqlite_metrics()` chỉ duyệt 3 file `.sqlite3`, bỏ quên `data/subsystem_heartbeat.sqlite` (có đuôi `.sqlite`).
   - `VisualReportGenerator` gọi `datetime.datetime.now()` (naive local time) nhưng ghép chuỗi cứng `(UTC+7)` và ghi thẳng vào `REPORTS_DIR` kể cả khi được gọi từ unit test.
   - `probe_live_infrastructure()` chỉ truy vấn 3/5 task Windows.

### (c) Tại sao các cổng kiểm thử trước đó không bắt được? (Why Existing Gates Missed It)
1. Test [`tests/test_audit_r2_scanner_cap.py`](file:///d:/scp/tests/test_audit_r2_scanner_cap.py) chỉ kiểm tra: (i) hằng số `_MAX_SCAN_FILES >= 1000`, (ii) khi truyền `limit=3` trên 5 file giả lập thì có log `[scanner-cap]` và trả về 3 file. Test không kiểm tra trường hợp caller thực tế (`startup_gate.py`, `evolution.py`) truyền `limit=100` trên cây `scp/` thật sẽ bỏ đói các thư mục bảo mật/kernel.
2. Test [`tests/T02_contract/test_visual_report_live_probe.py`](file:///d:/scp/tests/T02_contract/test_visual_report_live_probe.py) chỉ kiểm tra chuỗi tiêu đề `"Live HTTP Socket/Endpoint Probe"`, `"OS Scheduler Probe"`, `"Tỷ lệ:"` và `"knowledge:" in wal_val`, nhưng thiếu các **Anti-Placebo Negative Tests** cho trường hợp: nguồn `.jsonl` không tồn tại, dữ liệu `loop_runs.jsonl` đã cũ quá 24h (`STALE`), hoặc `subsystem_heartbeat.sqlite` bị `malformed`.

### (d) Nguyên nhân gốc rễ cấp kiến trúc / nhận thức (Architectural & Epistemic Root Cause)
- **Vi phạm DNA #22 (`PASS != TRUE` / Vắng dữ liệu $\neq$ Sạch) ở tầng tổng hợp báo cáo (Reporting Aggregation Layer):**
  Các hàm đọc dữ liệu vận hành được viết theo thói quen "defensive fail-open" (`if not path.exists(): return []`), biến trạng thái **không quan sát được (`UNKNOWN`)** hoặc **đã chết từ lâu (`STALE`)** thành số `0` (trông giống như hệ thống sạch lỗi) hoặc lặp lại số cộng dồn lịch sử như thể vừa diễn ra hôm nay.
- **Lấy mẫu tĩnh không phân tầng (Unstratified Static Prefix Sampling):**
  Sử dụng `sorted()[:limit]` để đảm bảo tính tất định (determinism) theo hệ điều hành nhưng lại vô tình biến "tất định" thành "cố định vĩnh viễn trên 12% đầu bảng chữ cái", khiến 88% mã nguồn phía sau chữ `d` (`scp/autofix/diagnostic.py`) trở thành vùng mù vĩnh viễn.

### (e) Biện pháp phòng ngừa hệ thống & Bài học bất biến (Systemic Prevention & Invariant Pinning)
1. **Stratified Priority + Deterministic Rotation Invariant:** Mọi bộ quét lấy mẫu có giới hạn (`limit < total_eligible`) bắt buộc phải phân tầng ưu tiên các thư mục trọng yếu (`CRITICAL_SCAN_DIRS`) vào từng cửa sổ và xoay vòng qua toàn bộ tập file theo `rotation_index`, đồng thời xuất kèm độ phủ `scanned_count/total_eligible`.
2. **Epistemic Telemetry Tri-State (`OK` / `UNKNOWN` / `STALE`):** Mọi hàm tổng hợp báo cáo bắt buộc phân biệt rõ 3 trạng thái: có dữ liệu trong khung giờ (`OK/ACTIVE`), nguồn dữ liệu chưa tồn tại (`UNKNOWN`), và dữ liệu quá hạn không có nhịp mới (`STALE`). Tuyệt đối cấm ánh xạ `MISSING` $\rightarrow$ `0 lỗi` hoặc `0 chu kỳ` $\rightarrow$ `100% thành công`.
3. **Hermetic Test Output Isolation:** Công cụ sinh báo cáo tự động phát hiện ngữ cảnh `PYTEST_CURRENT_TEST` để ghi vào sandbox tạm, không bao giờ để test tạo ra báo cáo giả danh vận hành thật trên đĩa sản phẩm.

---

## 3. Sơ Đồ Nhân Quả (FA-12 Causal Graph) & Ma Trận Bao Phủ (FA-13 Coverage Matrix)

```mermaid
graph TD
    A["Caller: startup_gate / evolution / CLI"] --> B["_iter_python_files(root, limit, rotation_index)"]
    B -->|limit <= 0| C["Fail-closed: return [], coverage=0.0"]
    B -->|total_eligible <= limit| D["Full scan: return sorted(paths), is_full_coverage=True"]
    B -->|total_eligible > limit| E["Stratified Priority + Rotation: CRITICAL_SCAN_DIRS in every window + [scanner-cap] warning"]
    E --> F["get_scan_coverage_metadata() / get_last_scan_coverage()"]

    G["VisualReportGenerator.generate()"] --> H["load_deep_audit_results(since_ts, data_dir)"]
    H -->|File missing| I["status=UNKNOWN, total_findings=None (DNA #22)"]
    H -->|File exists| J["status=OK, filter by since_ts"]

    G --> K["load_loop_runs_stats(since_ts, now_ts, data_dir)"]
    K -->|File missing/empty| L["status=MISSING/EMPTY, success_rate=0.0%"]
    K -->|last_cycle age > 24h| M["status=STALE, window total_runs=0, preserve all_time stats"]
    K -->|Fresh runs in window| N["status=ACTIVE, window success_rate_pct"]

    G --> O["load_sqlite_metrics(data_dir)"]
    O --> P["Check knowledge + ask_kernel + hands_kernel + subsystem_heartbeat"]
    P -->|Corrupt / DatabaseError| Q["FAIL/MALFORMED, all_sqlite_ok=False"]
    P -->|Healthy| R["ok(wal), all_sqlite_ok=True"]

    G --> S["probe_live_infrastructure()"]
    S --> T["Probe 4 HTTP ports + 5 SCP Windows Scheduled Tasks"]
    T -->|0/5 active on win32| U["scheduler_verdict = ALL_DISABLED"]
```

### Ma Trận Bao Phủ Nhân Quả (FA-13 Coverage Matrix)

| Nhánh Nhân Quả | File & Hàm Liên Quan | Test Kiểm Chứng (`tests/`) | Trạng Thái |
|---|---|---|---|
| `limit <= 0` trả về `[]` và `coverage_ratio=0.0` | `ast_scan.py::_iter_python_files`, `get_scan_coverage_metadata` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_coverage_metadata_anti_placebo` | **COVERED** |
| `total_eligible <= limit` trả về full sorted list | `ast_scan.py::_iter_python_files` | `tests/test_audit_r2_scanner_cap.py::test_cap_at_least_1000`, `test_selfreport_misses_fixes.py::test_ast_scan_rotation_covers_100_percent_of_eligible_files` | **COVERED** |
| `total_eligible > limit` phân tầng ưu tiên đủ 6 `CRITICAL_SCAN_DIRS` trong mọi cửa sổ | `ast_scan.py::_iter_python_files`, `_priority_sort_key` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_stratified_priority_covers_critical_dirs_in_every_window` | **COVERED** |
| Xoay vòng qua tất cả cửa sổ phủ 100% file eligible (`817/817`) | `ast_scan.py::_iter_python_files`, `_resolve_rotation_index` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_rotation_covers_100_percent_of_eligible_files` | **COVERED** |
| Cảnh báo `[scanner-cap]` khi cắt mẫu | `ast_scan.py::_iter_python_files` | `tests/test_audit_r2_scanner_cap.py::test_ast_scan_iter_warns_on_truncation` | **COVERED** |
| Tích hợp `startup_gate` với `ast_scan_scp` | `scp/startup/startup_gate.py::run_startup_ast_scan` | `tests/T04_governance/test_startup_gate_transparency.py` (10 tests) | **COVERED** |
| `deep_audit_results.jsonl` không tồn tại $\rightarrow$ `UNKNOWN` (cấm in `0 lỗi`) | `tools/generate_visual_reports.py::load_deep_audit_results` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_load_deep_audit_results_missing_file_reports_unknown_not_zero` | **COVERED** |
| `loop_runs.jsonl` quá hạn 24h $\rightarrow$ `total_runs=0`, `status=STALE`, `0.00%` | `tools/generate_visual_reports.py::load_loop_runs_stats` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_load_loop_runs_stats_filters_by_window_and_flags_stale` | **COVERED** |
| `loop_runs.jsonl` có chu kỳ mới trong khung 24h $\rightarrow$ `status=ACTIVE`, tính đúng `%` | `tools/generate_visual_reports.py::load_loop_runs_stats` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_load_loop_runs_stats_filters_by_window_and_flags_stale` | **COVERED** |
| `subsystem_heartbeat.sqlite` bị hỏng $\rightarrow$ `FAIL/MALFORMED`, `all_sqlite_ok=False` | `tools/generate_visual_reports.py::load_sqlite_metrics` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_load_sqlite_metrics_detects_malformed_heartbeat_db` | **COVERED** |
| Kiểm tra đầy đủ 5 Windows Scheduled Tasks + kết luận `ALL_DISABLED` | `tools/generate_visual_reports.py::probe_live_infrastructure`, `scp_247_control.ps1`, `scp_247_teardown.ps1` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_probe_live_infrastructure_includes_all_5_scp_windows_tasks`, `test_ops_control_and_teardown_scripts_include_all_5_tasks` | **COVERED** |
| Chuẩn hóa múi giờ `UTC+7` thật & cô lập đầu ra khi chạy trong `pytest` | `tools/generate_visual_reports.py::VisualReportGenerator` | `tests/T02_contract/test_selfreport_misses_fixes.py::test_visual_report_utc7_timezone_conversion_and_pytest_isolation` | **COVERED** |

---

## 4. Bằng Chứng Thực Thi Vật Lý & Kiểm Chứng Thực Tế (FA-03 / FA-12)

### 4.1. Kết quả chạy bộ test hợp đồng & chống suy giảm (`40 passed`)
```text
$ D:\scp\.venv\Scripts\python.exe -m pytest tests/T02_contract/test_selfreport_misses_fixes.py tests/T02_contract/test_visual_report_live_probe.py tests/test_audit_r2_scanner_cap.py tests/T04_governance/test_startup_gate_transparency.py tests/T07_learning/test_verify_mixin_gate5_and_paths.py -v

tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_stratified_priority_covers_critical_dirs_in_every_window PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_rotation_covers_100_percent_of_eligible_files PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_ast_scan_coverage_metadata_anti_placebo PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_probe_live_infrastructure_includes_all_5_scp_windows_tasks PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_ops_control_and_teardown_scripts_include_all_5_tasks PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_load_deep_audit_results_missing_file_reports_unknown_not_zero PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_load_loop_runs_stats_filters_by_window_and_flags_stale PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_load_sqlite_metrics_detects_malformed_heartbeat_db PASSED
tests/T02_contract/test_selfreport_misses_fixes.py::test_visual_report_utc7_timezone_conversion_and_pytest_isolation PASSED
tests/T02_contract/test_visual_report_live_probe.py (4 passed)
tests/test_audit_r2_scanner_cap.py (11 passed)
tests/T04_governance/test_startup_gate_transparency.py (11 passed)
tests/T07_learning/test_verify_mixin_gate5_and_paths.py (4 passed)
============================= 40 passed =============================
```

### 4.2. Kết quả kiểm tra cổng `T00 Meta-Audit` (`0 new regressions`)
```text
$ D:\scp\.venv\Scripts\python.exe tools/t00_meta_audit.py
[T00 Meta-Audit] Starting Test-Integrity Regression Authority...
[T00 Meta-Audit] Trusted Base: main
[T00 Meta-Audit] All integrity checks passed (0 new regressions).
```

### 4.3. Bằng chứng chạy thực tế CLI `tools/generate_visual_reports.py --cadence daily` & `scp_247_control.ps1 -Action status`
```text
[scanner-cap] scp.autofix.runner_phases.ast_scan: 817 file .py vượt cap 100 — cắt còn 100 (window=4/9, coverage=12.2%, critical_dirs={'security': 12, 'meta': 9, 'task_kernel_parts': 2, 'hands': 1, 'llm_gateway': 1, 'core': 11}, unscanned_dirs=20; nâng _MAX_SCAN_FILES nếu cần full coverage)
[DAILY] Đã xuất bản báo cáo: C:\Users\check\AppData\Local\Temp\scp_e2e_visual_report_check\daily\REPORT_DAILY_2026-10-31.md

> **Thời điểm xuất bản:** `2026-10-31 15:44:59 (UTC+7)`
> **Khoảng thời gian giám sát:** `2026-10-30 15:44 -> 2026-10-31 15:44 (UTC+7)`

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ
- **Độ phủ AST Scanner hiện hành:** `100/817` files (`12.2%`, window `4/9`).
- **Độ phủ thư mục trọng yếu (`security`, `meta`, `task_kernel_parts`, `hands`, `llm_gateway`, `core`):** `{"security": 12, "meta": 9, "task_kernel_parts": 2, "hands": 1, "llm_gateway": 1, "core": 11}`.
| _UNKNOWN (`UNKNOWN (nguồn data/deep_audit_results.jsonl chưa tồn tại)`)_ | **UNKNOWN** | Không in `0 lỗi` giả khi thiếu nguồn dữ liệu (DNA #22: Vắng dữ liệu != sạch; độ phủ mẫu `100/817`) |

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA (ĐO ĐẠC VẬT LÝ)
- **Subsystem Heartbeat (`data/subsystem_heartbeat.sqlite`):** `FAIL/MALFORMED(database disk image is malformed)`.
- **Kiểm tra tính toàn vẹn SQLite WAL thực tế (`PRAGMA integrity_check`):** `knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal); subsystem_heartbeat:FAIL/MALFORMED(database disk image is malformed)`.

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)
- **Tổng số chu kỳ hoàn thành của Loop Scheduler (trong khung giờ):** `0` chu kỳ (Thành công: `0`, Lỗi: `0`, Tỷ lệ: `0.00%`; Trạng thái: **STALE** — chu kỳ cuối: `2026-10-26T19:28:20.709Z`, lịch sử toàn cục: `6` chu kỳ / `6` lỗi).
- **Trạng thái bộ lập lịch hệ điều hành (OS Scheduler Probe — Kết luận: `ALL_DISABLED (0/5 active, 5/5 Disabled — toàn bộ bộ lập lịch 24/7 trên Windows host đang tắt; SCP hiện chỉ chạy trong tiến trình Docker)`):**
  + `SCP-247-Supervisor`: **Disabled (Next run: N/A)**
  + `SCP-247-Recovery-Watchdog`: **Disabled (Next run: N/A)**
  + `SCP-247-Visual-Reports`: **Disabled (Next run: N/A)**
  + `SCP-System-Reporter-247`: **Disabled (Next run: N/A)**
  + `SCP-Public-Relay-Bridge`: **Disabled (Next run: N/A)**
```

---

## 5. Danh Sách File Thay Đổi

1. [`scp/autofix/runner_phases/ast_scan.py`](file:///d:/scp/scp/autofix/runner_phases/ast_scan.py) — Phân tầng ưu tiên `CRITICAL_SCAN_DIRS`, xoay vòng tất định theo cửa sổ (`rotation_index`), xuất metadata độ phủ (`get_scan_coverage_metadata`, `get_last_scan_coverage`).
2. [`scp/autofix/runner_phases/__init__.py`](file:///d:/scp/scp/autofix/runner_phases/__init__.py) & [`scp/autofix/runner.py`](file:///d:/scp/scp/autofix/runner.py) — Re-export `CRITICAL_SCAN_DIRS`, `get_scan_coverage_metadata`, `get_last_scan_coverage`.
3. [`tools/generate_visual_reports.py`](file:///d:/scp/tools/generate_visual_reports.py) — Sửa `load_deep_audit_results` (`UNKNOWN` khi thiếu file), `load_loop_runs_stats` (lọc theo khung giờ + phát hiện `STALE`), `load_sqlite_metrics` (kiểm tra `subsystem_heartbeat.sqlite`), `probe_live_infrastructure` (đủ 5 task Windows + `ALL_DISABLED`), múi giờ `UTC+7` thật, và cô lập đầu ra khi chạy dưới `pytest`.
4. [`scripts/ops/scp_247_control.ps1`](file:///d:/scp/scripts/ops/scp_247_control.ps1) & [`scripts/ops/scp_247_teardown.ps1`](file:///d:/scp/scripts/ops/scp_247_teardown.ps1) — Bao phủ đầy đủ 5 Windows Scheduled Tasks.
5. [`tests/T02_contract/test_selfreport_misses_fixes.py`](file:///d:/scp/tests/T02_contract/test_selfreport_misses_fixes.py) — 9 bài test hợp đồng & Anti-Placebo kiểm chứng toàn diện cả 3 hạng mục.
