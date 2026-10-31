# TASK AG (LÀM NGAY SAU task hiện tại — OWNER ĐÃ DUYỆT XÓA): BUYER-04 — xóa số liệu ảo tưởng khỏi README

(Giao 04:3x. Owner duyệt xóa sau khi hub kiểm độc lập thất bại phản bác: cả 4 con số đều không tái hiện được.)

## XÓA khỏi README.md (đúng dòng, không đụng gì khác)
1. "SQLite WAL Benchmark 0.299ms avg / 0.351ms p95, 200 iterations" — không có benchmark script nào sinh số này (benchmark/ toàn LLM eval).
2. "60 chuyên ngành → 2.120 Public APIs (1.010 auth=No)" — catalog thật data/free_api_catalog.json có 2.200 entries (số README lệch, không tái hiện).
3. "TaskKernel 17 trạng thái" → hoặc xóa con số, hoặc sửa "18" (definitions.py:19-24 = 18; chọn cách sửa 18 nếu câu văn cần giữ, xóa nếu chỉ là badge).
4. Badge bảng test: "Reality Portable Suite 76/76 PASS", "Contract Tests 99/99", "T00 0 Regressions" — 69 file reality, badge cũ 12 ngày, không tái chạy được read-only. XÓA cột số; giữ tên suite + lệnh chạy (đó là thật).

## NGUYÊN TẮC
- CHỈ xóa số/badge không chứng minh. KHÔNG xóa mô tả tính năng có code thật (TaskKernel, LLM Gateway, 5 services Docker — buyer xác nhận SUPPORTED tĩnh).
- KHÔNG thêm số mới thay thế (owner chưa yêu cầu; nếu sau này cần số thật thì lấy từ free_api_catalog.json count=2200 + sha256).
- Ghi chú: simulation/tests/data.test.js:98 CẤM chuỗi '0.299ms'/'76/76'/'2120 API' xuất hiện trong UI — việc xóa khỏi README ĐỒNG HƯỚNG với luật chống bịa của chính repo.

## VERIFY
- git grep sau khi sửa: 0.299 / 2120 / "17 trạng thái" / "76/76" / "99/99" không còn trong README.md.
- Chạy test liên quan README nếu có (t00/meta grep README?) — đỏ thì báo, đừng ép xanh.

## Đầu ra
- File inbox: `fe2e4readme-done.md` — diff README, bằng chứng grep sạch, giới hạn.
- Guard CHECKER verify; REJECTED = làm lại theo feedback.
