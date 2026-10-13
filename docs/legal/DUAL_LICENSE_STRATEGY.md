# SCP Agent OS — Chiến Lược Cấp Phép Kép (Dual-Licensing Strategy)

> **Mã định danh:** `SCP-LEGAL-DLS-01`  
> **Cấp độ tuân thủ:** Open Source Community (AGPL-3.0) / Enterprise & Government Commercial  
> **Hiệu lực từ:** 2026-10-13  
> **Trạng thái:** ACTIVE & GOVERNED  

---

## 1. Tuyên Bố Chiến Lược & Mục Tiêu Cấp Phép

Hệ điều hành SCP Agent OS áp dụng mô hình **Cấp phép Kép (Dual-Licensing Model)** nhằm hài hòa giữa hai mục tiêu chiến lược:
1. **Phát triển Cộng đồng Mở (Open Source Community):** Thúc đẩy minh bạch khoa học, nghiên cứu mở và kiểm toán an toàn độc lập thông qua giấy phép mã nguồn mở có tính chất bảo vệ tương hỗ mạnh mẽ.
2. **Sẵn Sàng Doanh Nghiệp & Chính Phủ (Enterprise & Public Sector Readiness):** Cung cấp giải pháp triển khai an toàn, bảo vệ sở hữu trí tuệ, đáp ứng đầy đủ các yêu cầu pháp lý nghiêm ngặt về chủ quyền số, bảo mật dữ liệu và không bị ràng buộc bởi nghĩa vụ công khai mã nguồn của giấy phép copyleft.

---

## 2. Hai Luồng Cấp Phép Song Song (Two Licensing Tracks)

```text
                                  ┌─────────────────────────────┐
                                  │   SCP Agent OS Codebase     │
                                  └──────────────┬──────────────┘
                                                 │
                   ┌─────────────────────────────┴─────────────────────────────┐
                   ▼                                                           ▼
    ┌─────────────────────────────┐                             ┌─────────────────────────────┐
    │    Track 1: Mã Nguồn Mở     │                             │     Track 2: Thương Mại     │
    │         AGPL-3.0-only       │                             │     Enterprise/Chính Phủ    │
    ├─────────────────────────────┤                             ├─────────────────────────────┤
    │ • Miễn phí bản quyền        │                             │ • Bản quyền thương mại kín  │
    │ • Sao chép, sửa đổi tự do   │                             │ • Không ràng buộc copyleft  │
    │ • Ràng buộc copyleft mạng:  │                             │ • Triển khai On-premise/Air-│
    │   khi cung cấp qua mạng     │                               gapped bảo mật tuyệt đối    │
    │   phải mở toàn bộ mã nguồn  │                             │ • Bảo hành, cam kết SLA     │
    │ • Phù hợp: Học thuật, R&D,  │                             │ • Bồi thường vi phạm IP     │
    │   nghiên cứu an toàn        │                             │ • Phù hợp: Ngân hàng, BQP,  │
    │                             │                               Bộ Công An, Doanh nghiệp lớn│
    └─────────────────────────────┘                             └─────────────────────────────┘
```

### 2.1 Track 1: GNU Affero General Public License v3.0 (`AGPL-3.0-only`)
- **Phạm vi áp dụng:** Toàn bộ repository công khai, các bản phát hành cộng đồng trên GitHub và Docker Hub.
- **Điều kiện cốt lõi (Network Copyleft):** Theo Điều 13 của AGPL-3.0, bất kỳ tổ chức hoặc cá nhân nào sửa đổi SCP Agent OS và vận hành hệ thống như một dịch vụ mạng (Network Interactive Service / SaaS) cho người dùng bên ngoài đều **bắt buộc phải công khai toàn bộ mã nguồn sửa đổi** dưới giấy phép AGPL-3.0.
- **Mục đích an toàn:** Ngăn chặn việc các thực thể thương mại đóng gói SCP thành dịch vụ đám mây đóng mà không đóng góp lại các cải tiến bảo mật và phát hiện lỗi cho cộng đồng.

### 2.2 Track 2: Giấy Phép Thương Mại & Chính Phủ (Commercial & Government License)
- **Phạm vi áp dụng:** Khách hàng doanh nghiệp, các bộ ban ngành nhà nước, cơ quan an ninh - quốc phòng ký hợp đồng thương mại trực tiếp với đơn vị chủ quản bản quyền SCP.
- **Quyền hạn cấp phép:**
  - **Miễn trừ Copyleft:** Không có bất kỳ nghĩa vụ nào phải chia sẻ, công khai hoặc đóng góp lại mã nguồn nội bộ hoặc các module tích hợp tùy biến.
  - **Triển khai Air-Gapped & Sovereign:** Toàn quyền vận hành trong mạng nội bộ cô lập (air-gapped), trung tâm dữ liệu quốc gia mà không cần liên kết bản quyền ra ngoài internet.
  - **Bảo hành & Cam kết SLA:** Nhận hỗ trợ kỹ thuật chuyên sâu, cam kết thời gian khắc phục sự cố (SLA) và các bản vá bảo mật ưu tiên (Zero-Day Security Advisories).
  - **Bồi hoàn Pháp lý (IP Indemnification):** Bảo vệ khách hàng trước các tranh chấp quyền sở hữu trí tuệ phát sinh từ mã nguồn gốc của SCP.

---

## 3. Quản Trị Quyền Tác Giả & Đóng Góp (Contributor Authority & CLA)

Để duy trì tính hợp pháp vững chắc của mô hình cấp phép kép, hệ thống tuân thủ nghiêm ngặt các nguyên tắc sau:
1. **Quyền Sở Hữu Bản Quyền Cốt Lõi:** Đơn vị chủ quản duy trì đầy đủ quyền sở hữu trí tuệ đối với các thành phần cốt lõi của SCP Agent OS.
2. **Thỏa Thuận Cấp Phép Đóng Góp (CLA):** Mọi đóng góp từ bên thứ ba (pull request từ cộng đồng) bắt buộc phải ký thỏa thuận CLA (Contributor License Agreement), cấp quyền cho đơn vị chủ quản sử dụng, sửa đổi và cấp phép lại đóng góp đó dưới cả hai hình thức AGPL-3.0 và Commercial License.
3. **Phân Tách Phụ Thuộc (Third-Party Dependency Boundary):**
   - SCP kiểm soát 100% license của các thư viện bên thứ ba thông qua cổng kiểm toán tự động `tools/audit_dependencies.py` (DEP-05) và `docs/sbom.json`.
   - Toàn bộ dependencies bắt buộc phải thuộc các giấy phép tương thích (`MIT`, `Apache-2.0`, `BSD-3-Clause`, `PSF-2.0`, `MPL-2.0`, `LGPL-3.0-only`).
   - Tuyệt đối cấm đưa các thư viện có giấy phép xung đột hoặc không rõ nguồn gốc vào mã nguồn sản phẩm.

---

## 4. Kiểm Toán Tuân Thủ Tự Động (Continuous License Compliance Gates)

Mô hình cấp phép kép được bảo vệ bằng các rào cản kỹ thuật CI/CD tự động:
1. **SPDX License Identifier Enforcement (DEP-05):** Mọi component trong `docs/sbom.json` phải đạt 100% SPDX ID hợp lệ, không chấp nhận `UNKNOWN`.
2. **Tripwire Dependency Check:** `tools/audit_dependencies.py` chạy trước mọi commit và tại CI PR gate, fail-closed nếu phát hiện vi phạm bản quyền chuỗi cung ứng.
3. **Thông Báo Bản Quyền (Legal Notices):** Duy trì đầy đủ các tài liệu `docs/legal/LICENSES.md`, `docs/legal/THIRD_PARTY_NOTICES.md`, và `docs/legal/COMMERCIAL_LICENSE_NOTICE.md`.

---

## 5. Quy Trình Chuyển Đổi Sang Commercial License Cho Khách Hàng

1. **Thẩm định Kỹ thuật & Phạm vi:** Xác định rõ danh mục module, số lượng node/agent, và môi trường triển khai (Cloud, On-Premise, hoặc Air-Gapped).
2. **Ký kết Hợp đồng Cấp phép Thương mại:** Ký hợp đồng Master License Agreement (MLA) ghi nhận rõ phạm vi miễn trừ AGPL-3.0.
3. **Bàn Giao Bundle Doanh Nghiệp:** Cung cấp commercial license key, container digest đã ký cryptographic số và tài liệu hỗ trợ triển khai tiêu chuẩn ISO 27001 / Nghị định 13.
