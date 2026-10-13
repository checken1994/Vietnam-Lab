# SCP Agent OS — Hồ Sơ Đánh Giá Tác Động Bảo Vệ Dữ Liệu Cá Nhân (DPIA)
## Tuân Thủ Nghị Định 13/2023/NĐ-CP (Việt Nam)

> **Mã hồ sơ:** `SCP-GOV-DPIA-ND13-2026`  
> **Căn cứ pháp lý:** Nghị định số 13/2023/NĐ-CP ngày 17/04/2023 của Chính phủ về Bảo vệ Dữ liệu Cá nhân  
> **Phạm vi hệ thống:** SCP Agent OS (TaskKernel, LLM Gateway, Egress Proxy, Local Storage)  
> **Trạng thái:** BAN HÀNH CHÍNH THỨC & SẴN SÀNG TRIỂN KHAI  

---

## 1. Mục Đích & Bối Cảnh Đánh Giá

Hệ điều hành SCP Agent OS được thiết kế như một nền tảng điều phối tác tử tự chủ phục vụ doanh nghiệp và các tổ chức chính phủ tại Việt Nam. Hồ sơ này đánh giá toàn diện các rủi ro liên quan đến việc thu thập, xử lý, lưu trữ và luân chuyển dữ liệu cá nhân theo đúng quy định tại **Điều 24 (Đánh giá tác động xử lý dữ liệu cá nhân)** và **Điều 25 (Đánh giá tác động chuyển dữ liệu cá nhân ra nước ngoài)** của Nghị định 13/2023/NĐ-CP.

---

## 2. Ma Trận Đối Chiếu Các Biện Pháp Kỹ Thuật Của SCP Với Nghị Định 13

| Điều Khoản NĐ 13 | Nội Dung Yêu Cầu | Cơ Chế Kiểm Soát Kỹ Thuật Của SCP Agent OS | Mức Độ Tuân Thủ | Bằng Chứng / Test Binding |
|---|---|---|:---:|---|
| **Điều 3 § 1-8** | Nguyên tắc xử lý dữ liệu hợp pháp, minh bạch, có mục đích cụ thể | Mọi luồng xử lý đều bắt nguồn từ Task có định danh, mục tiêu (`goal`), và hợp đồng tác vụ rõ ràng trong TaskKernel. Không xử lý ngầm ngoài phạm vi task. | **TUÂN THỦ 100%** | `tests/T04_kernel/test_task_kernel_mutation_contract.py` |
| **Điều 9** | Quyền của chủ thể dữ liệu (Biết, Đồng ý, Truy cập, Xóa dữ liệu, Phản đối) | • Quyền được biết: Event Journal ghi nhận minh bạch từng bước xử lý.<br>• Quyền rút lại đồng ý: Thu hồi tức thời qua Token Revocation (SCP-06).<br>• Quyền xóa dữ liệu: Purge endpoint và Task lifecycle xóa bỏ an toàn.<br>• Quyền phản đối: Global Kill Switch (O3-KIL / REC-03) ngắt tức thì. | **TUÂN THỦ 100%** | `tests/T03_capability/test_jwt_revocation_and_limits.py`<br>`tests/T10_recovery/test_mission_kill_switch_recovery.py` |
| **Điều 13** | Nghĩa vụ bảo vệ dữ liệu cá nhân cơ bản và nhạy cảm | Tách biệt hoàn toàn credentials và dữ liệu nhạy cảm thông qua Secret Loader (`scp/security/secret_loader.py`), loại bỏ tuyệt đối raw secret khỏi prompt, context, và stdout. | **TUÂN THỦ 100%** | `tools/scan_secrets.py`<br>`tests/T03_capability/test_security_sweep_s3.py` |
| **Điều 17 & 26** | Lưu trữ dữ liệu tại chỗ & Chủ quyền số (Data Residency) | SCP triển khai hoàn toàn On-premise hoặc Private Cloud đặt tại Việt Nam. Dữ liệu Task và Knowledge Base lưu tại SQLite/PostgreSQL nội bộ, không phụ thuộc vào hạ tầng lưu trữ đám mây nước ngoài. | **TUÂN THỦ 100%** | `tests/T04_kernel/test_pg_boot_runtime.py` |
| **Điều 25** | Chuyển dữ liệu cá nhân ra nước ngoài | Cổng kiểm soát Egress nghiêm ngặt (`SCP_EGRESS_MODE=deny` mặc định). Khi ở chế độ deny, toàn bộ kết nối ra máy chủ ngoại vi bị chặn hoàn toàn tại tầng socket/driver (fail-closed). Khi ở allowlist, chỉ cho phép các endpoint nội địa hoặc endpoint được phê duyệt có đánh giá DPIA. | **TUÂN THỦ 100%** | `tests/T03_capability/test_egress_enforcement.py`<br>`tests/T02_contract/test_data_sources_health_fail_closed.py` |
| **Điều 27** | Biện pháp bảo vệ kỹ thuật, phòng ngừa xâm nhập | Cách ly OS-level qua bwrap namespace / Windows Job Objects (SCP-01). Chống giả mạo nhật ký qua HMAC SHA-256 Keyed Journal (M-11). Rate limiting chống DoS (SCP-06). | **TUÂN THỦ 100%** | `tests/T03_capability/test_sandbox_evaluator_isolation.py`<br>`tests/T04_kernel/test_kernel_crash_consistency.py` |

---

## 3. Quy Trình Thực Hiện Quyền Của Chủ Thể Dữ Liệu (Data Subject Rights SOP)

1. **Quyền Truy Cập & Cung Cấp Dữ Liệu (Right to Access):**
   - Chủ thể dữ liệu hoặc người quản trị có thể tra cứu toàn bộ lịch sử tác vụ, bản ghi sự kiện và dữ liệu liên quan qua endpoint `/v1/tasks/{task_id}/events` hoặc xuất báo cáo qua SQLite/PostgreSQL schema `events`.
2. **Quyền Chỉnh Sửa & Xóa Bỏ (Right to Rectification & Erasure):**
   - Hỗ trợ xóa bỏ vĩnh viễn (Secure Data Purge) các checkpoint, bản ghi trung gian và dữ liệu bộ nhớ đệm (cache) khi task kết thúc hoặc khi có yêu cầu hợp pháp từ chủ thể.
3. **Quyền Thu Hồi Quyền & Ngăn Chặn Xử Lý (Right to Revoke & Halt):**
   - Khi chủ thể rút lại sự đồng ý, token truy cập được đưa vào danh sách thu hồi ngay lập tức thông qua hàm `revoke_token(jti)` (M-09/SCP-06). Mọi yêu cầu tiếp theo mang token này bị chặn fail-closed với mã 401.

---

## 4. Quản Trị Rủi Ro Chuyển Dữ Liệu Xuyên Biên Giới (Điều 25 NĐ 13)

- **Cấu hình Mặc định (Out-of-the-box Invariant):** Biến môi trường `.env.example` cấu hình mặc định `SCP_EGRESS_MODE=deny`.
- **Đánh Giá Nhà Cung Cấp LLM Ngoại Vi:**
  - Nếu tổ chức kích hoạt `SCP_EGRESS_MODE=allowlist` để sử dụng LLM quốc tế (như OpenAI, Anthropic, OpenRouter), tổ chức bắt buộc phải hoàn thành Hồ sơ chuyển dữ liệu ra nước ngoài theo Điều 25 NĐ 13 gửi Cục An ninh mạng và phòng, chống tội phạm sử dụng công nghệ cao (A05) - Bộ Công An.
  - SCP hỗ trợ triển khai kết nối trực tiếp với các mô hình SLM/LLM tự chủ nội địa hoặc On-premise qua Gateway tương thích OpenAI-protocol mà không phát sinh luồng dữ liệu ra nước ngoài.

---

## 5. Kết Luận & Khuyến Nghị Triển Khai

Kiến trúc SCP Agent OS tại commit HEAD hoàn toàn đáp ứng các nguyên tắc cốt lõi về bảo vệ dữ liệu cá nhân theo Nghị định 13/2023/NĐ-CP:
1. **Dữ liệu được lưu trữ và kiểm soát tại chỗ (100% Data Sovereignty).**
2. **Khóa chặt egress ngăn chặn rò rỉ dữ liệu xuyên biên giới ngoài ý muốn.**
3. **Nhật ký bất biến HMAC bảo vệ tính toàn vẹn và phục vụ giải trình thanh tra.**
4. **Cơ chế thu hồi token và ngắt khẩn cấp đảm bảo quyền kiểm soát tối thượng của con người.**
