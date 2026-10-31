# TASK AG — LESSON-LEDGER: SỔ BÀI HỌC TỰ LƯU CỦA SCP (owner 31/10 01:4x, luật 12 hook Claude)

## Bối cảnh (nguyên văn luật owner, qua hook Claude mục 12)
> "SCP không phải cái gì cũng biết — SCP phải TỰ HỌC, TIẾN HÓA như con người theo thời gian. Mọi rủi ro còn lại / lỗi / lệch kết quả / UNKNOWN = BÀI HỌC SCP tự lưu (bản ghi có cấu trúc, nhãn HYPOTHESIS) → kiểm chứng độc lập bởi Reality → WHY trước khi tự sửa → shadow → áp dụng/bác."

## Việc — dựng NỀN lesson-ledger (DESIGN + implementation tối thiểu chạy được)

### 1. Thiết kế schema bài học (lesson record)
Mỗi bài học 1 bản ghi JSON/JSONL tại `data/lessons/lessons.jsonl` (append-only, tương tự verdict log):
```
{
  lesson_id: "L-<timestamp>-<slug>",
  trigger: {source: "e2e|checker|audit|runtime", sha, file, test_name?, question?},
  observation: "câu gì sai/lech/UNKNOWN", 
  hypothesis: "vì sao (nhãn HYPOTHESIS — chưa kiểm)",
  evidence: [{type: "docker|probe|git", command, result_hash}],
  status: "HYPOTHESIS|REALITY-CHECKED|SHADOW|APPLIED|REJECTED",
  independent_check: {by: "checker lineage khác", verdict, at},
  why_chain: [5 mục chuẩn luật 15],
  next_action: "shadow|autofix-lane|watch|none",
  kill_switch_ref: "owner-on-top"
}
```

### 2. Implementation tối thiểu (chạy được ngay)
- **Writer:** 1 hàm Python `scp/learning/lesson_ledger.py` — `record_lesson(...)` append-only + `query_lessons(status=...)`. KHÔNG đụng đường /ask (chỉ là kho ghi, chưa wire vào pipeline).
- **Trigger nguồn (làm 2 cái trước):** (a) từ guard-verdicts.log — mỗi REJECTED + mỗi UNKNOWN_WITH_REASON trong done-file = 1 bài học tiềm năng; (b) từ e2e-extra-done.md của chính AG (4 tiêu chí vừa đo) — mọi FAIL/lệch = bài học.
- **Reader/summary:** 1 endpoint nội bộ hoặc CLI `python -m scp.learning.lesson_ledger --summary` in bảng: bao nhiêu HYPOTHESIS, bao nhiêu REALITY-CHECKED, bao nhiêu đã APPLIED.
- **Bất biến:** KHÔNG tự áp dụng bất cứ fix nào — chỉ ghi + nhãn. Chống tự đầu độc: mọi bài học mới = HYPOTHESIS cho tới khi có independent_check từ lineage khác (CHECKER guard). Kill switch: owner duyệt trước khi chuyển status APPLIED (thủ công lúc đầu).

### 3. Điền ~10 bài học THẬT từ đêm nay (seed từ thực tế)
Từ các file inbox + guard-verdicts.log đã có: (1) shadow gọi LLM không xin phép (T05 bắt), (2) keyword regex thắng tín hiệu hội thoại (ca 13 e2e-final), (3) B1 √144 không vào fast-path vì router thiếu tín hiệu "căn bậc hai", (4) mount 9p stall 3 giờ vs embedded 13 phút, (5) bind v13.db read-only làm vỡ suite ghi DB, (6) compose command:['8000'] thiếu entrypoint trong image mới, (7) PAT push cần x-access-token basic không phải bearer, (8) Claude app 4.4212 Enter cần click-focus + draft ma, (9) AG dừng sau mỗi delivery không tự kéo tiếp, (10) tail pipe nuốt output P8 3 lần. Mỗi bài: hypothesis + evidence thật + status HYPOTHESIS.

### 4. Wire点 tối thiểu (không đụng /ask)
- Guard CHECKER REJECTED → tự sinh bài học bằng writer (gọi qua world.run từ guard không được — writer chạy bởi AI khi làm xong việc, ghi vào ledger từ inbox file)
- Đơn giản nhất: 1 CLI script `tools/seed_lesson.py` Claude/AG gọi khi phát hiện lỗi mới.

## Bất biến tổng
- Luật 14 (không hardcode câu hỏi), luật 15 (Chuỗi Tại Sao), luật 12 (không tự thắng — mọi APPLIED cần owner/lineage khác).
- Tách file: KHÔNG đụng api_server/pipeline/router (Claude đang giữ).
- Chuỗi Tại Sao 5 mục trong done-file.

## Đầu ra
- `lesson-ledger-done.md` vào inbox: schema + code layout + 10 bài học seed + cách gọi + giới hạn.
