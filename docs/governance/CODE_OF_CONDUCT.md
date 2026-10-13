# Quy Tắc Ứng Xử & Liêm Chính Kỹ Thuật (Code of Conduct & Technical Integrity)

> **Mã hiệu:** SCP-GOV-COC-01 · **Phạm vi:** Người đóng góp, Nhà phát triển, AI Coding Agents & Subagents  
> **Nguyên tắc nền tảng:** 29 Nguyên Lý SCP DNA · Fail-Closed · Zero Placebo

---

## 1. Cam Kết Chung
Chúng tôi cam kết xây dựng một môi trường phát triển kỹ thuật minh bạch, an toàn và có trách nhiệm cao. Quy tắc này áp dụng cho cả con người (maintainers, contributors) và các Agent AI tự trị (Antigravity, Codex, Subagents) tham gia phân tích, chỉnh sửa mã nguồn hoặc kiểm toán hệ thống SCP Agent OS.

---

## 2. Các Chuẩn Mực Liêm Chính Kỹ Thuật Bắt Buộc (Mandatory Technical Invariants)

### 2.1 Thực Tế > Mô Hình (Reality > Model — DNA #1)
- Không được tin tưởng vào giả định, suy đoán hoặc câu trả lời tự khẳng định của mô hình AI.
- Mọi phán quyết an toàn hoặc kết quả hoàn thành nhiệm vụ bắt buộc phải được chứng minh bằng bằng chứng vật lý (terminal execution stdout/stderr, database records, test exit code 0).

### 2.2 Không Tạo Bằng Chứng Giả Lập & Không Placebo Assertion (FA-01 / FA-04 / FA-08)
- **Cấm tạo Placebo Assertion:** Tuyệt đối không thay đổi assertion theo hướng nới lỏng, thêm `any()` / `or` / fallback conditions để làm bài test chuyển màu xanh một cách giả tạo. Mọi test suite phải đi kèm negative test chứng minh cổng kiểm soát sẽ từ chối fail-closed khi gặp dữ liệu giả mạo.
- **Cấm giả lập VERIFIED:** Không được trả về `VERIFIED` từ stub, mock hoặc hardcoded return khi chưa có quan sát thực tế (Reality observation).
- **Cấm bịa đặt log:** Không tự ý dùng tool tạo các file log giả mạo để chứng minh kết quả.

### 2.3 Không Xóa, Bỏ Qua Hoặc Xfail Bài Kiểm Thử (FA-02)
- Không được phép xóa bài test, thêm cờ `@pytest.mark.skip`, `@pytest.mark.xfail` hoặc comment out các đoạn kiểm tra để vượt qua CI.
- Khi một bài kiểm thử bị đỏ (RED), phải phân loại trung thực:
  - `HARNESS_BROKEN`: Sửa harness nhưng phải chứng minh độ nghiêm ngặt được giữ nguyên hoặc tăng lên.
  - `PRODUCT_FAIL`: Sửa mã sản phẩm tại đúng điểm phát sinh lỗi.
  - `PRODUCT_BLOCKED`: Giữ nguyên trạng thái thất bại, không manufacture kết quả xanh.

### 2.4 An Toàn Chuỗi Cung Ứng Tuyệt Đối (Supply Chain Integrity)
- Mọi gói phụ thuộc Python và Node.js phải được khóa cứng với số phiên bản chính xác và mã băm mật mã (Cryptographic SHA256 hashes).
- Docker base images bắt buộc phải được pin bằng canonical multi-arch SHA256 digest trên Docker Hub (DEP-04 / DEP-06).
- 100% GitHub Actions phải được pin bằng 40-character commit SHA (OPS-05).
- Tích hợp quét lỗ hổng Trivy với cờ `exit-code: 1` fail-closed và chứng thực nguồn gốc SLSA Build Level 3 provenance (SUP-01 / SEC-19).

---

## 3. Quy Trình Báo Cáo Lỗ Hổng Có Trách Nhiệm (Responsible Disclosure)
1. Khi phát hiện lỗ hổng bảo mật trong hệ thống, nhà nghiên cứu hoặc agent phải lập tức kích hoạt quy trình báo cáo ngoại vi bắt buộc theo FA-11 (Mandatory Peripheral Audit).
2. Tạo báo cáo độc lập kèm theo Sơ đồ Nhân quả (Mermaid Causal Graph) và script tấn công mô phỏng độc lập (The Exploit Mandate — FA-09) chứng minh lỗ hổng trên terminal thực tế trước khi đề xuất bản vá.
3. Tuyệt đối không khai thác lỗ hổng vượt quá phạm vi chứng minh kỹ thuật cần thiết.
