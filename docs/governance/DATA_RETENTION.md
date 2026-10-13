# Chính Sách Phân Cấp Dữ Liệu & Thời Hạn Lưu Giữ (Data Retention Policy)

> **Mã hiệu:** SCP-GOV-DR-01 · **Tiêu chuẩn:** GDPR Art. 5(1)(e) · Nghị định 13/2023/NĐ-CP (Điều 3, 9, 13, 17, 25) · NIST SP 800-53 MP-6  
> **Cấp độ áp dụng:** Toàn bộ dữ liệu xử lý, lưu trữ, nhật ký kiểm toán và bộ nhớ tạm của SCP Agent OS

---

## 1. Mục Đích & Nguyên Tắc Cốt Lõi
Chính sách này quy định quy tắc phân loại dữ liệu, cơ chế mã hóa lưu trữ và thời hạn lưu giữ nhật ký của hệ điều hành SCP Agent OS. Hệ thống tuân thủ nghiêm ngặt nguyên tắc **Tối thiểu hóa dữ liệu (Data Minimization)** và **Chủ quyền dữ liệu (Data Sovereignty)**: dữ liệu chỉ được lưu giữ trong phạm vi cần thiết cho mục đích thực thi nhiệm vụ và kiểm toán an ninh.

---

## 2. Phân Cấp 4 Tầng Dữ Liệu (4-Tier Data Classification)

| Tầng Dữ Liệu | Định Nghĩa & Ví Dụ | Cơ Chế Bảo Vệ & Mã Hóa | Quyền Truy Cập | Thời Hạn Lưu Giữ Tối Đa |
|---|---|---|---|---|
| **Tier 1: Công Khai (Public)** | Mã nguồn mã nguồn mở, tài liệu kỹ thuật công khai, OpenAPI specification. | Kiểm tra tính toàn vẹn bằng SHA256 checksums và chữ ký số Cosign. | Không giới hạn (Public Read). | Vĩnh viễn (theo vòng đời dự án). |
| **Tier 2: Nội Bộ (Internal)** | Cấu hình triển khai, số liệu đo lường Telemetry/Prometheus, log hệ thống không nhạy cảm. | Lưu trữ nội bộ, truyền thông qua TLS bắt buộc (`SCP_FORCE_HTTPS=1`). | Nhân sự dự án và Agent OS nội bộ. | 90 ngày kể từ ngày tạo. |
| **Tier 3: Bảo Mật (Confidential)** | Kế hoạch hành động chi tiết (Action Plans), Task Kernel event journal, SQLite/PG database. | Mã hóa bắt buộc bằng HMAC-SHA256 có khóa (`SCP_CAPABILITY_SECRET`) và AES-256. | Cấp độ L3–L4 (Workspace/Real), yêu cầu CapabilityToken. | 12 đến 24 tháng phục vụ kiểm toán tuân thủ. |
| **Tier 4: Tối Mật / Dữ Liệu Cá Nhân (Restricted / PII)** | Token xác thực, mật khẩu, khóa bí mật (`.env`, JWT keys), dữ liệu cá nhân theo NĐ 13/2023/NĐ-CP. | Mã hóa ở mức tĩnh (AES-256-GCM), cấm tuyệt đối xuất hiện trong prompt/log/git; Ephemeral generation trong RAM. | Cấp độ L5 (Privileged), yêu cầu Human Confirmation Store. | **Ngay khi kết thúc phiên** (Zero persistent PII); khóa bí mật xoay vòng tối đa 90 ngày. |

---

## 3. Quy Tắc Lưu Giữ & Quản Lý Nhật Ký Kiểm Toán (Audit Trail Retention)
1. **Nhật Ký Sự Kiện Task Kernel (`events` table):**
   - **Tính bất biến (Append-Only Invariant):** Nhật ký sự kiện được liên kết bằng chuỗi hàm băm HMAC-SHA256 (`event_hash` -> `prev_event_hash`) và neo giữ qua Journal Anchor (`control['journal_head_hash']`).
   - **Thời hạn lưu giữ:** Tối thiểu **12 tháng** và tối đa **24 tháng** phục vụ đối soát pháp lý và an ninh thông tin.
   - **Bảo vệ chống xóa:** Mọi hành vi xóa bảng `events` đều bị phát hiện fail-closed tức thì qua Journal Anchor.
2. **Không Gian Làm Việc Tạm Thời (Sandbox Evaluation Workspace):**
   - Thư mục kiểm thử tạm thời trong Sandbox Evaluator (`tempfile.mkdtemp`) phải được dọn dẹp sạch sẽ ngay khi hoàn tất bài đánh giá (`_cleanup(workspace, keep)`).
   - Chỉ lưu lại artifacts khi có cờ tường minh `keep=True` phục vụ điều tra sự cố (tối đa 48 giờ).
3. **Bộ Nhớ Tạm JWT & Danh Sách Thu Hồi (Revocation List):**
   - Bộ nhớ đệm danh sách đen JTI (`revoked_tokens`) được lưu trữ bền vững tại file dữ liệu runtime và tự động dọn dẹp các token đã quá hạn TTL (hết hạn sử dụng tự nhiên).

---

## 4. Tiêu Hủy Dữ Liệu An Toàn (Cryptographic Erasure & Sanitization)
- Khi hết thời hạn lưu giữ hoặc khi nhận được yêu cầu xóa dữ liệu hợp pháp (Right to be Forgotten theo GDPR / Điều 17 NĐ 13/2023/NĐ-CP):
  1. Dữ liệu nhạy cảm được ghi đè bằng giá trị ngẫu nhiên trước khi giải phóng bộ nhớ.
  2. Các tệp tin tạm thời trên đĩa cứng được xóa vĩnh viễn bằng lệnh OS an toàn.
  3. Bản ghi database liên quan được xóa và kích hoạt lệnh `VACUUM` để giải phóng hoàn toàn dung lượng vật lý.
