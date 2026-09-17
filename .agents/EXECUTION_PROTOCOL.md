# SCP Execution Protocol

> **Quy trình bắt buộc cho MỌI AI agent trước khi sửa code SCP.**
> File này được tự động nạp bởi các AI coding surface (Antigravity, ChatGPT, Codex, etc.)
> thông qua cơ chế `.agents/` directory loading.

---

## Phase 0: Reconcile (BẮT BUỘC — trước khi sửa bất kỳ file nào)

1. Đọc `GA.md` trên `main` — xác định trạng thái hiện tại và handoff notes.
2. Đọc `SCP_MASTER_END_TO_END_CAUSAL_AUDIT_REPORT_2026_VERIFIED.md` — xác định 8 gap ưu tiên.
3. Xác định exact HEAD SHA: `git rev-parse HEAD`.
4. So sánh HEAD với baseline snapshot nếu cần: `git diff <baseline>..HEAD --stat`.
5. Tạo candidate branch nếu cần, hoặc xác nhận branch hiện tại.
6. **Báo cáo SHA cho user** và chờ xác nhận trước khi tiếp tục.
7. **KHÔNG mutation bất kỳ file nào trước khi Phase 0 hoàn tất.**

---

## Phase 1: Implement (CHỈ sau khi user duyệt)

1. Chỉ sửa files nằm trong scope được giao cho Wave/Agent hiện tại.
2. Mỗi thay đổi phải là **small reversible patch**.
3. **Nêu rõ exact files dự định sửa** trước khi bắt đầu.
4. Refresh branch HEAD trước mỗi mutation: `git pull --rebase origin main`.
5. Chạy scoped tests **SAU MỖI thay đổi** — không gom nhiều thay đổi rồi test một lần.
6. Nếu test đỏ → phân loại (HARNESS_BROKEN, PRODUCT_FAIL, PRODUCT_BLOCKED). Sửa harness phải giữ hoặc tăng strictness. Không được loosen assertion (FA-01).

---

## Phase 2: Evidence (BẮT BUỘC — trước khi claim Done/Fixed/Pass)

1. Chạy **toàn bộ** test suite: `python -m pytest tests/ -v`.
2. **Paste terminal output thực tế** — không tóm tắt, không claim bằng lời.
3. Ghi exact SHA vào evidence: `git rev-parse HEAD`.
4. Nếu có test fail → phân loại:
   - `HARNESS_BROKEN` — test harness lỗi, cần sửa harness (giữ hoặc tăng strictness).
   - `PRODUCT_BLOCKED` — implementation thiếu, cần implement.
   - `PRODUCT_FAIL` — product lỗi, cần sửa product.
   - `STRUCTURAL_COVERAGE_GAP` — test coverage chưa đủ.
   - `BLOCKED` — phụ thuộc vào Wave/Agent khác.
5. Liệt kê **limitations và unresolved blockers** — không tự tuyên maturity.

---

## Phase 3: Handoff (SAU KHI evidence đầy đủ)

1. Cập nhật `GA.md` trên `main` với trạng thái mới.
2. Commit với message chuẩn: `fix(scope): mô tả ngắn gọn`.
3. **Chờ user review** trước khi mở Wave tiếp theo.
4. Không tự quyết định push — hỏi user.

---

## Wave Gate Rules (Kế hoạch GPT SOL 5.6)

### Thứ tự bắt buộc:
```
S0 (Baseline Reconcile)
  ↓
P0-A (A1 Epistemic, A2 Capability, A3 Recovery, A4 ZeroCost) — song song
  ↓
P0-B (B1 AutoFix Reality) — phụ thuộc A1
  ↓
P0 Integration Gate — kiểm tra toàn bộ P0
  ↓
P1-A/B/C/D — song song sau P0
  ↓
P2, P3, P4, P5 — tuần tự
```

### Không được song song:
- Hai agent sửa cùng authority file.
- AutoFix Reality trước khi EpistemicBoundary contract được khóa.
- Integration/release trong khi agent khác còn mutation production tree.
- Final auditor đồng thời là agent vừa sửa code.

### Mỗi agent phải:
1. Nhận dependency cone nhỏ.
2. Nêu exact files dự định sửa.
3. Refresh branch HEAD trước mutation.
4. Tạo small reversible patch.
5. Chạy scoped tests.
6. Lưu evidence.
7. Không tự tuyên maturity.
8. Trả lại: commit SHA + tests + limitations + unresolved blockers.

---

## Multi-Agent Orchestration & Independent Verification (BẮT BUỘC — owner directive 2026-09-15/16)

> Nguồn sự thật duy nhất: **Thực tế > mọi báo cáo** (kể cả self-report của worker lẫn nhận định của
> orchestrator). "PASS" chỉ nghĩa là không thấy lỗi trong phạm vi đã đo, KHÔNG phải "xong/an toàn/sẵn sàng".

### Vai trò tách bạch (chống "tự làm rồi tự kiểm")
- **Orchestrator (tôi):** CHỈ điều phối + **verify độc lập** bằng chính lệnh mình chạy (grep/pytest/Docker). KHÔNG tự sửa product inline khi còn worker.
- **Worker:** sửa code trong scope hẹp, **KHÔNG commit/push**, bắt buộc có mục **PHÁT HIỆN MỚI (NEW FINDINGS)** cuối report (file:line, severity, slot đề xuất).
- **Verifier:** agent KHÁC worker, read-only, kiểm lại cả code lẫn bằng chứng. Không có verifier độc lập ⇒ verdict tối đa là `PENDING / CANDIDATE_NOT_PROVEN`, **không** `ACCEPT`.

### Hàng đợi & slot
- **Luôn giữ 2 agent song song**; worker xong ⇒ verifier/worker kế tiếp chiếm slot ngay, không chờ owner nhắc.
- Mỗi việc đi theo chuỗi: `worker → PHÁT HIỆN MỚI → verifier độc lập → commit → merge-if-cần → bằng chứng thật → mới là xong`.
- Không 2 worker đụng cùng file. Khi merge/push gặp branch đã di chuyển: **merge, KHÔNG force-push**.

### Bằng chứng thật (nghiêm cấm "xanh trên giấy")
- Chọn môi trường theo HIỆN TƯỢNG: **Docker isolated** (mặc định) cho service/production/runtime/egress/readiness; **PC native** khi vấn đề thuộc Windows filesystem/ACL/process/UI hoặc Docker không tái hiện.
- Môi trường đã chọn **phải chạy thật** + lưu evidence (archive SHA, image digest, health/readiness, golden request, log, teardown). **Static/unit-only KHÔNG đủ** cho runtime claim.
- Không làm xanh test bằng delete/skip/xfail/deselect/loosen assertion (FA-01). Sửa harness phải giữ hoặc tăng strictness.
- Worker chạy xong nhưng chết trước khi ghi report (captcha/quota/timeout) ⇒ orchestrator **tự verify lại** diff trên đĩa, không tin summary dang dở.
- **Orchestrator không được copy claim của worker vào commit message/report khi chưa tự verify TỪNG claim đó** (bài học thực: message A1 `e33622d` ghi "fix NameError gh_count" nhưng symbol không tồn tại → phải sửa bằng commit follow-up trung thực, KHÔNG amend SHA đã push).

### Hộp thoại duyệt khi owner vắng mặt
- Khi owner đi ngủ/đi vắng: các thao tác đã ủy quyền trong scope (build/run Docker, pytest, commit/push branch, xác nhận cảnh báo kỹ thuật) ⇒ orchestrator **tự Computer-Use bấm xác nhận**, không chặn chờ.
- VẪN DỪNG + hỏi với: destructive (xóa dữ liệu/volume), push `main`/deploy/public, thay đổi credential/quyền — dù đã ủy quyền chung.

---

## Enforcement

Các quy tắc trên được enforce bởi:
- **Tier 1:** `tools/install_git_hooks.py` (pre-commit hook) -> `tools/t00_meta_audit.py`
- **Tier 2:** File này + `.agents/AGENTS.md` § FORBIDDEN ACTIONS (auto-loaded)
- **Tier 3:** `.github/workflows/scp_guardrails.yml` (CI — độc lập khỏi AI)
