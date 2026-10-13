# Quy Trình Ứng Phó Sự Cố An Ninh Thông Tin (Incident Response Plan)

> **Mã hiệu:** SCP-GOV-IR-01 · **Tiêu chuẩn:** NIST SP 800-61 Rev 2 · ISO/IEC 27001:2022 A.5.24–A.5.28  
> **Cấp độ áp dụng:** Toàn bộ hệ điều hành SCP Agent OS, Task Kernel, và Hệ thống Điều phối Ngoại vi

---

## 1. Mục Đích & Phạm Vi
Tài liệu này xác lập quy trình chuẩn 5 giai đoạn ứng phó sự cố an ninh thông tin cho hệ điều hành SCP Agent OS nhằm phát hiện sớm, ngăn chặn kịp thời, khắc phục triệt để và phục hồi an toàn trước các cuộc tấn công khai thác mô hình AI, xâm nhập chuỗi cung ứng, trốn thoát sandbox, hoặc vi phạm ranh giới hệ thống.

---

## 2. Quy Trình 5 Bước Ứng Phó Sự Cố (NIST SP 800-61 Rev 2)

```text
[1. Detection & Identification]
         │
         ▼
  [2. Containment]  ──► (3-Layer Kill Switch + Dead-Proxy + Egress Deny)
         │
         ▼
 [3. Eradication]   ──► (Token Revocation + JTI Invalidation + Clean State)
         │
         ▼
   [4. Recovery]    ──► (TaskKernel Orphan Reconcile + WAL Rollback + Shadow Restore)
         │
         ▼
[5. Lessons Learned]──► (Causal Graph + STATUS-LEDGER + POA&M + CI Tripwire)
```

### Bước 1: Phát Hiện & Nhận Diện (Detection & Identification)
- **Cơ chế giám sát chủ động:**
  - **T00 Integrity Gates & Meta-Audit:** Tự động rà soát vi phạm ranh giới code, skips, và nợ kỹ thuật trên mỗi commit và PR.
  - **Egress Violation Detector (`url_safety.py`):** Phát hiện mọi nỗ lực kết nối ngoại vi trái phép dưới `SCP_EGRESS_MODE=deny` hoặc ngoài `allowlist`.
  - **JWT Guard & Replay Detection (`jwt_guard.py`):** Giám sát các token mang chữ ký sai, epoch không hợp lệ hoặc JTI đã bị thu hồi.
  - **Tamper-Evident Journal Verification (`lease_manager.py`):** Tự động đối chiếu chuỗi băm HMAC-SHA256 và Journal Anchor; phát hiện ngay lập tức hành vi xóa bảng `events`.
- **Phân loại cấp độ nghiêm trọng (Severity Tiers):**
  - **P0 (Critical):** Thoát khỏi Sandbox Evaluator, rò rỉ secret/credential, hoặc can thiệp trực tiếp vào TaskKernel database.
  - **P1 (High):** Lỗi xác thực JWT/WebSocket, bypass cờ an toàn, hoặc phát hiện package giả mạo trong chuỗi cung ứng.
  - **P2 (Medium):** Tần suất vi phạm rate limit tăng đột biến, crash lặp lại trong shadow execution.
  - **P3 (Low):** Cảnh báo drift cấu hình hoặc log định dạng không chuẩn.

### Bước 2: Cô Lập Sự Cố (Containment)
Hệ thống kích hoạt cơ chế cô lập tức thì thông qua kiến trúc chốt chặn đa tầng:
1. **Kill Switch 3 Lớp Độc Lập:**
   - **Lớp 1 (Task-Level Kill):** Thu hồi lease của worker đang thực thi, chuyển task sang `CANCELLED`.
   - **Lớp 2 (Cell-Level Kill):** Tạm dừng toàn bộ hàng đợi trong phân vùng dịch vụ gặp sự cố.
   - **Lớp 3 (Global Mission Kill Switch):** Kích hoạt cờ `global_kill = 1` tại bảng `control` trong TaskKernel SQLite, vô hiệu hóa toàn bộ việc claim task mới và fencing toàn bộ worker lease ngay cả khi tiến trình khởi động lại (`O3-KIL` / `REC-03`).
2. **Khóa Mạng Tuyệt Đối (Dead-Proxy & Network Quarantine):**
   - Chuyển `SCP_EGRESS_MODE=deny`, trỏ toàn bộ proxy sang dead-proxy `127.0.0.1:0`.
3. **Cô Lập Tiến Trình Subprocess:**
   - Hủy bỏ các subprocess không an toàn thông qua `taskkill` được quản trị bởi Capability Token (`safe_process.py`).

### Bước 3: Loại Bỏ Gốc Rễ (Eradication)
- **Vô hiệu hóa Khóa & Token Xác Thực:**
  - Lập tức đưa toàn bộ JWT ID (`jti`) nghi vấn vào danh sách thu hồi vĩnh viễn qua `revoke_token(jti)` (`SCP-06`).
  - Xoay vòng (rotate) `SCP_CAPABILITY_SECRET`, `SCP_AUTH_TOKEN_SECRET`, và `SCP_JWT_SECRET`.
- **Dọn dẹp Vùng Nhớ Tạm & Shadow Workspace:**
  - Cách ly thư mục vá lỗi hỏng vào `rolled_back/<tx_id>` với lý do `CORRUPT_MANIFEST_CRASH_RECOVERY`.
  - Thu hồi toàn bộ file tạm và lock file WAL tồn đọng.

### Bước 4: Phục Hồi Hệ Thống (Recovery)
Thực thi quy trình phục hồi đạt chuẩn Cấp độ D (Recovery Evidence Gate):
1. **Orphan Reconciliation:** TaskKernel tự động phát hiện và thu hồi các task mồ côi có worker lease hết hạn (`auto_reconcile_orphans`).
2. **Crash Consistency & WAL Recovery:** Mở lại SQLite/PostgreSQL storage, khôi phục khóa ghi sạch sẽ (`test_disaster_recovery_storage_wal.py`).
3. **Shadow Snapshot Checksum Restore:** Hoàn nguyên trạng thái tệp tin mã nguồn về đúng mã băm SHA256 trước khi áp dụng bản vá bị lỗi.
4. **Giải phóng Kill Switch:** Sau khi hệ thống được xác nhận an toàn, kích hoạt `_release_kernel_global_kill()` đưa hệ thống trở lại trạng thái vận hành bình thường.

### Bước 5: Tổng Kết & Rút Kinh Nghiệm (Post-Incident Lessons Learned)
- **Vẽ Sơ đồ Nhân quả Toàn phần (Mermaid Causal Graph):** Làm rõ chuỗi: *Trigger -> Local Failure -> System Impact* theo quy chuẩn FA-11 / FA-12.
- **Cập Nhật Sổ Cái Trạng Thái (`STATUS-LEDGER.md`):** Ghi nhận finding mới và gắn kèm Closure Test Binding (NodeID) bắt buộc.
- **Bổ Sung Test Tripwire Chống Tái Phát:** Viết negative unit test / integration test chứng minh cổng kiểm soát bắt được đòn tấn công trước khi đóng sự cố.
- **Cập nhật Bảng Theo Dõi POA&M:** Ghi nhận tiến độ khắc phục dài hạn trong `docs/COMPLIANCE_MAPPING.md`.
