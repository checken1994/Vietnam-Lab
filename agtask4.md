# TASK AG (xếp HÀNG sau BUYER-02): BUYER-03 — docker build từ clone sạch phải THÀNH CÔNG

(Giao 04:2x. Nguồn: buyer đợt 2 — [HIGH/OBSERVED] + [HIGH/SUPPORTED_INFERENCE], đối soát GO.)

## Bằng chứng buyer
1. `docker build` từ clone sạch GitHub @ 05e0e328 → **BUILD_EXIT=1**: `Dockerfile:161 COPY dashboard/.next/` — artifact Next.js bị gitignore, không có trong clone (ls chỉ còn public/, scripts/, src/).
2. `dashboard/Dockerfile:8-9`: `COPY .next/standalone` + `COPY .next/static` — cùng bệnh cho image dashboard (trên máy trắng gần như chắc chắn fail — suy luận tĩnh được đối soát ghi SUPPORTED).
3. Lối thoát duy nhất hiện nay `--target runtime` GIẤU trong compose (dòng 30), KHÔNG có trong README → người mua tự build không biết đường.

## Việc
1. Sửa Dockerfile backend: multi-stage build dashboard bên trong image (npm ci + next build) thay vì COPY artifact gitignored — hoặc fetch artifact bằng build-arg có kiểm tra. Ưu tiên: `docker build .` từ clone SẠCH phải exit 0.
2. Sửa dashboard/Dockerfile tương tự cho path máy trắng.
3. Bỏ/khỏi phụ thuộc lối `--target runtime` giấu trong compose (hoặc đưa nó thành đường chính tài liệu hóa).
4. Verify thật: clone sạch ra thư mục temp (git clone file:// hoặc copy worktree sạch), `docker build -t scp-cleanbuild-test .` → exit 0. KHÔNG dùng image có sẵn trên máy làm bằng chứng.
5. Giữ cảnh báo lint SecretsUsedInArgOrEnv (ENV placeholder rỗng) — nếu fix được luôn thì tốt (ARG thay ENV).

## Đầu ra
- File inbox: `fe2e3build-done.md` — diff Dockerfile(s), lệnh + log build từ clone sạch (exit 0), giới hạn.
- Guard CHECKER verify (sẽ tự build lại từ clone sạch); REJECTED = làm lại.
