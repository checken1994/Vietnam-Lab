# Wave 16 CHECKER VERDICT — f4 factual-ABSTAIN-delivery + f5 carve-out un-starve

- Checker (độc lập với implementer): agent_954602f8-f1ba-48a2-bc8e-a7f42b7d1d0d
  (re-dispatch — lần trước kết thúc vì credential, không phải lỗi công việc;
  evidence dir khi nhận chỉ có `dispatch_record.txt` → checker này tự chạy
  toàn bộ kc-A..kc-G, không có kc_A cũ để tái sử dụng).
- Dispatch record: `reports/scp_acceptance_ci/wave16/dispatch_record.txt`
  (implementer agent_44c0e656-447c-40ca-b147-36b38124912c; separation: checker
  không tham gia implement, focus BYPASS sweep claim-answer qua moi shape).
- Scope: base `5a0ed49d` → head `8d24e9c7` (fix/wave16), 2 commit:
  a85a77e1 (f4 factual-ABSTAIN-delivery, owner duyệt 1+2),
  8d24e9c7 (f5 carve-out un-starve, domain-based weather/finance + pin W11-f1).
- Ngày kiểm: 2026-10-07. Runner: uv venv CPython 3.12.14, pytest 9.1.1,
  pytest-asyncio 1.3.0, ruff 0.16.1, Windows (theo requirements-dev.txt;
  Python system máy đã hỏng — chạy qua uv-managed interpreter, ghi nhận minh bạch).
- Ràng buộc tuân thủ: KHÔNG push/merge; KHÔNG sửa scp/tests (`git status scp/
  tests/` = sạch sau check); worktree tạm D:\scp_w16chk_{c1,c2,base} đã xoá;
  PoC script đặt ngoài repo (system temp w16chk); không đụng worktree
  session khác (scp_wt_w16a1/a2/base giữ nguyên).

## Kết quả 7 mục check

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | kc-A per-commit replay ×2 (worktree riêng, import-path assertion) | **PASS** | `kc_A_per_commit_replay.txt` — a85a77e1: 50 passed/exit 0 (7 file theo trailer); 8d24e9c7: 89 passed/exit 0 (7 file theo trailer). IMPORT_PATH assert đúng worktree từng commit. |
| 2 | kc-B anti-placebo @ 5a0ed49d (test mới trên code cũ) | **PASS** | `kc_B_anti_placebo_base.txt` — 5 failed, 9 passed, PYTEST_EXIT=1. f4: đúng 3 old-fails (honest-refusal, honest-conv, unlabeled-hole); f5: đúng 2 old-fails (domain-hint weather, finance keyword-miss). Fail đúng root-cause, không phải collection error. |
| 3 | kc-C T00 wave-delta + ruff | **PASS** | `kc_C_t00_ruff.txt` — T00 EXIT 0 "0 new regressions" (debt = FA-01 historical baseline); 2 file test W16 `git cat-file -e` tại base = exit 128 ×2 (delta non-vacuous), 14 nodeids collected; ruff E9,F821 "All checks passed" trên 3 file .py của wave. |
| 4 | kc-D census | **PASS** | `kc_D_census.txt` — 0 skip/xfail/noqa thêm; 0 test xoá; 0 secret (1 match = tên biến `_api_v98_canary_token = None`); `^\+.*except` = **0** → không có except mới cần duyệt. Fix fail-closed mới của f4 là nhánh `elif ... v.verdict == 'ABSTAIN'` hướng withhold, probe độc lập B3/B3b xác nhận bắt đúng. |
| 5 | kc-E BYPASS SWEEP (mục 7) | **FAIL** | `kc_E_bypass_sweep.txt` + `kc_E_bypass_sweep_raw.log` — **4/7 e2e probe BYPASS-LIVE**: claim chưa verify được deliver dưới nhãn `[unverified — abstain]` qua f4/f5. Chi tiết mục 7 dưới. |
| 6 | kc-F W8-e1 interaction | **PASS** | `kc_F_w8e1_interaction.txt` — static: guard (dòng ~697-724) mutate PASS→FAIL/ESCALATE TRƯỚC `_api_final_answer` (~767), trước f4 gate (~845) và nhánh deliver (~907); f4 chỉ nằm trong nhánh `verdict == 'ABSTAIN'` → guard fire thì f4 không thể deliver. Runtime probe: time-signal + PASS + UPHOLD + không evidence → `[W8-e1] ... PASS downgraded to FAIL/ESCALATE` → withheld, không nhãn. Pin replay: W16 pin + test_ask_w8_stale_fact_guard = 5 passed/exit 0. |
| 7 | BYPASS-SWEEP tổng hợp (kc-E) | **FAIL** | 3 bypass-candidate còn sống: BC-E1, BC-E2 (CAO), BC-E3 (design residual) — kèm PoC chạy thật, xem dưới. |

## Mục 7 — BYPASS SWEEP (tổng hợp kc-E; PoC chạy thật trên code head 8d24e9c7, router THẬT)

**(a) Claim-answer ngoài cue set — 3/5 shape ĐI QUA (đúng rủi ro (a) dispatch):**
- Detector FP: `is_honest_abstain_answer` trả **True** cho các CLAIM:
  - `"Hello! The earth revolves around the sun."` — "revolves" ngoài closed
    set `_ABSTAIN_CONV_ASSERTION_RE` (BC-2 residual, lặp lại đúng dạng "fly");
  - `"Xin chào! Trái đất quay quanh mặt trời."` / `"Xin chào! Cổ phiếu FPT
    đang tăng mạnh."` — "tăng"/"đang tăng" ngoài set, "đang" chỉ có ở tầng
    refusal (`_ABSTAIN_ASSERTION_RE`), không có trong `_ABSTAIN_CLAIM_CUE_RE`;
  - `"Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng"` /
    `"I don't have data, but Bitcoin keeps rising"` — refusal-wrapper +
    assertion KHÔNG-copula thoát BC-1 (set chỉ copula là|thì|đang|is|are|...).
- E2E qua `_ask_impl` (real route, judge echo ai_answer, ABSTAIN/ESCALATE):
  4 probe deliver: `"[unverified — abstain] Tôi chưa có dữ liệu, nhưng giá cổ
  phiếu tăng"` (weather domain VÀ general factual — bypass KHÔNG cần domain),
  `"[unverified — abstain] Hello! The earth revolves around the sun."`,
  `"[unverified — abstain] Xin chào! Cổ phiếu FPT đang tăng mạnh."` —
  governance ghi ABSTAIN, verdict ABSTAIN → claim không verify rơi đúng shape
  kernel-accepted (nhãn + governance ABSTAIN) mà f4 comment tuyên bố không thể
  xảy ra ("mọi shape khác = có claim, conservative" — BỊ BẰNG CHỨNG PHỦ ĐỊNH).
- Claim có digit/copula (A1) hoặc assertion-verb đã liệt kê (A2) hoặc không có
  marker (A3) → still withheld đúng thiết kế.
- Blast radius: pre-W16 FP surface giới hạn chatbot + carve-out realtime;
  f4 mở nó ra TOÀN lane FACTUAL → risk (a) của dispatch MATERIALIZED.

**(b) Domain-hint manipulation — PASS:** `"Thời tiết này khiến cổ phiếu tăng"`
route thật → LANE_FACTUAL, domain='finance', reason='lookup_signal:weather_fact'
(cả 2 nhánh allowlist f5 match) + claim thuần `"Thời tiết này khiến cổ phiếu
tăng mạnh."` → WITHHELD ESCALATE. f5 vẫn refusal-only; domain manipulate một
mình không deliver claim (claim + refusal/conv wrapper thì đi qua lỗ (a),
độc lập domain).

**(c) Unlabeled-hole — PASS:** ABSTAIN + governance UPHOLD + claim →
`[SCP: Answer withheld]`; ABSTAIN + governance 'WEIRD' + claim →
`[SCP: Answer withheld]`. Fail-closed catch mới bắt đúng shape lạ, không bắt
nhầm đường hợp lệ (14/14 test W16, 89 test scoped c2 vẫn xanh).

**(d) Pin BC-1 wrapper replay 3× — PASS:** 3/3 passed exit 0 (`kc_E_bc1_pin_replay_3x.txt`).
Lưu ý: pin chỉ phủ biến thể CÓ copula — biến thể không-copula (BC-E1) là lỗ
sống mà pin hiện có không thấy.

## Bypass-candidate còn sống (bắt buộc nêu theo dispatch)

1. **BC-E1 [CAO] Refusal-wrapper + assertion KHÔNG-copula → deliver claim.**
   PoC: question `"Thời tiết Hà Nội hôm nay thế nào?"` (hoặc câu factual bất
   kỳ) + ai_answer `"Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng"`, judge
   ABSTAIN/ESCALATE echo → response.final_answer = `"[unverified — abstain]
   Tôi chưa có dữ liệu, nhưng giá cổ phiếu tăng"`, governance=ABSTAIN.
   Root-cause: `_ABSTAIN_ASSERTION_RE` chỉ copula → BC-1 thoát sau marker
   `chưa có dữ liệu`. Fix gợi ý tại điểm lỗi (product, không phải hạ test):
   thêm assertion phi-copula (tăng|giảm|vượt|rising|falls|hits|...) hoặc
   heuristic mệnh đề theo sau refusal (nhưng/but + S-V) → conservative False.
2. **BC-E2 [CAO] Conv marker + assertion verb ngoài closed set → deliver
   claim.** PoC: `"Xin chào! Cổ phiếu FPT đang tăng mạnh."` /
   `"Hello! The earth revolves around the sun."` → deliver kèm nhãn. Closed
   set đã fail 2 lần (BC-2 "fly", nay "revolves"/"tăng") — khuyến nghị đảo
   chiều logic tầng conv: conv marker chỉ abstain khi phần sau marker KHÔNG
   tạo mệnh đề mới (whitelist động từ không bao giờ đủ).
3. **BC-E3 [THẤP, design residual] W8-e1 chỉ chặn PASS:** time-signal +
   ABSTAIN + honest refusal vẫn deliver nhãn (C2) — chủ ý owner-duyệt, hiện
   an toàn vì refusal thuần; nhưng khi BC-E1/E2 được mở, cùng shape này sẽ
   deliver stale claim — cần fix detector trước khi wave sau mở thêm scope.

## Verdict tổng

**FAIL (chặn merge ở trạng thái hiện tại)** — 6/7 mục check PASS (kc-A..kc-D,
kc-F + các mục phụ (b),(c),(d) của kc-E), nhưng mục 7 BYPASS-SWEEP **FAIL**:
risk (a) của dispatch đã materialize với 4/7 probe e2e — f4 mở deliver
claim-chưa-verify kèm nhãn trên TOÀN lane FACTUAL qua 2 họ detector
false-positive (refusal-wrapper không-copula; conv marker + assertion verb
ngoài closed set). f5 hoạt động đúng phạm vi owner duyệt (refusal-only,
withheld claim thuần) nhưng không chặn được bypass vì bypass ăn FP của
detector dùng chung.

Yêu cầu xử lý tại điểm lỗi (sản phẩm, KHÔNG phải hạ chuẩn test): harden
`is_refusal_abstain_answer` / `is_honest_abstain_answer` (BC-E1, BC-E2) +
thêm pin cho 2 họ shape bypass ở trên (test mới phải FAIL trước fix, PASS sau
fix); sau đó re-run battery W16 + replay scoped trailer.

PASS của từng mục chỉ có nghĩa "không thấy thất bại trong scope test đó"
(DNA #22) — không tuyên bố hệ thống hoàn chỉnh/production-ready.

## Ghi nhận quy trình / giới hạn bằng chứng

- Python system máy kiểm đã hỏng (choco shim + Python312 registry entry mất);
  runner dùng uv-managed CPython 3.12.14 + requirements-dev.txt pin đúng
  pytest 9.1.1/ruff 0.16.1 như wave 13/14 — chênh 3.12.10→3.12.14 patch-level.
- PoC e2e dùng judge mock echo ai_answer (shape thật "judge echo trên đường
  ABSTAIN" như pin W16) + env hermetic; judge LLM thật/crosscheck thật KHÔNG
  nằm trong bằng chứng này — đúng giới hạn của các wave trước.
- Lần chạy kc-B đầu bị `tee` che exit code (checker error, đã chạy lại capture
  trực tiếp → PYTEST_EXIT=1); raw log đầu vẫn giữ để audit.
- Không có bypass probe nào gọi mạng thật; toàn bộ route qua router thật local.
