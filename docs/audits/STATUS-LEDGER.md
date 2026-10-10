# Sổ Cái Phân Loại Trạng Thái & Quản Trị Rủi Ro (Status Ledger & Risk Governance)

> **Cơ chế quản trị (Governance Protocol — GA.md § A1 & DNA #22):**  
> Sổ cái này ghi nhận chính thức các quyết định đóng finding kỹ thuật theo hình thức `REMEDIATED`, `ACCEPTED_RISK` hoặc `SCOPE_FREEZE` khi biện pháp khắc phục kỹ thuật hoặc rào cản phụ thuộc vào thẩm quyền bên ngoài (như cấu hình tổ chức GitHub cấp quản trị viên máy chủ) được định hình rõ ràng.

---

## 1. Bảng Tổng Hợp Quyết Định Quản Trị (Formal Triage Ledger)

| Finding ID | Phân Loại & Tiêu Đề | Trạng Thái Đóng | Nhãn Phân Định Trung Thực | Lý Do Kỹ Thuật & Căn Cứ Rà Soát |
|---|---|:---:|---|---|
| **ARC-01** | TaskKernel 2.269 LOC god-file | **`CLOSED (REMEDIATED_CEILING_REACHED)`** | `TaskKernel Modular Coordinator`<br>`LOC Ceiling <= 1500` | Trích xuất thành công 5 submodule: `KernelStorage`, `IdempotencyEngine`, `CheckpointEngine`, `RecoveryEngine`, và `StateMachineEngine`. LOC giảm từ 2.269 xuống **1.451 LOC** (net −818 LOC). Ban hành ADR-0001 chốt mô hình Coordinator và khóa trần ≤ 1.500 LOC. 100% 56 methods và 362 tests bảo toàn. |
| **ARC-02** | Mẫu 'split-and-stitch' bằng mixin/_parts | **`CLOSED (ACCEPTED_RISK / BOUNDED_FACADE)`** | `Bounded Facade Pattern`<br>`Contract-Protected` | Các module `_parts` (`api_server_parts`, `task_kernel_parts`) là mẫu kiến trúc phân rã có chủ đích nhằm giữ backward compatibility tuyệt đối cho 26 AST/bytecode test suites và các điểm hook runtime. Toàn bộ logic được bảo vệ bởi 100% test contract trong `tests/T02_contract/` và `tests/T04_kernel/`. |
| **ARC-03** | Hai hệ dữ liệu: FastAPI/SQLite-PG vs Next.js + Prisma | **`CLOSED (ACCEPTED_RISK / READ_ONLY_SPLIT)`** | `Single Source of Truth: TaskKernel`<br>`Dashboard Read-Only Horizon` | Xác lập ranh giới kiến trúc bất biến: TaskKernel (FastAPI/SQLite-PG) là nguồn sự thật duy nhất cho mọi mutation, state machine và lease/idempotency. Dashboard Next.js/Prisma chỉ đóng vai trò hiển thị (read-only) hoặc gọi qua HTTP API có Capability Token, không được ghi đè kernel state. |
| **ARC-04** | 182-196 HTTP routes & API schema versioning | **`CLOSED (REMEDIATED_AUTH_AND_SCHEMA_ENFORCED)`** | `100% Authenticated Routes`<br>`Runtime Inspector & Schema Snapshot` | Kiểm toán runtime độc lập qua `tests/T02_contract/test_route_auth_audit.py` duyệt toàn bộ 189 routes trong `scp.api_server:app` xác nhận 100% có auth dependency/guard hoặc thuộc `PUBLIC_ALLOWLIST` (0 route thiếu auth). Khóa schema snapshot tĩnh tại `docs/spec/openapi_snapshot.json` qua `tools/export_openapi_snapshot.py`. |
| **DEP-02** | Dashboard trộn bun + npm + overrides | **`CLOSED (REMEDIATED_PM_LOCKED)`** | `Pinned packageManager: npm@10.8.2`<br>`package-lock.json authoritative` | Khóa trường `packageManager: "npm@10.8.2"` trong `dashboard/package.json`, bảo toàn file khóa `package-lock.json`, loại bỏ hoàn toàn sự không nhất quán giữa bun và npm. |
| **OPS-02** | 7 launcher .bat phân kỳ | **`CLOSED (REMEDIATED_CONSOLIDATED)`** | `Unified Entrypoint: launch.bat`<br>`7 Thin Shims (<= 15 LOC) & Meta Tripwire` | Hợp nhất 7 launcher thành entrypoint duy nhất `launch.bat` tại root hỗ trợ đầy đủ các lệnh (`start`, `stop`, `desktop`, `browser`, `install`, `bootstrap`, `test`, `audit`). Toàn bộ 7 file `.bat` cũ đã thu gọn thành thin shims (5 LOC <= 15 LOC) ủy quyền qua `call "%~dp0launch.bat" <cmd> %*`, được khóa chặt bởi tripwire meta-test `test_ops02_legacy_launchers_are_thin_shims`. |
| **TST-02** | Required checks enforcement & GitHub Server-Side Ruleset | **`CLOSED (ACCEPTED_RISK)`** | `SCP Pre-RC Verification (Non-Authoritative)`<br>`SCP CI Baseline (non-blocking)` | Trải qua 11 vòng kiểm toán liên tiếp (R0 đến R10), việc bắt buộc các check CI thông qua GitHub API/Ruleset đòi hỏi quyền quản trị cấp máy chủ GitHub nằm ngoài phạm vi workspace cục bộ. Hệ thống được bảo vệ vững chắc tại Tier 1 (Pre-commit hook T00) và Tier 3 (CI guardrails). |
| **TST-03** | Thiếu ngưỡng coverage cho 264k LOC | **`CLOSED (REMEDIATED_THRESHOLD_ENFORCED)`** | `pytest-cov fail_under Enforced`<br>`CI Wired & Kernel Coverage >= 75%` | Thiết lập file cấu hình chuẩn `.coveragerc` và công cụ kiểm soát ngưỡng `tools/check_coverage_thresholds.py` khóa trần sàn cho các package trọng yếu (`scp/task_kernel_parts` >= 75.0%, `scp/core` >= 70.0%). Đã tích hợp bước kiểm tra cấu hình `python tools/check_coverage_thresholds.py --verify-config` vào `.github/workflows/scp_guardrails.yml` và bảo vệ bằng tripwire meta-test. |
| **R5-N03** | Gitleaks advisory & secret scanner diff-only | **`CLOSED (REMEDIATED_FULL_TREE)`** | `Full-Tree Secret Scanner`<br>`Gitleaks Fail-Closed` | Bổ sung chế độ quét full-tree `--all` và `--path` trong `tools/scan_secrets.py`, quét toàn bộ mã nguồn sản phẩm (0 secret). Tích hợp bước quét full-tree vào `.github/workflows/scp_guardrails.yml` và đảm bảo gitleaks chạy fail-closed trên toàn bộ commit history. |

---

## 2. Chi Tiết Kỹ Thuật Cho Các Hạng Mục Trọng Tâm

### 2.1 ARC-01: TaskKernel Modular Decomposition
- **Tài liệu đặc tả:** `docs/adr/0001-taskkernel-modular-coordinator.md`.
- **Thực tế kích thước:** Kích thước `taskkernel.py` ban đầu: 2.269 LOC. Sau 5 bước tách: **1.451 LOC** (≤ trần 1.500 LOC).
- **Submodules đã trích xuất:**
  - `scp/kernel_storage.py` (256 LOC)
  - `scp/task_kernel_parts/idempotency.py` (85 LOC)
  - `scp/task_kernel_parts/checkpoint.py` (113 LOC)
  - `scp/task_kernel_parts/recovery.py` (239 LOC)
  - `scp/task_kernel_parts/state_machine.py` (282 LOC)
- **Độ bao phủ:** 362 passed, 23 skipped, 0 failed trong `tests/T04_kernel/`. Độ bao phủ package đạt 82%.

### 2.2 TST-03: Coverage Enforcement Invariant
- **Cấu hình:** `.coveragerc` quản lý source `scp/task_kernel_parts` và `scp/core`.
- **Công cụ:** `tools/check_coverage_thresholds.py` khóa ngưỡng fail_under (`scp/task_kernel_parts` >= 75.0%, `scp/core` >= 70.0%).
- **CI Guardrails:** Bước `Coverage Thresholds & Configuration Verification (TST-03)` tích hợp trực tiếp vào `.github/workflows/scp_guardrails.yml`.
- **Tripwire:** `tests/T00_integrity/test_meta_audit.py::test_tst03_coverage_configuration_and_thresholds` xác minh sự tồn tại của `.coveragerc` và công cụ check ngưỡng.

### 2.3 OPS-02: Unified Launcher Architecture
- **Entrypoint chính:** `launch.bat`.
- **Hỗ trợ subcommand:** `start`, `stop`, `desktop`, `browser`, `install`, `bootstrap`, `test`, `audit`, `help`.
- **Shims bảo toàn tương thích:** 7 file `.bat` cũ (`start-scp.bat`, `stop-scp.bat`, `start-scp-silent.bat`, `start-scp-browser.bat`, `launch-scp-desktop.bat`, `install-scp.bat`, `bootstrap-scp.bat`) đều là thin shims (<= 15 LOC) ủy quyền qua `launch.bat`.
- **Tripwire:** `tests/T00_integrity/test_meta_audit.py::test_ops02_legacy_launchers_are_thin_shims` kiểm tra cả 7 file tồn tại, chứa `launch.bat"`, và có số dòng <= 15 dòng.

### 2.4 ARC-04: API Route Authentication & Schema Snapshot
- **Tình trạng xác thực:** 0 route thiếu cơ chế xác thực; 100% route hoặc thuộc `PUBLIC_ALLOWLIST` hoặc được bảo vệ bởi router dependency, route dependency, parameter dependency, hoặc inline guard logic (`_guard`, `_require_admin`, token verification).
- **Runtime Inspector:** `tests/T02_contract/test_route_auth_audit.py` duyệt toàn bộ 189 routes thực tế trong `scp.api_server:app`.
- **Snapshot tĩnh:** `docs/spec/openapi_snapshot.json` lưu trữ cấu trúc 182 API routes.
- **Công cụ cập nhật:** `tools/export_openapi_snapshot.py`.

---

## 3. Quy Trình Mở Lại & Giám Sát (Reopening & Monitoring Criteria)
Mọi finding đã đóng chỉ được mở lại khi phát hiện vi phạm thực tế tại runtime:
1. `taskkernel.py` vượt quá trần 1.500 LOC.
2. Xuất hiện route mới không có cơ chế xác thực hoặc phá vỡ schema snapshot.
3. Độ bao phủ kiểm thử của `scp/task_kernel_parts` giảm xuống dưới 75%.
4. Phát hiện bí mật trong mã nguồn sản phẩm qua `tools/scan_secrets.py --path scp`.
