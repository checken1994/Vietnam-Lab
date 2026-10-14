# W3 Checker Verdict — independent CHECKER (kc-A..kc-G), SECURITY-FOCUSED

- **Date:** 2026-10-05 (Asia/Ho_Chi_Minh)
- **Head (fix/wave3):** `32ac655105400711a7cf845cbe16c1ac76dab2da`
- **Base:** `bc0553714ce7a26b2232aa8795b28594b3160069` (= `main` = `origin/main` tại thời điểm check)
- **4 commit:** e1 `26f24d26` (KILL-semantics), e2 `34b9b1d6` (family-independence), e3 `4e95919a` (identity-pin), e4 `32ac6551` (identity post-guard)
- **Diff scope:** 4 file product (`scp/api_server.py` +7, `scp/api_server_parts/_ask_impl.py` +120, `scp/runtime/judge.py` +54, `scp/runtime/multi_llm_crosscheck.py` +39) + 9 file test (3 file mới e1, 2 file mới e3/e4, 1 file mới T06, 4 file cũ sửa). 952(+)/57(−).
- **Dispatch record:** `reports/scp_acceptance_ci/wave3/dispatch_record.txt` **KHÔNG TỒN TẠI** tại thời điểm checker chạy (đã check 2 lần: đầu phiên và trước khi viết verdict). Wave2 có file này, wave3 chưa. Đây là thiếu sót evidence của orchestrator (không phải của checker); mọi nhận định separation-of-duties dưới đây dựa trên quan sát trực tiếp: commit e1–e4 đã tồn tại trên `fix/wave3` trước khi checker thực hiện lệnh git đầu tiên (read-only `git rev-parse`), checker không tham gia implement, không push/merge, không sửa `scp/tests`.
- **Skill binding (theo AGENTS.md contract):** `scp-dna` (4aada0be4873598dc50c3a7f38d90151429bb5263c511a838ed1cdcb4d594d10) + `scp-reality-verifier` (b72a77da9bbd0c09d3b7816537d31f727d2492e5db046c64824dea5ae21d54c8) + `scp-delta-audit` (1aec4c175ad77ccd8c67d6bcf5dba3c91b0ea28b025b4ad8e066d8d72f98757d).

## Bảng kết quả

| Check | Verdict | Evidence | Số liệu chính |
|---|---|---|---|
| kc-A per-commit replay (worktree riêng từng sha) | **PASS** | `kc_a_replay.log` + `kc_a_e{1,2,3,4}_replay.log` | e1: import-path OK (scp.__file__ nằm dưới worktree) + **74 passed, exit 0**; e2: **19 passed, exit 0**; e3: **2 passed, exit 0**; e4: **10 passed, exit 0** (gồm `test_api_server_rebind_globals.py`) + **REBIND-EXPORT-OK**: `scp.api_server._strip_vendor_identity_claims → scp.api_server_parts._ask_impl`. Exit 4/5 không xuất hiện |
| kc-B anti-placebo @ bc055371 (test của head chạy trên code base) | **PASS** | `kc_b_e{1,2,3,4}_base.log` | e1: **5 failed** (≥3) exit 1; e2: **4 failed** (≥3) exit 1; e3: **2 failed** exit 1; e4: collection ImportError (`_strip_vendor_identity_claims` không tồn tại ở base, exit 2 — không phải 4/5) + probe old-output: base giữ nguyên "phát triển bởi NVIDIA", head thay bằng pin SCP (changed=True). Không có exit 4/5 nào được tính |
| kc-C T00 wave-delta (apply từ bc055371..head) | **PASS** | `kc_c_t00_wavedelta.log` | Pass 1 `git apply --cached` (worktree base): **exit 0**, "0 new regressions". Pass 2 áp diff cả working tree (FA-01/FA-04 scan đúng nội dung wave): **exit 0**. 15 unique BASELINE_DEBT, toàn bộ ghi "historical instances", không file nào của wave3 |
| kc-D census diff | **PASS** | `kc_d_census.txt` + `wave3_full.diff` | skip/xfail strict (`pytest.skip/xfail/mark.skip/mark.xfail/importorskip/skipif`): **0** (1 hit của pattern rộng = tên test `test_postguard_skips_boundary_withheld_artifacts`, là test pin contract "post-guard KHÔNG đụng artifact `[SCP: …`"); **noqa mới: 0**; **except mới: 0** (sweep vacuous — không có except nào để duyệt); **secrets: 0** (2 lớp pattern); TODO/FIXME: 0 |
| kc-E ruff E9,F821 scp+tools | **PASS** | `kc_e_ruff.log` | "All checks passed!", **exit 0** |
| kc-F SECURITY-FOCUSED review | **PASS** (8/8 mục, xem dưới) | chính file này + code walk | — |
| kc-G region regression T05+T02+T06+T04_kernel | **PASS** | `kc_g_region.log` | **814 passed, 23 skipped, exit 0** (309s) — khớp baseline kỳ vọng ~462+352 passed, 23 skipped (baseline debt). Warning duy nhất: PytestCacheWarning `.pytest_cache` WinError 5 (môi trường, không ảnh hưởng kết quả) |

## kc-F — verdict 8 mục

- **(1) scope-only: ĐẠT.** Diff chỉ chạm 4 file product của pipeline /ask + judge + crosscheck, và 9 file test. Không đụng `spec/`, `tools/`, CI, policy, threshold, goldset, TTL/cap config.
- **(2) không nới assertion/threshold: ĐẠT.** Không test nào bị delete/skip/xfail. 4 test cũ sửa (flow_02 +13, flow_03 +8, judge_sync_crosscheck_alive +7, concurrency +5) toàn bộ là (a) cập nhật hợp đồng governance KILL→ESCALATE cho benign verification-FAIL với **assertion fail-closed giữ nguyên** (verdict FAIL + `[SCP: Answer withheld` + withheld content), và (b) stub provider bổ sung `base_url`/`model` để mang đúng shape family e2 (siết realism test, không nới gì).
- **(3) không fail-closed→fail-open: ĐẠT.** Walk từng nhánh governance sau e1 (chi tiết mục 8): mọi nhánh non-PASS đều withheld-or-403. e1 chỉ đổi **nhãn** (KILL→ESCALATE) và **status** (403→200-withheld) cho benign verification-FAIL — nội dung chưa verify vẫn không bao giờ rời boundary. e2 TĂNG strictness (cùng base_url+model = 1 family, không thể tự tạo consensus từ 2 opinion cùng lineage). e3/e4 là add-only pin/guard.
- **(4) TTL/cap không bị đụng: ĐẠT.** Grep diff cho ttl/lease/cap/rate-limit/timeout/deadline: 0 dòng product bị đổi (hit duy nhất là signature stub test `_DeadlineBurningProvider` + tên helper test `_CapturingGateway`). `_crosscheck_max_seconds()` deadline logic trong crosscheck không đổi (chỉ context lines).
- **(5) pin cap<TTL còn nguyên: ĐẠT.** `tests/T04_kernel/test_pipeline_caps_within_lease_ttl.py` không nằm trong diff và PASSED trong kc-G (T04_kernel thuộc 814 passed).
- **(6) mọi except mới đã duyệt: ĐẠT (vacuous).** 0 dòng `except` mới trong 965 dòng thêm (kc-D).
- **(7) harness strictness: ĐẠT.** T00 fail-closed 2/2 pass exit 0 (0 new regressions); anti-placebo chứng minh test mới old-fails thật tại base với đúng thuộc tính (kc-B); không có skip/xfail/noqa mới nào để dùng làm lỗ hổng (kc-D).
- **(8) SECURITY — governance branch walk + independence + e2 bypass: ĐẠT.** Chi tiết đầy đủ dưới đây.

## Mục 8 SECURITY — chi tiết

### 8a. Walk từng nhánh governance trong `_ask_impl` sau e1 (file: `scp/api_server_parts/_ask_impl.py` @ 32ac6551, vùng ~dòng 640–860)

Input: `_gov_decision = v.evidence['governance_decision']` (provenance: judge.py sync `judge()` dòng 410 + async `judge_async()` dòng 546 + escalated path dòng 351; `judge_with_react_fallback` = `judge_async`), `v.verdict`, `_is_chatbot_lane` (route), `_is_true_security_threat`.

| # | Điều kiện | Kết quả | Fail-closed? |
|---|---|---|---|
| 1 | `_is_true_security_threat` = (gov KILL ∨ verdict FLAGGED ∨ multimodal block ∨ lane SECURITY ∨ threat_detected ∨ injection_detected) | `[SCP: Answer withheld]` / `[SCP: Answer withheld — Governance KILL]`, **toàn bộ trường phụ (slm_responses, claims, guard, classification…) bị clear** | ✅ withheld; **KILL path nguyên vẹn** |
| 2 | verdict PASS ∧ (gov rỗng ∨ UNKNOWN) — hoisted, cả 2 lane | `HTTPException 403 "Governance clearance missing — fail-closed"` | ✅ 403 |
| 3 | non-chatbot ∧ gov ESCALATE | `[SCP: Answer withheld — không xác minh được câu trả lời (governance: ESCALATE)]` + clear toàn bộ trường | ✅ 200-withheld |
| 4 | non-chatbot ∧ gov DEGRADED | `[SCP: Answer withheld — governance degraded]` + clear | ✅ 200-withheld |
| 5 | non-chatbot ∧ verdict ∈ (FAIL, FLAGGED, DEGRADED, UNCERTAIN, ESCALATE) | `[SCP: Answer withheld]` + clear | ✅ withheld |
| 6 | non-chatbot ∧ verdict UNKNOWN ∧ why_gate REJECT | `[SCP: Answer withheld — WHY Gate blocked]` + clear | ✅ withheld |
| 7 | chatbot ∧ gov ESCALATE | override withheld-ESCALATE **chạy SAU resolver canned/memory** → mọi answer resolver (cả canned/memory) bị thay bằng withheld | ✅ 200-withheld |
| 8 | chatbot ∧ gov ∈ (KILL, REJECT, DENY, DEGRADED) | `HTTPException 403 "Governance KILL enforced"` | ✅ 403 |
| 9 | chatbot ∧ (gov rỗng ∨ UNKNOWN) | `HTTPException 403 "Governance clearance missing — fail-closed"` (backstop, `_gov_decision='DENY'`) | ✅ 403 |
| 10 | non-chatbot ∧ verdict UNKNOWN (ngoài #6) | nhánh pre-existing giữ nguyên từ base (đã diff-verify) — chỉ đạt được khi gov ≠ ESCALATE; judge không sinh tổ hợp UNKNOWN+UPHOLD (escalated path luôn gắn gov ESCALATE), nên thực tế là dead-code phòng thủ, không phải đường deliver | ✅ không mở đường mới |

**Kết luận:** KHÔNG có nhánh nào mà ESCALATE (hoặc bất kỳ non-UPHOLD nào) deliver answer content chưa verify. Nội dung thật chỉ rời boundary khi gov UPHOLD (tức judge PASS qua 2-LLM crosscheck độc lập) hoặc qua data-API lookup fork (provenance `input_context_only`, pre-existing).

### 8b. Kernel-side fail-closed (pre-existing, verify không bị wave đụng)

`scp/ask_kernel_adapter.py::_safe_response` (không nằm trong diff): mọi response chưa VERIFIED → `final_answer = "[SCP: Answer withheld — evidence not verified: …]"` + `governance_decision = KILL/ESCALATE` + confidence 0. Tức là ngay cả khi lớp trên có lỗ hổng, adapter vẫn withhold trước khi tới boundary.

### 8c. `_is_true_security_threat` vẫn độc lập với judge verdict

Định nghĩa tại head (dòng ~654): `gov=='KILL' ∨ verdict=='FLAGGED' ∨ _multimodal_block ∨ lane==LANE_SECURITY ∨ evidence.threat_detected ∨ evidence.injection_detected`. e1 chỉ thay đổi mapping FAIL→ESCALATE **trong judge**; 4/6 tín hiệu của `_is_true_security_threat` (multimodal block, security lane, threat_detected, injection_detected) **không phụ thuộc judge** và không bị thay đổi. Security threat thật đi đường nào (FLAGGED verdict, security lane, threat/injection evidence) vẫn rơi vào nhánh #1 = withhold đầy đủ. KILL từ judge giờ chỉ đến `_SECURITY_TIER1_TAGS = {REJECT_INTERNAL_MARKER}` (Tier-1 `scp/security/tier1_guard.py` chặn leak marker nội bộ `[SCP:`/`[ESCALATE`/`[KERNEL`) — đúng intent "KILL chỉ cho security-threat thật". Các Tier-1 failure khác (EMPTY/OVERLENGTH/CONTROL_CHARS/GROUNDING/QUESTION_OVERLENGTH) → ESCALATE → withheld (không deliver), tức an toàn dù nhãn mềm hơn: CONTROL_CHARS (bidi/zero-width smuggling) và OVERLENGTH (prompt-stuffing) vẫn bị chặn khỏi deliver, chỉ đổi 403→200-withheld.

### 8d. e2 không mở đường bypass

`cross_verify` @ head: family = `_family_key(provider)` = `base_url|model` (normalized lower/trim/no trailing slash); provider không khai báo → `unknown|unknown` → chung 1 family → không bao giờ đủ 2 family (fail-closed, không phát minh tính độc lập từ tên). Chỉ `len(valid) == 2` opinion từ **2 family khác nhau** mới cho `agree`:
- missing consensus (`len(valid) < 2`) → `consensus='missing_distinct_providers'`, `final=None` → judge: `semantic is None` → `escalated=True` → verdict UNKNOWN + **governance ESCALATE** → nhánh #3/#7 = withheld. KHÔNG deliver.
- disagree → `final=None` → cùng đường escalated → withheld.
- attempts/`distinct_families_attempted` ghi family-key mới → audit-trail quan sát được lineage thật (test pin `test_cross_verify_attempts_record_family_key_format`).
- 2 provider khác PROVIDER_NAME nhưng cùng base_url+model → 1 family → missing consensus → ESCALATE (kết quả nondeterminism q08 bị triệt tiêu bằng cách **siết**, không phải nới).

### 8e. e3/e4 identity pin/post-guard không mở bypass

- e4 `_strip_vendor_identity_claims` chạy SAU MỌI nhánh governance (dòng ~845, trước Milestone 2), chỉ nhận/ trả `str`; answer withheld bắt đầu `[SCP:` → skip nguyên văn (test pin `test_postguard_skips_boundary_withheld_artifacts`). Guard chỉ thay câu vendor-claim bằng pin trung lập — không thể biến withheld thành content, không thể raise verdict.
- Rebind export: `_strip_vendor_identity_claims` được export đúng qua `_ask_impl_part` namespace (`scp/api_server.py` +7 dòng) — khớp comment contract và được pin bởi `tests/T02_contract/test_api_server_rebind_globals.py` (PASSED trong kc-A e4); checker tự verify thêm runtime: `scp.api_server._strip_vendor_identity_claims.__module__ == 'scp.api_server_parts._ask_impl'`.
- Giới hạn che khuất (residual, xem phần phát hiện): pattern post-guard over-match là hướng an toàn (thay nhiều hơn = fail-closed); under-match (vendor không nằm trong `_W3_VENDOR_NAMES`, claim không dạng câu) chỉ là persona-leak residual, KHÔNG phải đường deliver nội dung chưa verify.

## Phát hiện cần orchestrator/human biết (không chặn gate hiện hành)

1. **`dispatch_record.txt` wave3 KHÔNG tồn tại** (orchestrator được giao ghi trước khi checker chạy; wave1/2 có). Mọi gate evidence cần file này — yêu cầu orchestrator bổ sung, ghi rõ agent-id 2 phía.
2. **Residual e2 (đã ghi trong docstring của `_family_key`):** OpenRouterProvider free-fallback per-task — family key quyết định theo primary model trước khi biết model thật serve; fallback thực tế có thể làm 2 "family" khác key nhưng cùng lineage. Đã biết từ trước, không giải được bằng pre-flight metadata; wave3 vẫn strict hơn base (name-key).
3. **Residual e4 coverage:** `_W3_VENDOR_NAMES` liệt kê 13 vendor (thiếu vd. Hugging Face, Moonshot, Z.ai/GLM, Inflection…); regex `by\s+VENDOR` khá rộng (over-match an toàn). Post-guard là lưới thứ 2 — lưới thứ nhất là identity pin trong system prompt vi+en. Đây là persona-leak residual, không phải governance bypass.
4. **Nhánh #10 (non-chatbot verdict UNKNOWN ngoài WHY-gate):** pre-existing, giữ nguyên từ base (diff-verified); với provenance judge hiện tại là dead-code phòng thủ, không phải đường deliver.
5. **Môi trường checker:** worktree thiếu `.env` (untracked) làm flow_02/03 fail `ConfigContractError` khi replay — đã copy `.env` vào worktree tạm (không đọc/in nội dung), dọn sạch sau. `.pytest_cache` ở D:\scp bị WinError 5 (junction hỏng từ process khác) — dùng `--basetemp` riêng.

## Verdict tổng

**PASS (trong scope đã test)** — 7/7 check kỹ thuật PASS + mục 8 SECURITY PASS: sau e1 không còn nhánh nào ESCALATE mà deliver answer content; security KILL path (Tier-1 internal-marker + 4 tín hiệu độc lập) nguyên vẹn; e2 siết độc lập consensus theo (base_url, model) và missing-consensus dẫn về withheld; e3/e4 là pin+guard add-only với rebind export đúng contract. PASS này nghĩa là "không quan sát thấy failure/fail-open trong scope/evidence trên"; các residual ở phần "Phát hiện" cần quyết định follow-up của orchestrator/human, không bị checker gỡ khỏi biên. Điểm hành chính cần khắc phục: **dispatch_record.txt wave3 còn thiếu**.

## Cleanup

Worktree tạm checker đã remove: `w3_ck_e1`, `w3_ck_e2`, `w3_ck_e3`, `w3_ck_e4`, `w3_ck_base`, `w3_ck_t00` (+ `.env` copy bên trong, pytest basetemp `D:\scp\.pytest_tmp_w3ck`, file scratch `/tmp/w3_ask_impl_{head,base}.py`). Không push/merge; `scp/tests` không bị sửa; branch `fix/wave3` giữ nguyên tại `32ac6551`.
