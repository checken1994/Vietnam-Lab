# SCP Skill-Agent Chassis v1 (2026-10-30)

Khung dùng chung cho các **agent độc lập ngoài SCP**, mỗi bản thể (workflow đã lưu) hiện thân
1 skill trong `.agents/skills/`. Bản thể gọi chassis = 2 lời gọi `files.read` bắt buộc trong
workflow: nạp CHÍNH file này + nạp SKILL.md của skill tương ứng, rồi nội suy vào prompt.

## 6 luật chassis (mọi bản thể phải tuân, vi phạm = kết quả vô hiệu)

1. **Skill Activation runtime-load (cấm training memory):** workflow PHẢI `files.read`
   SKILL.md từ workspace tại thời điểm chạy và nội suy nguyên văn vào prompt của kiểm toán viên.
   KHÔNG được phép mô tả skill từ trí nhớ. Đây là hiện thân Forced Skill Activation của
   `.agents/AGENTS.md` §4.1.

2. **Fail-closed trên HEAD + Auto-detect (FA-10):** trước khi nạp skill, workflow chạy `git rev-parse HEAD`
   (fail-closed nếu lệnh git lỗi hoặc SHA không hợp lệ 40 ký tự hex). Nếu `args.head_sha` để trống (`""`)
   hoặc `"auto"`/`"HEAD"`, workflow tự động neo vào `LIVE_HEAD` vừa đọc được; nếu truyền `args.head_sha`
   cụ thể mà khác `LIVE_HEAD` → DỪNG TOÀN BỘ fail-closed và trả về lý do, không chạy nửa chừng. Mọi bằng
   chứng sinh ra phải neo đúng HEAD đã xác nhận.

3. **Binding cưỡng chế mỗi agent (nguyên văn bắt buộc, chèn đầu prompt mọi role):**
   "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles
   (SCP DNA #1–#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from
   self-granting authority or simulating PASS results. Không bịa output — bằng chứng phải là
   raw stdout/HTTP exchange thật (FA-08). Claim lỗ hổng phải kèm script repro đã chạy văng lỗi
   thật, static-only thì gắn nhãn HYPOTHESIS (FA-09)."

4. **Lineage tách maker/checker:** kiểm toán viên (maker) và đối soát viên (checker) là 2 agent
   KHÁC nhau, checker tự tay tái hiện các claim then chốt bằng lệnh/HTTP thật, không tin lời khai.
   Checker bất đồng với maker → ghi UNKNOWN + nội dung bất đồng, KHÔNG tự phân xử thắng thua
   (needs-human). Checker không re-run mọi thứ — chỉ claim then chốt, tiết kiệm quota.

5. **Nhãn bằng chứng bắt buộc:** mọi phát hiện phải mang 1 trong:
   `OBSERVED` (tự tay chạy thấy) / `SUPPORTED_INFERENCE` (suy ra từ evidence file) /
   `UNPROVEN` (chưa kiểm) / `HYPOTHESIS` (static nghiêm trọng chưa có repro).
   PASS trong scope không được viết thành "hoàn tất/an toàn tuyệt đối" (DNA #22, #24).

6. **Sổ bằng chứng append-only + vệ sinh scope:**
   - Checker sau khi chốt PHẢI append ĐÚNG 1 dòng vào `.zcode/workflows/ledger/verdicts.log`:
     `YYYY-MM-DD HH:MM | <ten-workflow> | <head-8-ky-tu> | GO|NO-GO|UNKNOWN | <tóm tắt 1 câu>`
     (lấy thời gian bằng lệnh `date`; `mkdir -p` thư mục nếu chưa có; file chưa tồn tại thì tạo;
     KHÔNG sửa/xóa dòng cũ).
   - Tài nguyên Docker của bản thể đặt tên `scp-agent-<viet-tat>-*`; checker dọn HẾT
     container/image mình tạo sau khi tái hiện xong; TUYỆT ĐỐI không đụng container/port/image
     của phiên khác; không `docker rm` thứ mình không tạo.
   - CẤM in giá trị secret (.env); DÙNG key để auth thì được, KHÔNG echo. CẤM sửa/tạo/xóa file
     trong D:/scp (log tạm để ngoài repo, ví dụ Temp).

## Vai trò các thành phần

| Thành phần | Là gì | Ở đâu |
|---|---|---|
| Chassis (file này) | 6 luật + binding + format sổ | `.zcode/workflows/chassis/agent-chassis.md` |
| Bản thể = 1 skill | workflow `.dwf.ts` đã lưu, tự nạp chassis + skill khi chạy | `.zcode/workflows/scp-agent-*.dwf.ts` |
| Sổ bằng chứng | append-only verdict log, nguồn sự thật chéo-bản thể | `.zcode/workflows/ledger/verdicts.log` |
| Khởi động | `CreateWorkflow saved: { name: "<tên-bản-thể>", args: { head_sha, ... } }` | on-demand, không always-on |

## Bản thể đã dựng (2026-10-30, CHƯA chạy lần nào — lần chạy đầu của mỗi bản thể là nghiệm thu)

| # | Workflow | Skill | Ghi chú |
|---|---|---|---|
| 1 | `scp-agent-runtime-audit` | scp-runtime-audit | port 8040 |
| 2 | `scp-agent-capability-security-review` | scp-capability-security-review | port 8041, burst arg |
| 3 | `scp-agent-reality-verifier` | scp-reality-verifier | ~25-40', suite 2 conventions |
| 4 | `scp-agent-dna-audit` | scp-dna | chỉ-đọc, cần args.target |
| 5 | `scp-agent-delta-audit` | scp-delta-audit | FA-09 repro bắt buộc, args.hypothesis |
| 6 | `scp-agent-gateway-resilience` | scp-gateway-resilience | port 8042, trần LLM call |
| 7 | `scp-agent-learning-loop-guard` | scp-learning-loop-guard | chỉ-đọc, không mạng |
| 8 | `scp-agent-task-kernel-review` | scp-task-kernel-review | không boot server |
| 9 | `scp-agent-startup-troubleshooter` | scp-startup-troubleshooter | port 8043, image riêng |
| 10 | `scp-agent-safe-latency-optimizer` | scp-safe-latency-optimizer | port 8044, ≤9 probe |
| 11 | `scp-agent-web-orchestration-safety` | scp-web-orchestration-safety | chỉ-đọc, live probe optional |
| 12 | `scp-agent-release-evidence-gate` | scp-release-evidence-gate | port 8045, đọc sổ suite |
| 13 | `scp-agent-skill-review` | scp-skill-review | chỉ-đọc pack |
| 14 | `scp-agent-computer-use-recovery` | scp-computer-use-recovery | reconcile-only, args.task_context |
| 15 | `scp-agent-continuous-operations-loop` | scp-continuous-operations-loop | thiết kế/thẩm định, không chạy loop |
| 16 | `scp-agent-typesafe-eval` | typesafe-agent-eval | workflow-only, cần TYPESAFE_API_KEY |

`scp-dna` không có bản thể riêng — nó là DNA chung (luật 3 của chassis). Ngoại trừ #16 (workflow-only), 15 bản thể còn lại phủ 15 skill normative.

---

## V2 (2026-10-31) — 4 luật mới sau phản bác của owner

Owner phản bác 31/10: *"Đóng vòng hiện tại của bạn chỉ là hiện tại chứ không phải đóng vòng
hoàn chỉnh của SCP. Việc gắn cố định từng agent vào 1 cổng là sai lầm."* Phản bác đã thử,
2/3 phòng thủ sụp trước bằng chứng — 4 luật dưới đây là kết quả sửa, ràng buộc cao hơn 6 luật ban đầu:

7. **Cổng = bất biến, KHÔNG gắn người.** Một cổng khai báo INVARIANT phải đúng (VD: "suite
   xanh cùng SHA", "0 secret echo", "boot được theo tài liệu"), KHÔNG khai báo agent nào chấm.
   Cổng quan trọng tối thiểu **2 lineage khác nhau** mỗi lần chấm; phân công theo từng run
   (xoay vòng/quay số, GHI VÀO SỔ ai chấm cái gì); không để một agent chấm một cổng 2 lần
   liên tiếp mà thiếu cặp đối lineage. Port mapping của bản thể chỉ là vệ sinh tài nguyên
   chống trùng container, KHÔNG phải kiến trúc gắn cổng.

8. **Probe không tĩnh (chống phrase-listing ở TẦNG KIỂM).** Bộ probe cố định trong workflow
   v1 (6 câu lành / 5 novel / auth matrix…) chỉ có giá trị NGHIỆM THU lần đầu. Từ v1.1, mỗi
   run PHẢI sinh probe mới (generator LLM + corpus xoay) và lẫn mồi giả anti-placebo ngẫu
   nhiên. Probe đã lộ trong run trước tự động hạ giá trị bằng chứng. Owner mandate no-hardcode
   (28/10) áp dụng cho cả lớp auditor — auditor cũng không được "nhớ mẫu câu".

9. **Vòng sống theo trigger, không phải verdict một lần.** merge→main = delta gate (rẻ);
   định kỳ đêm = broad sweep; milestone/hoàn-thiện = buyer sweep; bất kỳ = deep ad-hoc.
   Bằng chứng trong ledger **tự hết hạn theo SHA**: stale tính bằng số commit chênh, không
   bằng cảm giác. Verdict cũ không bao giờ "đóng vĩnh viễn" một hệ thống đang sống — thực
   chứng: verdict người mua 05e0e328 stale ngay khi HEAD thành c9aa96e7 (3 commit sau 1 đêm).

10. **Quan sát viên kiểm chính hệ kiểm.** Định kỳ, một agent độc lập audit chính bộ 16 bản
    thể: probe có lộ chăng, phân công có xoay chăng, sổ có sạch chăng, bản thể có drift khỏi
    SKILL.md chăng. Hệ miễn dịch không được tự miễn (DNA #3/#21).
