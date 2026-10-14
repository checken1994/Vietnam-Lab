# W2 Checker Verdict — independent CHECKER (kc-A..kc-G)

- **Date:** 2026-10-05 (Asia/Ho_Chi_Minh)
- **Head (fix/wave2):** `c91090b95f489c8fae75331388edbad80f71378e` (1 product commit gộp d1–d6)
- **Base:** `f21907f5e7f4c26444eda8bf412d7d4c67836001`
- **Dispatch record:** `reports/scp_acceptance_ci/wave2/dispatch_record.txt`
  — implementer=main-orchestrator-session-scp_f1b3935b; checker=agent_a5d0ca69-a1b8-4419-994d-aa8bc52b52c0.
  Checker không tham gia implement: commit đã tồn tại trên fix/wave2 trước khi checker bắt đầu
  (lệnh đầu tiên của checker là read-only `git rev-parse`/`git log`).
- **Diff scope:** 5 files — `scp/api_server_parts/_ask_impl.py` (1 dòng),
  `scp/ask_kernel_adapter.py` (+17), `scp/runtime/question_router.py` (+181/−28),
  `tests/T02_contract/test_w2_router_contract.py` (mới, 115 dòng),
  `tests/T02_contract/test_w2_fork_relevance.py` (mới, 128 dòng). 415(+)/28(−).

## Bảng kết quả

| Check | Verdict | Evidence | Số liệu chính |
|---|---|---|---|
| kc-A per-commit replay @head | **PASS** | `kc_a_replay.log` | import-path assertion PASS (`...\Temp\scp_w2_ck\scp\__init__.py` — nằm dưới worktree); pytest 3 target: **215 passed, exit 0** (chụp lại bằng `pipefail` sau khi phát hiện lần capture đầu lấy exit của `tee`) |
| kc-B anti-placebo @base | **PASS** | `kc_b_anti_placebo.log` | Head-tests trên code cũ: combined (--continue-on-collection-errors) **exit 1, 5 failed + 1 error**; per-file: fork_relevance **exit 1, 5 failed** (d5×2, d6×3), router_contract **exit 2 ImportError** (`_sanitize_domain_hint` không tồn tại ở base). Không phải exit 4/5. 1 test pass ở base = `test_d5_terms_covered_semantics_preserved` (test bảo toàn semantics của helper `_terms_covered` đã tồn tại ở base — pass cả 2 phiên bản là đúng chủ đích) |
| kc-C T00 wave-delta @base | **PASS** | `kc_c_t00_wavedelta.log` | Pass 1 (đúng lệnh: `git apply --cached`, worktree = base): **exit 0**, "0 new regressions". Pass 2 (mạnh hơn: áp diff cả working tree để FA-01/FA-04 scan đúng nội dung wave + FA-02 collect cả test mới): **exit 0**. BASELINE_DEBT đều là debt historical (TRACK_DEBT) |
| kc-D census diff | **PASS** (1 noqa hit — benign, xem dưới) | `kc_d_census.txt` | skip/xfail/importorskip (kể cả substring rộng): **0**; except trên dòng thêm: **0** (→ không có silent-except mới để duyệt); secrets pattern: **0** trên 415 dòng thêm; **noqa mới: 1 hit** — `# noqa: F401` trên `from scp.ask_kernel_adapter import AskKernelAdapter` (test file fork_relevance). Verdict hit: **benign** — ruff `--select F401` trên bản đã bỏ noqa KHÔNG flag import này (nó được dùng thật qua `AskKernelAdapter._lookup_fork_self_certified`); thứ duy nhất F401 flag là `import pytest` (không dùng) ở cả 2 test file mới — lint debt tiềm ẩn ngoài scope kc-E (E9,F821 trên scp+tools), không che bất kỳ failure/assertion nào |
| kc-E ruff | **PASS** | `kc_e_ruff.log` | `ruff check scp tools --select E9,F821` → "All checks passed!", **exit 0** |
| kc-F maker/checker review | **PASS** (7/7 mục, xem dưới) | chính file này + các log trên | — |
| kc-G goldset | **PASS theo gate** (0.9930 ≥ 0.7) — có 1-case regression phải báo | `kc_g_goldset.log` | Head: **accuracy = 0.9930 (142/143), 3 passed, exit 0**. Base (so sánh do checker tự chạy thêm): **1.0000 (143/143)**. Miss duy nhất: `iso_phys_010` "Điện tích electron? (C)" — xem phân tích dưới |

## kc-F — verdict 7 mục

- **(a) scope-only: ĐẠT.** Diff chỉ chạm 3 file product của pipeline /ask + 2 test file contract mới. Không đụng spec/, tools/, CI, policy, threshold file, goldset (`tests/T07_learning/test_question_router_goldset.py` không nằm trong diff).
- **(b) không nới assertion/threshold: ĐẠT.** Không test cũ nào bị sửa (chỉ thêm 2 file mới); 12 pin trong router_contract + 6 test trong fork_relevance toàn assertion hành vi mới; confidence constants (0.9/0.75/0.5) giữ nguyên; goldset gate không đụng.
- **(c) không fail-closed→fail-open: ĐẠT.**
  - d3: `_l2_failsafe` + `_parse_l2_answer` chuyển `bypass_verdict_pass=True→False` — **TĂNG strictness** (xác nhận: cả 2 site, reason đổi tên `*_fail_closed`).
  - d5: relevance gate `return None` → đã xác minh caller (`scp/ask_kernel_adapter.py` ~:1370): `fork_response is None → response = await handler(req)` (LLM path) và comment/scope code xác nhận answer vẫn qua `finalize/verify` cùng verification/governance path — là chuyển từ deliver-trực-tiếp sang **verify-path**, không phải fail-open. Trường hợp `terms` rỗng: gate bỏ qua nhưng marker `checked=False` → d6 sẽ KHÔNG self-certify → rơi judge đầy đủ (conservative).
  - d6: `_lookup_fork_self_certified` — non-fork trả True (giữ nguyên hành vi), fork thiếu marker → `already_judged=False` → judge đầy đủ: **strict hơn**.
  - d2 (siết `gì\b`→`gì\s*\?`, opinion/creative về CHATBOT lane): là re-classification của câu phi-truth-claim (ý kiến/sáng tác) sang chatbot lane, có pin test; không mở bypass cho truth-claim nào. Residual risk ghi ở phần open items.
- **(d) TTL/cap không bị đụng: ĐẠT.** Diff 0 dòng TTL/cap (hit duy nhất của grep là false-positive "short_circuits" match "circuit" trong tên test d6).
- **(e) pin cap<TTL còn nguyên: ĐẠT.** Các pin nằm ở `tests/T05_gateway/test_gateway_hedge_pool.py`, `tests/T04_kernel/test_pipeline_caps_within_lease_ttl.py` — không nằm trong diff.
- **(f) mọi except mới đã duyệt: ĐẠT (vacuous).** 0 dòng `except` mới trong diff (kc-D). `except Exception as _det_err:` trong `classify_l0` là context line có sẵn từ trước, không đổi.
- **(g) test mới old-fails thật: ĐẠT (từ kc-B).** 5 failed thật (d5×2, d6×3) + ImportError (d1/d2/d4 pin các symbol chỉ tồn tại ở head). Thuộc tính old-fails/new-passes được chứng minh ở đúng cặp SHA base/head.

## Phát hiện cần orchestrator/human biết (không chặn gate hiện hành)

1. **Goldset regression 1 case (iso_phys_010)** — head 0.9930 vs base 1.0000. Đã chẩn đoán gốc: "Điện tích electron? (C)" — base `_domain_hint` trả 'math' (label sai cho câu vật lý) → LOOKUP; head d4 `_sanitize_domain_hint` hạ math→general (không có tín hiệu toán) → L0 None → L2 → test env offline rơi `l2-failsafe` REASONING (bypass=False, fail-closed về hướng verify). Là side-effect có hiểu biết của d4, đúng gate ≥0.7, nhưng là suy giảm coverage L0 cho mẫu này — khuyến nghị follow-up (thêm tín hiệu physics vào `_domain_hint`/guard hoặc chấp nhận như known trade-off của d4).
2. **noqa mới = 1 (khác 0 của chuẩn census):** benign theo bằng chứng ruff (F401 không fire trên dòng đó; import được dùng thật). Kèm lint debt tiềm ẩn: `import pytest` không dùng trong cả 2 test file mới (F401 sẽ fire nếu tests/ được ruff-check với F401 — hiện ngoài scope kc-E).
3. **L4 local warning:** 2 test file mới nằm dưới protected path `tests/T02_contract/` — T00 in warning (non-blocking theo tool; L4 VERIFIED chỉ bởi GitHub server-side ruleset). Cần server-side ruleset xác nhận khi merge.

## Deviation về cách thi hành (trung thực)

- kc-B: kỳ vọng gốc "exit 1 ≥5 failed" — run thô 2 file cho **exit 2** do collection ImportError ngắt toàn bộ; checker chạy thêm `--continue-on-collection-errors` (exit 1, 5 failed + 1 error) và per-file isolation để đo thuộc tính old-fails đầy đủ. Exit 4/5 không xuất hiện.
- kc-C: ngoài pass đúng lệnh (`--cached`), checker chạy thêm pass 2 áp diff vào working tree vì t00 scan FA-01/FA-04 theo **working tree** — pass 1 đơn thuần không scan được nội dung wave mới. Cả 2 pass exit 0.
- kc-A: lần capture exit code đầu dùng `$?` sau pipe `tee` (không phải exit pytest); đã chạy lại với `pipefail` — kết luận không đổi (215 passed, exit 0).

## Verdict tổng

**PASS** — toàn bộ 7 check đạt trong phạm vi đã test: head `c91090b9` replay sạch (215 passed), old-fails/new-passes được chứng minh, T00 wave-delta 0 regression mới, census sạch trừ 1 noqa benign có bằng chứng, ruff sạch, goldset 0.9930 ≥ 0.7. PASS này nghĩa là "không quan sát thấy failure trong scope/evidence trên"; các phát hiện mục "Phát hiện cần biết" (đặc biệt goldset 1-case regression do d4) cần quyết định follow-up của orchestrator/human, không bị checker gỡ khỏi biên.

**Cleanup:** worktree tạm của checker (`scp_w2_ck`, `scp_w2_base`, `scp_w2_t00`) + pytest temproot đã remove sau khi ghi evidence.
