# Sổ Cái Phân Loại Trạng Thái & Quản Trị Rủi Ro (Status Ledger & Risk Governance)

> **Cơ chế quản trị (Governance Protocol — GA.md § A1 & DNA #22):**  
> Sổ cái này ghi nhận chính thức các quyết định đóng finding kỹ thuật theo hình thức `ACCEPTED_RISK` hoặc `SCOPE_FREEZE` khi rào cản phụ thuộc vào thẩm quyền bên ngoài (như cấu hình tổ chức GitHub cấp quản trị viên máy chủ) thay vì mã nguồn repository cục bộ.

---

## 1. Bảng Tổng Hợp Quyết Định Quản Trị (Formal Triage Ledger)

| Finding ID | Phân Loại & Tiêu Đề | Trạng Thái Đóng | Nhãn Phân Định Trung Thực | Lý Do Kỹ Thuật & Căn Cứ Rà Soát |
|---|---|:---:|---|---|
| **TST-02** | Required checks enforcement & GitHub Server-Side Ruleset | **`CLOSED (ACCEPTED_RISK)`** | `SCP Pre-RC Verification (Non-Authoritative)`<br>`SCP CI Baseline (non-blocking)` | Trải qua 11 vòng kiểm toán liên tiếp (R0 đến R10), việc bắt buộc các check CI thông qua GitHub API/Ruleset đòi hỏi quyền quản trị cấp máy chủ GitHub (Organization/Repository Admin Settings) nằm ngoài phạm vi thực thi của workspace mã nguồn cục bộ. Hệ thống hiện đã được bảo vệ chặt chẽ tại **Tier 1** (Git pre-commit hook cưỡng chế `t00_meta_audit.py`) và **Tier 3** (`.github/workflows/scp_guardrails.yml`). Quyết định đóng chính thức dưới diện `accepted_risk` với nhãn trung thực phi-thẩm quyền cho đến khi duy trì viên có thẩm quyền cấu hình Ruleset phía máy chủ. |

---

## 2. Chi Tiết Kỹ Thuật Cho TST-02 (TST-02 Triage Specification)

### 2.1 Bối Cảnh & Vấn Đề
- **Mã phát hiện:** TST-02 (Audit Round 1 - Round 10).
- **Vấn đề ban đầu:** Kiểm toán yêu cầu xác thực các "required checks" của GitHub branch protection qua API hoặc test contract T11 để chặn bypass merge trên remote.
- **Thực tế kỹ thuật:**
  1. Trong môi trường runtime/worker cục bộ không mang quyền Admin Token của GitHub Organization, API cấu hình branch protection ruleset trả về 403 Forbidden hoặc không thể tự áp đặt từ script client.
  2. Việc cố gắng assert giả lập tên check CI trên client (R8-N01) là hành vi bị cấm tuyệt đối theo FA-01 (Cấm phong thẩm quyền hình thức).

### 2.2 Biện Pháp Kiểm Soát Thay Thế Đã Kiểm Chứng (Compensating Controls)
- **Pre-commit Hook Tier 1 (`tools/t00_meta_audit.py`):** Cưỡng chế chạy cục bộ trước mọi commit, chặn đứng việc xóa test, skip test, hoặc hạ chuẩn assertions (FA-01, FA-02).
- **GitHub Actions CI Tier 3 (`.github/workflows/scp_guardrails.yml`):** Chạy độc lập trên mỗi pull request và push lên `main` để kiểm tra toàn vẹn test suite.
- **Secret & Safety Scanners (`tools/scan_secrets.py`, `ruff`):** Chặn cứng lộ lọt bí mật và ngoại lệ ngầm.

### 2.3 Nhãn Định Danh Trung Thực (Honest Labeling Contract)
- **`SCP Pre-RC Verification (Non-Authoritative)`**: Thừa nhận rõ ràng rằng kiểm chứng phía repo cục bộ là tiền-phát hành, không tự thay thế phán quyết máy chủ GitHub.
- **`SCP CI Baseline (non-blocking)`**: Các bài test T11 liên quan chỉ đóng vai trò baseline giám sát và ghi nhận drift, không được giả lập thẩm quyền server-side.

---

## 3. Quy Trình Mở Lại (Reopening Criteria)
Finding TST-02 chỉ được xem xét mở lại khi:
1. Maintainer cấu hình chính thức GitHub Organization Ruleset với token quản trị thích hợp; HOẶC
2. Có cơ chế chứng thực máy chủ độc lập cung cấp chữ ký xác thực mà không cần hardcode token đặc quyền.
