# ADR-0001: TaskKernel Modular Decomposition & Coordinator Architecture

- **Status:** Accepted
- **Date:** 2026-10-10
- **Authors:** SCP Architecture Team
- **Deciders:** SCP Core Governance
- **Finding Reference:** ARC-01 (TaskKernel god-file decomposition)

---

## 1. Bối Cảnh (Context)

Ban đầu, `TaskKernel` (`scp/task_kernel_parts/taskkernel.py`) là một god-file lớn nhất trong hệ thống SCP với **2.269 dòng mã (LOC)**, gánh vác đồng thời 7 nhóm trách nhiệm khác nhau:
1. Quản lý trạng thái kết nối và giao dịch lưu trữ SQLite/Postgres.
2. Kiểm tra khóa idempotent (`IdempotencyKey`).
3. Lưu và nạp điểm kiểm tra (`Checkpoint`).
4. Quản lý hạn ngạch và lease của tác vụ (`Lease & Heartbeat`).
5. Ma trận chuyển đổi 17 trạng thái nguyên tử (`StateMachine`).
6. Hòa giải sự cố mồ côi và phục hồi sau sự cố (`Recovery & Orphan Reconciliation`).
7. Điều phối phê duyệt và cam kết tác vụ (`Commit & Approval`).

Phân tích AST coupling ở Round 8 cho thấy: khớp nối nghiệp vụ-lưu trữ chiếm 93.3% (mô hình sao quanh storage), trong khi khớp nối giữa các nhánh nghiệp vụ chỉ chiếm 6.7% (17 lời gọi liên nhóm). Điều này chứng minh TaskKernel hoàn toàn có thể phân rã thành các engine độc lập mà không phá vỡ logic nghiệp vụ.

---

## 2. Quá Trình Tách Mô-Đun Hóa Thực Tế (Phased Modular Extraction)

Qua 6 bước tái cấu trúc tuần tự bảo toàn 100% tương thích ngược:
1. **Bước 1 (GA B36):** Tách `KernelStorage` sang `scp/kernel_storage.py`, tiêm qua DI constructor `TaskKernel(storage=...)`.
2. **Bước 2 & 3 (GA B37):** Trích xuất `IdempotencyEngine` sang `scp/task_kernel_parts/idempotency.py` và `CheckpointEngine` sang `scp/task_kernel_parts/checkpoint.py`.
3. **Bước 4 (GA B38):** Trích xuất `RecoveryEngine` (toàn bộ logic phục hồi `recover_on_boot`, `reconcile_unknown`, `auto_reconcile_orphans`) sang `scp/task_kernel_parts/recovery.py`.
4. **Bước 5 (GA B39):** Trích xuất `StateMachineEngine` (toàn bộ ma trận 17 trạng thái, phương thức chuyển đổi trạng thái nguyên tử `transition()`) sang `scp/task_kernel_parts/state_machine.py`.
5. **Bước 6 (GA B44):** Trích xuất `CommitApprovalEngine` (`ApprovalManager`) sang `scp/task_kernel_parts/commit_approval.py` (toàn bộ logic phê duyệt `commit_approval`, cam kết hoàn tất với `VerifierReceipt` `commit_completed`, định tuyến thất bại `commit_failed`, hủy/kill tác vụ và quản lý lease).

Kết quả: Kích thước file `scp/task_kernel_parts/taskkernel.py` đã giảm từ **2.269 LOC xuống 971 LOC (885 non-blank LOC)** (net −1.298 LOC so với ban đầu).

---

## 3. Quyết Định Kiến Trúc (Architecture Decision)

1. **Khóa Mô Hình Coordinator (Coordinator Pattern):**
   Phần mã còn lại của `TaskKernel` (971 LOC) được chính thức xác định là một **Coordinator / Orchestrator Module**. Vai trò duy nhất của nó là làm điểm truy cập công khai thống nhất (Facade / Coordinator), điều phối luồng thực thi giữa 6 submodule thông qua Dependency Injection và ủy quyền (delegation).

2. **Khóa Trần Kích Thước Code (LOC Hard Ceiling):**
   Thiết lập trần kích thước tối đa cho `scp/task_kernel_parts/taskkernel.py` là **< 1.000 LOC**. 
   Mọi tính năng mới liên quan đến:
   - Lưu trữ dữ liệu: Bổ sung vào `KernelStorage`.
   - Idempotency: Bổ sung vào `IdempotencyEngine`.
   - Checkpoint: Bổ sung vào `CheckpointEngine`.
   - Phục hồi / Reconciliation: Bổ sung vào `RecoveryEngine`.
   - Chuyển đổi trạng thái: Bổ sung vào `StateMachineEngine`.
   - Phê duyệt & Cam kết / Lease lifecycle: Bổ sung vào `CommitApprovalEngine`.
   Tuyệt đối cấm thêm logic nghiệp vụ chi tiết trực tiếp vào `taskkernel.py` làm vi phạm trần 1.000 LOC.

3. **Bảo Toàn Khế Ước Giao Tiếp (Contract Preservation):**
   Toàn bộ 56 public methods của `TaskKernel` tiếp tục được giữ nguyên chữ ký và hành vi để bảo toàn 100% tương thích với API Server, Ask Kernel Adapter và 370+ test cases trong `tests/T04_kernel/`.

---

## 4. Hệ Quả & Trạng Thái Finding (Consequences & Status)

- **Trạng thái finding ARC-01:** Đánh dấu chính thức là **`CLOSED (REMEDIATED_CEILING_REACHED)`** trong `STATUS-LEDGER.md`.
- **Độ bao phủ kiểm thử (Test Coverage):** Toàn bộ module trong `scp/task_kernel_parts/` duy trì trên ngưỡng sàn kiểm soát TST-03 (`scp/task_kernel_parts` đạt 75.1% >= 75.0% threshold).

