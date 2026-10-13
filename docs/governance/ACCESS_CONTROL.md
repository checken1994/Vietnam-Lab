# Chính Sách Kiểm Soát Truy Cập & Phân Quyền (Access Control Policy)

> **Mã hiệu:** SCP-GOV-AC-01 · **Tiêu chuẩn:** NIST SP 800-53 Rev 5 (AC-2, AC-3, AC-6) · ISO/IEC 27001:2022 A.5.15–A.5.18  
> **Cấp độ áp dụng:** Toàn bộ API Endpoints, WebSocket, Task Kernel, và Công Cụ Điều Khiển Máy Tính (PC Control)

---

## 1. Mục Đích & Nguyên Tắc Đặc Quyền Tối Thiểu (Least Privilege)
Chính sách này thiết lập ma trận phân quyền 6 cấp và quy định cơ chế xác thực, ủy quyền độc lập cho mọi tác vụ trong hệ điều hành SCP Agent OS. Mọi tương tác của Agent, Subagent hoặc người vận hành đều phải tuân thủ nguyên tắc **Fail-Closed by Default** và **Không tự cấp quyền (FA-05 / Zero Self-Granted Authority)**: Caller phải xuất trình token hợp lệ được cấp bởi cơ quan thẩm quyền riêng biệt (`CapabilityAuthority`).

---

## 2. Ma Trận Phân Quyền 6 Cấp (6-Tier Capability Matrix)

| Cấp Độ (Level) | Danh Xưng | Mô Tả & Phạm Vi Hành Động Cho Phép | Cơ Chế Xác Thực & Điều Kiện Kích Hoạt |
|---|---|---|---|
| **L0** | `READ_ONLY` | Chỉ đọc thông tin tĩnh, truy vấn tài liệu, kiểm toán cú pháp AST, xem log không nhạy cảm. Không làm biến đổi trạng thái hệ thống. | Yêu cầu xác thực cơ bản hoặc API public allowlist đã được kiểm toán (`PUBLIC_ALLOWLIST`). |
| **L1** | `DRY_RUN` | Lập kế hoạch hành động (Plan generation), dự phóng biến đổi mã nguồn trong RAM, mô phỏng kết quả. Không ghi đĩa hoặc tạo tiến trình thực tế. | Yêu cầu JWT token hợp lệ của phiên làm việc. |
| **L2** | `SANDBOX` | Thực thi mã kiểm thử trong môi trường cô lập cao (Bubblewrap / Dead-Proxy `127.0.0.1:0`). Mạng ngoại vi bị ngắt tuyệt đối. | Yêu cầu `CapabilityToken` hoặc cờ `strict_mode` với đầy đủ rào chắn namespace. |
| **L3** | `WORKSPACE` | Đọc và ghi tệp tin trong phạm vi thư mục làm việc được cấp phép (`working_dir`). Cấm tuyệt đối path traversal (`..`) hoặc truy cập tệp nhạy cảm (`.env`, SSH keys). | `CapabilityToken` có chữ ký HMAC hợp lệ, khớp epoch hiện tại, và scope `workspace:write`. |
| **L4** | `REAL` | Chạy lệnh hệ điều hành thông qua bộ điều phối an toàn (`safe_process.py`), thực thi công cụ kiểm thử (ruff, bandit, pytest). | `CapabilityToken` hợp lệ, lệnh nằm trong danh sách an toàn (`_WHITELISTED_TOOLS`), cấm chaining operators (`;`, `&`, `|`) nếu thiếu token. |
| **L5** | `PRIVILEGED` | Thay đổi cấu hình bảo mật, kích hoạt/giải phóng kill switch, thao tác quản trị đặc quyền. | **Bắt buộc Human Confirmation Store** (`confirmation_id` có chữ ký của người vận hành) kèm theo token thẩm quyền cấp cao (`approval:grant`). |

---

## 3. Quản Lý Phiên & Thu Hồi Quyền Hạn (Session Management & Revocation)
1. **Định danh Token & Tính Độc Bản (JTI Tracking):**
   - Mỗi token JWT được cấp phát bắt buộc phải chứa một định danh duy nhất (`jti` - JWT ID).
   - Server duy trì danh sách theo dõi token đang hoạt động và danh sách thu hồi bền vững (`revoked_tokens`).
2. **Cơ Chế Thu Hồi Quyền Hạn Tức Thì (Instant Token Revocation - `SCP-06`):**
   - Khi phát hiện phiên làm việc bất thường hoặc theo lệnh của quản trị viên, hàm `revoke_token(jti)` được kích hoạt, đưa `jti` vào danh sách đen.
   - Mọi yêu cầu tiếp theo sử dụng token này đều bị từ chối fail-closed với mã lỗi `401 Unauthorized`.
3. **Chống Tấn Công Replay & Epoch Nonce:**
   - Mỗi `CapabilityToken` được gắn với một chu kỳ thời gian (`epoch`) và thời gian phát hành (`issued_at`). Token có thời gian trong tương lai (`> now + 60s`) hoặc epoch không khớp với `CapabilityAuthority` sẽ bị từ chối ngay lập tức (`InvalidTokenSignatureError`).
4. **Bảo Vệ WebSocket Auth (SEC-16):**
   - Cấm tuyệt đối truyền token xác thực qua URL query parameter (`?token=...`, CWE-598).
   - Chỉ chấp nhận xác thực WebSocket qua HTTP headers (`x-scp-capability-token`, `authorization`, `sec-websocket-protocol`). Vi phạm sẽ bị đóng kết nối ngay lập tức với mã lỗi 4001.
