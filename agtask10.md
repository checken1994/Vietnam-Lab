# TASK AG — P8 BASELINE TRIAGE (giao 00:2x, chạy song song bằng subagent với 2 việc đang có)

Hub đã làm tới đây (ngươi tiếp tục, KHÔNG làm lại): cây 32-commit chạy P8 embedded được **12F/6130P/0E** (log đầy đủ `C:/Users/check/.council-loop/p8-emb2.log`). Cần baseline đối chứng để phân loại 12 fail: pre-existing vs regression.

## Tình trạng baseline build (hub dừng giữa chừng)
- Clone sạch đã có tại `C:/Users/check/.council-loop/p8base` @ 05e0e328 + Dockerfile/.dockerignore từ main tip
- Lỗi cuối: clone base KHÔNG có `data/identity/` (file do commit dehardcode 4d482eaa tạo, sau 05e0e328) ⇒ đã sed bỏ dòng `COPY data/identity` trong p8base/Dockerfile — **build lại từ đó** (chưa chạy)

## Việc
1. Build `scp-p8:base` từ p8base (sau sed) — nếu còn lỗi COPY gì thiếu nửa thì xử tương tự (chỉ hạ tầng build, không đụng nguồn test).
2. Chạy baseline suite embedded với ĐÚNG cùng bộ binds/env như p8-emb2 + `-v D:/scp/data/v13.db:/app/data/v13.db:ro`, log ra `C:/Users/check/.council-loop/p8-base.log`.
3. **DIFF 2 danh sách fail** (base vs emb2) — mỗi fail trong 12: PRE-EXISTING (có ở base) hay REGRESSION (chỉ ở 32-commit). Chú ý nhóm: 3 test knowledge_reader_tiers (nghi do v13.db thiếu trong emb2 — emb3 đang chạy thêm bind đó, đọc `p8-emb3.log` nếu xong) + 3-4 T12 + gd2 2 cái (buyer chấm order-dependent từ trước) + m06/live_cluster/CRLF (môi trường).
4. Nếu emb3 (có v13.db) vẫn fail reader-tiers ⇒ đó là regression thật của lane dichb 6a578411 ⇒ ghi rõ để hub giao lane fix.

## Đầu ra
- `p8-baseline-done.md` vào inbox: bảng 12 fail | base-status | phân loại | đề xuất (fix-lane / ghi-nhận-môi trường). Kèm Chuỗi Tại Sao 5 mục.
- KHÔNG sửa code sản phẩm. Guard CHECKER verify.
