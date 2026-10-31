# TASK AG: F-E2E-2 PHẦN COMPOSE/PROVIDER — container E2E phải có provider dùng được

(Giao 04:05 bởi hub. Spec ĐÃ CHECKER-VERIFIED: e2e-falsepositive-fix-spec.md. Phần này TÁCH khỏi lane code của Claude — chỉ compose/config.)

## Root cause đã xác minh
- docker-compose.full.yml chỉ truyền 2 key provider (OpenRouter, Groq) vào container; E2E production posture không provider nào dùng được → gateway trả rỗng → 4/6 câu lành withheld oan.
- Claude làm phần code (trạng thái thật + nhãn + field chết) ở lane riêng — ĐỤNG ĐẾN code python.

## Việc
1. Kiểm chứng bằng chạy thật: boot compose (tư thế E2E như e2e-report.md), đếm số provider khả dụng trong container (env + gateway config), chụp danh sách TÊN provider (KHÔNG in giá trị key).
2. Bổ sung env passthrough provider còn thiếu vào docker-compose.full.yml theo đúng pattern 2 key hiện có. KHÔNG in key vào file done/log — chỉ tên biến + tình trạng available.
3. Chạy lại 1 câu lành (VD "Viết 2 câu thơ về con mèo") qua /ask thật: phải CÓ đáp án (không còn rỗng). Ghi HTTP + lane + governance + thời gian.
4. Nếu phát hiện provider key thiếu ở .env host (không có để truyền) → ghi rõ trong done-file, ĐỪNG tự tạo/ghé key.

## Bất biến
- Không mở khóa egress allowlist, không thêm provider ngoài bộ hiện có của repo.
- .env không được đọc trực tiếp bằng mắt — chỉ qua compose env mechanism.

## Đầu ra
- File inbox: `fe2e2b-done.md` — danh sách provider trước/sau (tên only), diff compose, bằng chứng câu /ask thật có đáp án, giới hạn.
- Guard CHECKER verify; REJECTED = làm lại theo feedback.
