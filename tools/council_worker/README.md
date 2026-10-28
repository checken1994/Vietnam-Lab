# Council Worker + CHECKER

Hiện thực hóa guồng hội đồng 3-AI (28/10) thành daemon tự chạy: đọc tín hiệu
push (file inbox + git) → CHECKER đối kháng (deterministic gates TRƯỚC, LLM
sau) → merge/push khi GO, feedback về maker khi NO-GO, hàng đợi owner cho
việc loại C. Không ai phải ngồi điều phối.

## Nguyên tắc (hóa học từ 12 lần bị owner phê trong ngày 28/10)

1. **PUSH, không POLL**: AI làm xong ghi file vào inbox; worker phát hiện qua
   mtime > mốc khởi ca (bài học guard v1 nuốt tín hiệu có sẵn).
2. **Không tin trạng thái — tin bằng chứng**: indicator "working" là giả;
   worker chỉ phán dựa trên exit code pytest, sha256 byte-identical, file
   tồn tại, git SHA.
3. **Deterministic-first**: việc nào máy quyết được (test, T00, mutation
   attack) thì KHÔNG hỏi LLM. LLM CHECKER chỉ vào sau các cổng máy đều xanh.
4. **Maker ≠ CHECKER**: CHECKER chạy call/context riêng, prompt đối kháng
   ("tìm cách phá claim"), bắt buộc kèm evidence lệnh+output.
5. **Anti-placebo bắt buộc**: artifact khai báo `- mutation: <file> ::
   <lệnh test>` → worker gỡ fix về HEAD, chạy test phải ĐỎ, khôi phục
   byte-identical (sha256) — placebo bị chặn tại cửa, không tin "xanh".
6. **Timeout mọi bước**: mỗi bước có giờ; quá giờ → ghi notChecked, đi tiếp
   hoặc hủy task — không treo vô hạn (bài học worker R7 treo 3 lần).
7. **3 neo không tự vượt**: (a) không tự sửa policy/manifest của chính worker;
   (b) không đụng secret / thao tác ngoài repo / xóa vĩnh viễn / publish;
   (c) merge/push chỉ khi CHECKER GO + cây sạch. Việc loại C → hàng đợi owner.
8. **PAUSE là PAUSE**: `paused_by_owner: true` trong state → worker chỉ ghi
   log "paused" mỗi chu kỳ, KHÔNG dispatch việc gì (bài học 20:00 owner chơi
   game). Chỉ owner bỏ cờ (sửa state hoặc lệnh `resume`).
9. **Durable state + bus**: mọi biến cố ghi `bus.jsonl` (append-only) và
   `state_council_worker.json` (atomic write); crash giữa chừng đọc lại là
   nối tiếp được. Mọi dòng bus đi qua `scrub_secrets` TRƯỚC khi ghi đĩa.
10. **Fail-closed**: thiếu key/config/tín hiệu mập mờ → KHÔNG hành động, ghi
    queue_owner. Không bao giờ đoán.

## Vận hành

```
# môi trường (đọc từ env, không hardcode):
COUNCIL_WORKER_HOME      # mặc định ~/.council-loop  (inbox/, bus.jsonl, state_council_worker.json)
COUNCIL_GLM_API_KEY      # key GLM (Z.ai) cho CHECKER LLM
COUNCIL_GLM_BASE_URL     # mặc định https://api.z.ai/api/paas/v4
COUNCIL_GLM_MODEL        # mặc định glm-5.3
COUNCIL_GIT_PAT_ENV      # tên biến chứa PAT workflow-scope (mặc định GITHUB_TOKEN)
COUNCIL_REPO             # mặc định repo chứa tools/ này

python tools/council_worker/worker.py            # chạy 1 ca (mặc định 28 phút)
python tools/council_worker/worker.py --once     # 1 vòng đơn lẻ (kiểm tra/dry)
python tools/council_worker/worker.py --resume   # xóa cờ paused_by_owner (CHỦ dùng)
```

Tín hiệu nhận: file mới trong `inbox/` (nội dung markdown, dòng đầu `# ...`);
commit mới trên nhánh liệt kê trong state `watch_branches` (đối chiếu
`last_seen_heads` mỗi vòng).

Loại C (dừng, xếp hàng owner): thấy các từ khóa/việc — secret/credential,
`git push` repo khác, xóa file ngoài repo, publish mạng ngoài, sửa
`tools/council_worker/policy.json`.

## Giới hạn bản đầu (trung thực — đã qua CHECKER đối kháng)

- Merge/push chỉ nhánh đã qua CHECKER đầy đủ; conflict cần tay → queue_owner.
- CHECKER LLM một call/phần (không hội thoại đa lượt) — dành cho artifact
  docs/spec; fix code phải có test+mutation chạy được trên máy.
- Không tự tạo worktree; maker vẫn là người/AI khác ghi vào inbox hoặc commit
  nhánh. Worker là thư ký + giám thị, không phải thợ.
- PAT lộ trong argv process listing trong giây push (trust-domain máy local);
  bus đã lọc token trước ghi. `--resume` không xác thực — cùng trust-domain
  máy của chủ.
- Cổng loại C là máy (regex+allowlist push target) — LLM chỉ là lớp sau.
