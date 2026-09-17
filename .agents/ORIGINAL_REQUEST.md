# Original User Request

## 2026-09-08T12:26:38Z

# Teamwork Project Prompt — R2, R3, R6 Remediation

Dự án SCP (Agent OS) đang cần vá 3 lỗ hổng kiến trúc nghiêm trọng cuối cùng (R2, R3, R6) để đạt trạng thái Autonomous 24/7.

Working directory: c:\Users\check\Downloads\scp
Branch hiện tại: omega/gap-01-remediation (hoặc main tuỳ bạn checkout, hãy tạo nhánh mới nếu cần, ví dụ: remediation/R2-R3-R6).

MANDATORY BINDING: You are strictly bound by Zero-Trust and Fail-Closed principles. You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Any code modifications must explicitly enforce boundaries at the Database/Hardware level, not via RAM/Variables.

## Lỗ hổng cần vá

### R2: Execution Bypass (PCController)
- Vấn đề: `PCController` có các phương thức thực thi trực tiếp (VD: chạy command) mà thiếu boundary kiểm tra token hợp lệ từ Unified Broker.
- Yêu cầu: Thêm token boundary vào `PCController`. Bất kỳ request thực thi nào cũng phải có token (HMAC-SHA256 signature hợp lệ) được cấp phát đúng thẩm quyền. Chặn fail-closed nếu thiếu/sai token.

### R3: Provenance Forgery (Verifier receipts)
- Vấn đề: Các biên lai `verification.passed` (verifier receipts) đang thiếu độc lập (cryptographic provenance). Worker có thể tự giả mạo biên lai thành công để lừa Kernel.
- Yêu cầu: Thêm chữ ký số (cryptographic signature/HMAC) vào Receipt. Kernel phải verify chữ ký này trước khi commit trạng thái `COMPLETED`.

### R6: AutoFix Rollback (Cognitive loop perfect isolation)
- Vấn đề: Vòng lặp Cognitive/AutoFix thiếu cơ chế perfect isolation và rollback. Nếu AI áp dụng code lỗi, hệ thống bị hỏng (catastrophic corruption).
- Yêu cầu: Xây dựng cơ chế snapshot/rollback (có thể dùng `data/shadow/` hoặc git stash/restore) cho file trước khi AutoFix áp dụng patch. Tự động rollback nếu Reality Test (pytest) thất bại sau khi patch.

## Yêu cầu Bắt buộc (FA-12, FA-13)
- Vẽ Causal Graph và tạo file báo cáo `EMERGENCY_GAP_REPORT.md` (nếu phát hiện lỗ hổng lân cận).
- Phủ test cho toàn bộ nhân quả (Causal-Driven Test Generation) trong thư mục `tests/`. Chạy `pytest` phải xanh.
- Cuối cùng, tổng hợp kết quả (Fix steps, Test outcomes) vào artifact báo cáo.

## 2026-09-13T21:15:02Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: [none — teamwork routes from the description]

Hoàn thiện, kiểm thử và hợp nhất (commit) toàn bộ công việc của S26 (xóa 13.8k dòng code cũ) và S24 (logic Question Router T2-first) trong một lần một cách an toàn.

Working directory: `c:\Users\check\Downloads\scp`

## Requirements

### R1. Tích hợp S26 (Expert Unification)
Hoàn thành việc loại bỏ các file legacy (`judge_parts`, `slm_impls`, `slms.py`) theo đúng những gì S26 đã chuẩn bị. Đảm bảo code không bị gãy dependencies.

### R2. Tích hợp S24 (T2-first Routing)
Tích hợp file `question_router.py` và các đoạn code móc nối dở dang trong `ask_kernel_adapter.py`. 

### R3. Sửa lỗi Test (Khắc phục di chứng S22)
Sửa lại hàm `_disable_openrouter` trong bài test `test_ws_chat_fail_closed_when_no_answer_source_available` (thuộc T02) để tắt toàn bộ các LLM Provider mới (Groq, Cerebras, Gemini, Nvidia) vốn được thêm vào từ S22, giúp bài test này xanh trở lại.

## Acceptance Criteria

### Verification & Commit
- [ ] Chạy lệnh `pytest tests/T02_contract tests/T03_capability tests/T07_learning -q` trả về 0 failures.
- [ ] Các lỗi "đỏ" do test cũ gọi vào file đã bị xóa (FA-02) được báo cáo rõ hoặc có cơ chế bypass hợp lệ khi commit.
- [ ] Sau khi test pass, thực hiện chốt commit toàn bộ S24 và S26 vào nhánh hiện tại (`audit/runtime-guard-AUDIT-20260909`).

## 2026-09-13T22:14:57Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: [none — teamwork routes from the description]

Chiến dịch "Đại Phẫu Thuật" (Full Sweep): Giải quyết triệt để S33/S35 (không dùng Mock), thanh toán nợ kỹ thuật (Tripwire 5), và tích hợp toàn bộ 6 tính năng Backlog (SSE, Structlog, Typed Settings, v.v.).

Working directory: c:\Users\check\Downloads\scp
Integrity mode: development

## Requirements

### R1. S33 & S35: Zero-Trust Implementation
Viết lại Contract Prober (S33) và Model Lifecycle (S35). Tuyệt đối KHÔNG sử dụng `unittest.mock` hay `AsyncMock`. Bắt buộc phải gọi API vật lý qua loopback (127.0.0.1) hoặc dựng Docker container thật để kiểm thử.

### R2. Technical Debt Cleanup
Dọn dẹp nợ kỹ thuật từ báo cáo Tripwire 5: Xóa 11 API routes bị đánh dấu DEAD và hoàn thiện móc nối (wire-in) cho 11 đoạn code TODO.

### R3. Architecture Backlog
Tích hợp Token Streaming (SSE), thay thế toàn bộ lệnh print bằng thư viện Structlog, dùng pydantic-settings cho biến môi trường, và cài đặt hệ thống Auth JWT mới.

## Acceptance Criteria

### Verification & Anti-Cheating
- [ ] Lệnh `grep -rn "unittest.mock" tests/T05_gateway/` TRẢ VỀ RỖNG (Chứng minh không dùng mock giả lập).
- [ ] Chạy thành công toàn bộ `pytest tests/ -q` (Bao gồm các bài test mới cho S33/S35 kết nối qua cổng mạng vật lý).
- [ ] Trình `t00_meta_audit.py` (Forensic Auditor) chạy qua mà không phát cảnh báo vi phạm FA-01 đến FA-13.
- [ ] 0 lỗi Import trên toàn hệ thống sau khi xóa code DEAD ở R2.
