# W7 Checker Verdict — independent CHECKER (kc-A..kc-G), SECURITY-ABSTAIN focus

- **Date:** 2026-10-05 (Asia/Ho_Chi_Minh)
- **Head (fix/wave7):** `e836bb69e16e69cf3d382a69809c96a190f403f0`
- **Base:** `feba681e` — 3 commit: e5 `df1b73e8` (judge 3-state ABSTAIN), e6 `0f63c589` (benign-ABSTAIN-delivery + adapter clearance), e7 `e836bb69` (family-2 judge model, .env.example)
- **Diff scope:** 7 file — 4 product (`scp/runtime/judge.py` +241/−17, `scp/api_server_parts/_ask_impl.py` +48, `scp/ask_kernel_adapter.py` +23/−3, `.env.example` +3) + 3 test file MỚI (T06 298 dòng, T02 283 dòng, T05 135 dòng). 1011(+)/20(−). Không file test cũ nào bị sửa/xóa.
- **Dispatch record:** `reports/scp_acceptance_ci/wave7/dispatch_record.txt` đã được orchestrator ghi TRƯỚC khi checker chạy lệnh git đầu tiên; ghi rõ implementer `agent_ecc6fed2…` vs checker `agent_ef3f0d3a…`, separation-of-duties + owner đã duyệt Option A + Option C-lite. Checker không tham gia implement, không push/merge, không sửa `scp/tests`.
- **Skill binding (AGENTS.md contract):** `scp-dna` (sha256 `4aada0be…d94d10`) + `scp-reality-verifier` (`b72a77da…d54c8`) + `scp-delta-audit` (`1aec4c17…f98757d`) — probe bypass chạy theo evidence-hierarchy cấp 1 (executable reproduction).
- **Checker worktrees (đã dọn sau check):** `%TEMP%\w7_ck\wt_{e5,e6,e7,base}` — mỗi SHA một worktree; import-path assertion qua plugin `pathcheck_plugin.py` (assert `scp.__file__` nằm dưới worktree — chống nhiễm `scp-v4` site-packages / checkout chính `D:\scp`).

## Bảng kết quả

| Check | Verdict | Evidence | Số liệu chính |
|---|---|---|---|
| kc-A per-commit replay ×3 (worktree riêng từng sha, import-path assertion) | **PASS** | `kc_a_e{5,6,7}_replay.log` | e5: IMPORT-PATH OK + **12 passed, exit 0**; e6: IMPORT-PATH OK + **8 passed, exit 0**; e7: IMPORT-PATH OK + **4 passed, exit 0** (lần chạy đầu exit 1 do crash cleanup temp của pytest — WinError 5 SAU khi 4/4 PASSED; chạy lại với `--basetemp` riêng → exit 0 sạch; artifact môi trường, không phải test). Exit 4/5 không xuất hiện. Tổng 24 test scoped |
| kc-B anti-placebo @ feba681e (test của head chạy trên code cũ) | **PASS** (e5/e6) + **DEVIATION e7** | `kc_b_e{5,6,7}_base.log`, `kc_b_e5_probe_{base,head}.log` | e5 file: collection **ImportError** `cannot import name 'is_honest_abstain_answer' from 'scp.runtime.judge'` — exit 2 (≠4/5), scp path nằm trong worktree base (import-path OK) + probe checker: base **3 passed** (refusal/greeting = FAIL old) ↔ head e5 **3 failed** (ABSTAIN new) — old-fails/new-passes có thuộc tính test-level; e6 file: **4 failed / 4 passed, exit 1** — failed = đúng 4 test delivery/clearance (`test_chatbot_abstain_delivers…`, `test_realtime_no_tool_refusal…`, `test_adapter_accepts_labeled_abstain_delivery_chatbot_lane`, `test_adapter_accepts_labeled_abstain_realtime_factual_lane`), ≥3 ✓; e7 file: **4 passed, exit 0** — **KHÔNG fail tại base** (xem DEVIATION-D1 dưới) |
| kc-C T00 wave-delta (apply --cached feba681e..head) | **PASS** | `kc_c_t00_wavedelta_pass{1,2}.log` | Pass 1 (index-only, worktree base): **exit 0**, "All integrity checks passed (0 new regressions)"; Pass 2 (áp cả working tree): **exit 0**. 16 DEBT toàn historical (0 file w7/abstain/family2). L4 warning (local-only): 3 test file mới nằm protected path — T00 tự ghi "VERIFIED only by GitHub Server-Side Ruleset" |
| kc-D census diff | **PASS** | `kc_d_census.txt` + `wave7_full.diff` | skip/xfail strict **0** (broad sweep cũng 0); noqa mới **0**; `^\+.*except` **0** (sweep vacuous — không có except mới nào để duyệt); secrets 2 lớp **0**; TODO/FIXME **0** |
| kc-E ruff E9,F821 scp+tools | **PASS** | `kc_e_ruff.log` | "All checks passed!", exit 0 |
| kc-F SECURITY VERDICT (8 mục) | **PARTIAL — 7/7 chuẩn PASS, mục 8b có bypass xác nhận** | chính file này + `kc_f_bypass_probe.txt` | Fail-open KHÔNG được mở (mọi deliver non-PASS đều có nhãn + pair adapter); NHƯNG detector e5 bị bypass bởi claim-riding-refusal (chi tiết mục 8) |
| kc-G region regression T05+T06+T02+T04_kernel | **PASS** | `kc_g_region.log` | **838 passed, 23 skipped, exit 0** (301.9s) — khớp chính xác baseline kỳ vọng ~486+352 passed / 23 skipped (486+352=838; cùng 23 skipped baseline-debt của wave3). Warning duy nhất: môi trường pytest temp (WinError 5 cleanup lần chạy đầu — đã rerun với `--basetemp` ngoài repo, exit 0 sạch). 24 test wave7 (12 e5 + 8 e6 + 4 e7) nằm trong 838 |

## DEVIATION-D1 — kỳ vọng orchestrator cho e7 không khớp codebase

Orchestrator kỳ vọng kc-B: "e7 file failed (knob chưa tồn tại = fail đúng)". Thực tế: `OPENROUTER_MODEL_JUDGE_PRIMARY` **đã tồn tại tại base** (`git show feba681e:scp/llm_gateway/client.py` dòng 493–494, y hệt head) — e7 không đổi code nào, chỉ thêm 3 dòng comment/guidance vào `.env.example` + 1 test file pin hành vi có sẵn. Do đó e7 test battery (4 test) **PASS tại base** → không discriminating. Đây KHÔNG phải placebo của code (không có code change), nhưng hệ quả: (1) toàn bộ "evidence" cho hiệu quả e7 (judge primary = deepseek ≠ nemotron) là live-config probe trên máy owner (`.env` gitignored) — **checker KHÔNG thể xác minh độc lập**; (2) nếu muốn anti-placebo thật cho e7, cần test pin tạo khác biệt tại base (hiện không có). Đánh giá: rủi ro thấp (config-only, rollback = xóa 1 dòng .env), nhưng evidence wave7 cho e7 chỉ đạt cấp A (static) theo scp-reality-verifier.

## kc-F — SECURITY VERDICT, 8 mục

- **(1) scope-only: ĐẠT.** Diff chỉ chạm judge + `_ask_impl` + kernel adapter + `.env.example` + 3 test file mới. Không đụng policy/threshold/goldset/tools/CI/policy-as-code.
- **(2) assertion không bị nới: ĐẠT.** 0 test cũ bị sửa/xóa (cả 3 test file là new-file). Test mới chứa pin fail-closed: `test_factual_abstain_stays_withheld_no_delivery`, `test_realtime_claim_answer_never_gets_abstain_delivery`, `test_adapter_rejects_unlabeled_abstain_fail_closed`, `test_sync_judge_factual_claim_answer_keeps_fail_not_abstain`, `test_sync_judge_security_marker_answer_never_abstain`, `test_sync_judge_empty_answer_still_fail_not_abstain`, `test_sync_judge_degraded_path_not_reclassified_abstain`, `test_chatbot_kill_path_unchanged_withhold_kill_label`.
- **(3) không fail-closed→fail-open: ĐẠT (mục tiêu chính của wave).** Walk nhánh: mọi verdict non-PASS trên factual thường vẫn withheld (ESCALATE/DEGRADED/FAIL/FLAGGED/UNCERTAIN/WHY-Gate); nhánh deliver mới (e6) chỉ đạt khi ĐỦ: verdict ABSTAIN ∧ ¬mt_suspicious ∧ (chatbot ∨ carve-out realtime-no-tool) ∧ answer non-empty ∧ ¬'User Safety:' ∧ ≠'safe', và LUÔN gắn nhãn `[unverified — abstain]` + `_gov_decision='ABSTAIN'` + logger marker. Không có đường nào deliver nội dung unverified KHÔNG nhãn (probe + test adapter xác nhận shape không nhãn → CONTRADICTED).
- **(4) TTL/cap untouched: ĐẠT.** 0 dòng product diff chứa ttl/lease/rate-limit/deadline/max_seconds (2 hit duy nhất là docstring test nhắc "hedge/deadline W1 … giữ nguyên"). `_crosscheck_max_seconds` không nằm trong diff.
- **(5) pin cap<TTL nguyên vẹn: ĐẠT.** `tests/T04_kernel/test_pipeline_caps_within_lease_ttl.py` không nằm trong diff và thuộc kc-G (T04_kernel).
- **(6) except sweep: ĐẠT (vacuous).** 0 dòng `except` mới trong 1011 dòng thêm — không có gì để duyệt.
- **(7) harness strictness: ĐẠT.** T00 0 new regressions ×2 pass; anti-placebo old-fails thật tại base (e5 ImportError+probe 3/3 flipped, e6 4 failed); không có skip/xfail/noqa mới để làm lỗ hổng; không hạ threshold nào.

## Mục 8 — SECURITY-ABSTAIN chi tiết

### 8a. Walk các nhánh delivery trong `_ask_impl` sau e6 — ĐẠT

Thứ tự branch (file `scp/api_server_parts/_ask_impl.py` @ head, vùng 654–890): `_is_true_security_threat` (654) → carve-out flag (670) → **KILL/security-withheld (689)** → 403 SEC-R2-02 gov-missing/UNKNOWN (722) → **[MỚI e6] ABSTAIN-deliver (726)** → factual ESCALATE-withhold (754) → DEGRADED-withhold (775) → FAIL/FLAGGED/…-withhold (794) → WHY-Gate (814) → chatbot resolver + ESCALATE-withhold (860) / KILL-403 (872) / clearance-missing-403 (881).

ABSTAIN-delivery CHỈ xảy ra khi: lane CHATBOT (không cần refusal — Option A thiết kế) **hoặc** carve-out `LANE_FACTUAL ∧ reason∈{weather_fact,finance_fact} ∧ ¬web_fallback ∧ ¬has_provided_evidence ∧ ¬retrieval_triggered ∧ is_refusal_abstain(_ai_answer)`; kèm ¬mt_suspicious, answer non-empty/¬'User Safety:'/≠'safe'. Carve-out không thể kích khi có evidence/tool/data nào đã trả dữ liệu (cả 3 cờ phải False). KILL + FLAGGED + security-lane + threat/injection được xử lý TRƯỚC nhánh e6 → không nuốt. Governance ABSTAIN chỉ được mint trong nhánh deliver này (judge luôn trả ESCALATE cho ABSTAIN) → adapter pair (verdict ABSTAIN + governance ABSTAIN + nhãn) chỉ tồn tại qua đường đã gắn nhãn.

### 8b. Factual claim + crosscheck FAIL → có bypass được ABSTAIN không? — **CÓ (bypass xác nhận bằng probe thực thi)**

Hợp đồng e5: "answer chứa factual claim giữ nguyên đường PASS/FAIL". Probe (`kc_f_bypass_probe.txt`, chạy trên head trong worktree tạm):

| # | Bypass candidate | Kết quả probe | Mức độ |
|---|---|---|---|
| **BC-1** | Refusal marker + claim, không digits/URL — tier-1 `is_refusal_abstain_answer` KHÔNG có claim-cue guard (chỉ chặn URL + chữ số). `"Tôi không thể xác minh — Donald Trump là tổng thống Mỹ."`, `"…trời đang mưa tại Hà Nội."`, `"I do not have data on that, but the president is Donald Trump."` → **True** cả 3 | Detector **True** ×3; judge + crosscheck agree-FAIL → **verdict ABSTAIN (thay FAIL)**; **end-to-end carve-out weather → deliver 200 `[unverified — abstain] Tôi không thể tra cứu thời tiết — trời đang mưa tại Hà Nội.`** — claim "trời đang mưa tại Hà Nội" đi tới user (có nhãn) | **CAO nhất** — phủ trực tiếp hợp đồng "claim không lọt ABSTAIN"; đây đúng điểm rủi ro chính của Option A |
| **BC-2** | Conv marker + claim không cue/digit/URL — cue regex là closed-set: `"Hello, birds fly south in winter."`, `"Hello! Water means life."` → `is_honest_abstain_answer` **True** | Detector **True** ×2; judge agree-FAIL → ABSTAIN (bỏ FAIL). Delivery chỉ qua chatbot-lane (labeled) | Trung bình — và là IncBin của BC-1: mọi copula/động từ khẳng định ngoài danh sách cue đều lọt |
| **BC-3** | `multi_llm_disagreement` + answer abstain-shaped → ABSTAIN thay UNKNOWN — code escalated-path KHÔNG loại disagreement (commit message nói "disagree giữ nguyên UNKNOWN"; pin test chỉ dùng claim-shape nên không bắt được) | Probe: refusal + DISAGREE → **verdict ABSTAIN**, failures có `multi_llm_disagreement` | Thấp-vừa — lệch contract đã công bố; impact bó trong nhãn + lane |
| **BC-4** | `missing_distinct_providers` + claim answer → ABSTAIN (design, được pin bởi chính `test_sync_judge_consensus_missing_benign_verdict_abstain`) — mâu thuẫn với docstring "claim → giữ nguyên PASS/FAIL" ở đường escalated | Probe: claim + missing → ABSTAIN, reasons `[answer_without_verifiable_claim, crosscheck_consensus_missing]` | By-design (owner duyệt) nhưng cần ghi nhận: docstring e5 khai báo conservative hơn code thật |
| BC-5 | ABSTAIN+UPHOLD giả định (mock) → factual rơi qua mọi nhánh withhold (ABSTAIN không nằm trong list) tới deliver cuối | Không sinh được từ judge thật (cả 2 đường ABSTAIN đều ESCALATE); nếu có, kernel vẫn chặn (`verdict_pass` non-chatbot = PASS hoặc labeled-pair) | Không thực tế — defense-in-depth giữ |
| BC-6 | Nhãn em-dash lệch encoding giữa `_ask_impl` (U+2014) và `adapter.startswith` | Byte-compare `cat -A`: cả 2 = `M-bM-^@M-^T` (E2 80 94) | Không rủi ro; lệch encoding → fail-closed (chiều an toàn) |

**Kết luận 8b:** Cổng CHỐNG fail-open (deliver unverified KHÔNG nhãn) **giữ nguyên** — probe boundary chỉ deliver được shape CÓ nhãn, factual thường + ABSTAIN vẫn withheld, adapter từ chối shape không nhãn. NHƯNG hợp đồng detector "factual claim không lọt ABSTAIN" **bị phủ định cho shape claim-riding-refusal (BC-1) và claim-ngoài-cue (BC-2)**: crosscheck agree-FAIL trên answer đó bị reclassify ABSTAIN, và trên carve-out realtime (hoặc chatbot) nội dung claim được deliver SAU nhãn. Option A thực tế rộng hơn scopes "answer-benign-không-có-factual-claim" mà owner duyệt.

### 8c. KILL path + `_is_true_security_threat` — ĐẠT

Block `_is_true_security_threat` (654–661) chỉ là context lines trong diff (không đổi); KILL→`'[SCP: Answer withheld — Governance KILL]'` và chatbot KILL→403 không nằm trong diff; `test_chatbot_kill_path_unchanged_withhold_kill_label` PASSED (kc-A e6). Judge: security tier-1 (REJECT_INTERNAL_MARKER→FAIL+KILL) đứng TRƯỚC nhánh ABSTAIN ở cả 2 đường sync/async — `test_sync_judge_security_marker_answer_never_abstain` PASSED.

### 8d. Kernel adapter clearance fail-closed khi thiếu nhãn — ĐẠT

`_abstain_delivery` = AND của 3 điều kiện (verdict ABSTAIN ∧ governance ABSTAIN ∧ `answer.startswith("[unverified — abstain]")`). Thiếu nhãn → `judge_pass=False` (chatbot: ESCALATE ∉ {UPHOLD,ALLOW}) + `verdict_pass=False` (non-chatbot) + `governance_uphold=False` → CONTRADICTED. `test_adapter_rejects_unlabeled_abstain_fail_closed` PASSED; tại base 4 test clearance FAIL đúng như old-fails.

### 8e. `.env.example` không chứa credential thật — ĐẠT

3 dòng thêm chỉ là comment guidance + tên model public (`deepseek/deepseek-v4-flash-0731:free`) — không key/token. Secrets census 2 lớp trên toàn diff: 0 hit. `.env` (gitignored, máy owner) không nằm trong diff — giá trị cấu hình thật của e7 ngoài tầm kiểm chứng độc lập (xem DEVIATION-D1).

## Verdict tổng

**CONDITIONAL PASS — không đủ điều kiện claim "clean release".**

- 7/7 check kỹ thuật PASS (kc-A, kc-C, kc-D, kc-E sạch; kc-G **838 passed / 23 skipped / exit 0** khớp baseline 486+352; kc-B PASS cho e5/e6 với 1 deviation ghi nhận ở e7).
- kc-F: 7 mục chuẩn PASS — cơ chế fail-closed của wave7 (mục tiêu "ABSTAIN-delivery KHÔNG mở fail-open") **được chứng minh giữ nguyên**: không tồn tại đường deliver unverified-không-nhãn.
- **Finding F-W7-01 (mục 8b, BC-1/BC-2):** ABSTAIN detector của e5 có bypass xác nhận bằng probe thực thi — claim-riding-refusal đi qua detector, biến crosscheck-FAIL thành ABSTAIN, và được deliver (có nhãn) qua carve-out realtime/chatbot. Không phải fail-open theo định nghĩa nhãn, nhưng vượt scope Option A đã duyệt. **Cần quyết định owner/L4:** (i) chấp nhận residual (nhãn là control duy nhất), hoặc (ii) harden detector trước release — gợi ý: thêm claim-cue guard (regex copula/entity) vào tier-1 refusal, hoặc loại `multi_llm_disagreement` khỏi đường ABSTAIN (BC-3), hoặc thu hẹp carve-out thành whitelist refusal template.
- **Evidence limits:** e7 chỉ có static evidence (DEVIATION-D1); live-config deepseek probe là claim của implementer, không kiểm chứng được từ máy checker;kc-G chạy trên máy Windows checker (1 lần, 1 profile) — PASS_WITHIN_SCOPE; không test nào chạy LLM thật/crosscheck thật ngoài mạng.

## Open questions (còn lại sau check)

0. **W-02 (quan sát trong lúc check):** working tree chính `D:\scp` KHÔNG đồng nhất head — có 1 chỉnh sửa CHƯA commit xuất hiện trong phiên check (mtime 2026-10-06 00:10:11 +0700, không phải do checker thực hiện): `tests/T03_capability/test_flow_10_streaming_scp_standard.py` nới `JUDGE_VERDICT_VOCABULARY` thêm `"ABSTAIN"` (comment "W7 Option A 2026-10-06 — owner duyệt"). Checker KHÔNG revert (bảo toàn thay đổi của người khác). Phạm vi ảnh hưởng: KHÔNG nằm trong 3 commit đã audit; KHÔNG thuộc region kc-G (T03_capability không được thu thập trong 838) → mọi số liệu trên vẫn hợp lệ cho committed head `e836bb69`. NHƯNG: (i) đây là sửa test-cũ-lớp-governance nằm NGOÀI battery wave7 (không qua T00/census của checker), (ii) mọi release evidence lấy từ working tree chính phải trừ delta này. Nên commit riêng/đưa vào wave tiếp theo với kiểm kê đầy đủ.

1. BC-1 trên lane CHATBOT với route misclassification (câu factual bị route vào chatbot) — scope Delivery thực tế rộng cỡ nào phụ thuộc tỉ lệ misroute của `route_question` (chưa đo trong wave này).
2. `abstain_reasons` chỉ là marker audit additive — chưa có consumer/alerter nào đọc nó để phát BC-1 về sau (chưa kiểm có dashboard/log-parse nào không).
3. V103/V98/V104 đánh giá claim-shape sau nhãn — multi-turn tracker có dạy lại về pattern "refusal+claim lặp lại" không (chưa kiểm).
