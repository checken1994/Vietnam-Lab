# TASK AG (giao ngay vì AG rảnh — 14:0x): G0 + G1 autofix — WAVE OWNER ĐÃ CHỐT

(Nguồn: kế hoạch 5G trong memory scp-autofix-evolution-plan, owner chốt 13:2x "theo đề xuất của bạn". AG rảnh sau bundle sec06/safemath/buyer06 — VERIFY ĐỀU XANH.)

## G0 — AUDIT SỔ FIX (chạy ngay, 0 rủi ro, read-only)
1. Rà toàn bộ lịch sử fix của hội đồng几 ngày qua (git log + reports): mỗi fix ghi — phát hiện từ đâu, ai fix, CHECKER verdict, có tái hiện thật không, có regression nào phát sinh sau nó không (đối chiếu 6 fail ổn định buyer-audit: sec06 [đã fix], qlt06 [Claude đang làm], 4 T12 routing [đã fix ac24008c]).
2. Điểm phân loại: fix-chết (không vào luồng chạy), fix-placebo (test xanh nhưng thực tế không tác động), fix-thật. Đối chiếu luongsong-done.md của Claude (vừa nộp) cho phần lane của nó làm.
3. File done: `g0-autofix-ledger-done.md` — bảng + thống kê + danh sách nợ.

## G1 — POLICY MANIFEST MIỀN TỰ-SỬA (thiết kế, chưa code)
1. Thiết kế manifest fail-closed:哪些 loại lỗi SCP được PHÉP tự sửa (autofix) — miền an toàn (test hỏng rõ nguyên nhân, format/lint...), miền CẤM (security, policy, secret, anything touching governance), mỗi miền kèm điều kiện bắt buộc (test-đỏ-trước, mutant, shadow-canary, owner duyệt với loại nhạy cảm).
2. Nền tảng: autonomous_checker_gate (máy kiểm luật cứng) làm cột sống — manifest là data cho nó.
3. CHỈ thiết kế + đề xuất + test contract đỏ — KHÔNG code product. File done: `g1-manifest-design-done.md`.

## BẤT BIẾN
- Read-only với code sản phẩm (G0/G1 đều không sửa scp/).
- Không đụng pipeline/QLT-06 (Claude đang giữ).
- Guard CHECKER verify cả 2 file done.

## XẾP HÀNG SAU (tự lấy khi xong): WHY W2b — đọc inbox/why-w2b-amendments.md + W1 contract, dựng kế hoạch W2b (engine rebuild theo reality).
