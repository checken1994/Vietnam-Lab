# GA — SCP Session Authority + Current Handoff

> Bootstrap duy nhất cho phiên SCP. Câu gọi chuẩn: **“Đọc GA trên main, refresh GitHub live rồi tiếp tục SCP.”**
>
> Chat/memory chỉ hỗ trợ continuity. Repo + Reality evidence trên đúng SHA là source of truth.

> [!IMPORTANT]
> **BẮT BUỘC SAU KHI ĐỌC FILE NÀY:** Bất kỳ AI nào (Gemini, Claude, ChatGPT, hoặc bất kỳ model nào khác) đều phải **tải thêm 2 file sau bằng cách gọi tool `view_file`** trước khi bắt đầu làm việc:
> 1. `.agents/GEMINI.md` — Tóm tắt toàn bộ FA rules + Skills SCP
> 2. `.agents/AGENTS.md` — Chi tiết đầy đủ FA-01 đến FA-13 (nguồn chuẩn)
>
> **Lý do:** `GEMINI.md` chỉ tự động nạp trên surface Antigravity/Gemini. Các AI khác (Claude, ChatGPT...) phải tải thủ công. File này (GA.md) là file DUY NHẤT mọi AI đều đọc — đây là điểm truyền bá luật FA đến toàn bộ hệ thống.
> **"Tải" = gọi tool `view_file`. Dùng training memory thay thế = Vi phạm.**

---

# A. SESSION RULES

## A1. Authority order

Khi mâu thuẫn, ưu tiên:

1. Chỉ thị trực tiếp mới nhất của người dùng.
2. `AGENTS.md`.
3. `.agents/skills/scp-dna/SKILL.md` + DNA principles.
4. `spec/complete_scp_reference.yaml` + `spec/protected_invariants.yaml`.
5. Skill chuyên biệt phù hợp task.
6. `spec/scp_future_target_manifest.yaml` + effective target spec mà manifest compose.
7. Implementation/test/release bindings machine-readable.
8. Live Git + runtime/test/evidence trên đúng SHA.
9. CURRENT HANDOFF này.
10. Chat/model memory.

Target requirement không biến mất vì implementation thiếu; target spec cũng không phải runtime proof. **Reality > Model.**

## A2. Effective SCP Future Target

Không đọc `spec/scp_future_cause_effect_matrix.yaml` riêng lẻ như baseline hiện hành.

```text
spec/scp_future_target_manifest.yaml
  -> base: spec/scp_future_cause_effect_matrix.yaml @ 4.0.1
  -> overlay: spec/scp_future_cause_effect_matrix_v4_0_2.overlay.json
  -> effective SCP Future Target Architecture 4.0.2
```

Baseline effective theo live manifest:

```text
12 core systems
8 cross-cutting systems
5 phases P0-P4
12 gates T00-T11
138 capabilities
67 cause-effect edges
34 global invariants
13 normative SCP Skills
```

Target 4.0.2 là baseline BUILD, không phải bằng chứng SCP runtime đã hoàn thiện. Tên commit có thể mang nhãn `4.0.3`; authority revision hiện hành vẫn là giá trị machine-readable trong manifest/T00.

## A3. Bootstrap cho substantial SCP task

1. Đọc `GA.md` live trên `main`.
2. Đọc `AGENTS.md`.
3. Đọc SCP DNA Skill/principles.
4. Đọc Complete Reference + protected invariants khi liên quan authority/evidence/governance.
5. Nếu liên quan architecture/coverage, compose target theo manifest hiện hành.
6. Đọc Skill chuyên biệt đúng dependency cone; không nạp cả Skill pack nếu không cần.
7. Refresh live `main` ngay trước analysis quan trọng và ngay trước mutation.
8. Nếu HEAD khác `work_snapshot_sha`, đọc commit/diff chen ngang trước khi kế thừa.
9. Test/evidence claim phải gắn đúng scope/SHA.

## A4. Engineering invariants

```text
Reality > Model
PASS != TRUE
UNKNOWN != VERIFIED
Consensus != Truth
independent lineage required
fail-closed khi thiếu authority/evidence quan trọng
same-SHA evidence cho mandatory verification/release claim
không delete/skip/xfail hoặc hạ assertion/security/mutation/acceptance threshold để manufacture green
không đổi fail-closed thành fail-open để pass
không blind-retry uncertain external side effect; reconcile first
small + reversible + observable changes
max_cost_usd=0; unknown/stale price=DENY; paid_fallback=false
External Data -> Evidence -> Epistemic Assessment -> Knowledge
Knowledge/Reasoning/Risk -> Proposal -> Governance -> Execution
sandbox/browser/workspace/process state phải task-scoped; không reuse state bẩn/cross-task
open port/stale log != service readiness
latency optimization không được bỏ policy/capability/revocation/egress/verifier/recovery/high-risk approval
gateway failover không được hạ privacy/zero-cost/epistemic/retry safety
Skill count/index tự khai chỉ là giả thuyết; recount khi pack đổi
stale lease/worker không được commit task/idempotency state
recovery authority không được vô tình cấp lại quyền cho stale worker
RiskAuthority không được gọi Tool trực tiếp; containment phải qua CapabilityAuthority
prediction/forecast không được tự ghi thành OBSERVED
Golden task/test không được khai như production subsystem implementation
```

## A5. Test/evidence semantics

Traceability đích:

```text
target capability/edge
  -> T00-T11 gate
  -> concrete test
  -> required evidence A/B/C/D
  -> same-SHA evidence khi mandatory
```

`spec/scp_target_test_coverage.yaml` chỉ lưu explicit claims. Effective universe hiện là **138 capabilities + 67 edges**; target chưa bind phải hiện `UNPROVEN`, không được bỏ khỏi report.

Test tồn tại không tự thành Reality proof. `TEST_BOUND_PARTIAL`/`TEST_BOUND_CONTRACT` khác `EVIDENCE_VERIFIED`. `BLOCKED_MISSING_IMPLEMENTATION` biểu thị target bắt buộc nhưng product chưa có implementation; claim dạng này hợp lệ khi không có `concrete_tests`. `EVIDENCE_VERIFIED` cần scope + evidence level + snapshot SHA + evidence refs đúng contract.

Failure classes dùng thống nhất:

```text
HARNESS_BROKEN
PRODUCT_BLOCKED
PRODUCT_FAIL
STRUCTURAL_COVERAGE_GAP
TEST_COVERAGE_UNPROVEN
BLOCKED
```

## A6. Bounded verification / chống audit vô hạn

Target architecture hiện hành đã freeze làm baseline BUILD. Không mở lại chỉ để audit thêm hoặc tạo validator của validator.

Chỉ reopen khi có trigger thật:
- user đổi architecture requirement;
- DNA/normative Skill đổi;
- Reality phát hiện missing piece/contradiction;
- implementation chứng minh target contract không dung hòa;
- security/recovery evidence phủ định assumption kiến trúc.

Một verification round có finite scope + budget + exit condition. Hết budget/evidence => UNKNOWN/BLOCKED, không forced PASS và không loop vô hạn.

## A7. Main synchronization

Chính sách hiện tại: **đồng bộ relevant Reality-checked work lên `main` để AI khác kiểm tra cùng trạng thái**.

`main` là coordination source, không tự động là release proof. Trước write luôn refresh live HEAD; nếu AI khác đã commit thì reconcile/fast-forward, không overwrite concurrent work.

Một SHA chỉ DONE khi toàn bộ mandatory gate PASS trên chính SHA đó và blocker=0. Release/customer handoff còn cần full-system verification mới trên resulting `main` SHA.

---

# B. CURRENT HANDOFF

## B1. Snapshot

```text
project: SCP / GA-LAB
repository: checken1994/GA-LAB
active_sync_branch: audit/runtime-guard-AUDIT-20260909 -> main (đã sync; PR #39 MERGED)
work_snapshot_sha: 617a425 (commit sản phẩm cuối trước handoff này)
snapshot_role: campaign 100% + compliance round + EE-G1 egress closed — 14 mạch closure (M01-M14) + Track A security + Track B fail-loudly/logging (545 silent-except + 82 print) + Track C adoption (C1 Postgres, C2 event bus, C3 Sandbox Evaluator) + Track D (Playwright, MCP, evals) + independent witness W2 (real API cluster); HIGH 190->0
active_target_revision: 4.0.2
baseline_status: ACTIVE_BASELINE_FOR_BUILD
runtime/release_verdict: BLOCKED_PENDING_SAME_SHA_GITHUB_GATES
```

Campaign 2026-09-10→13 (đọc trước khi làm tiếp): 14 mạch flow map V4 + 3 adoption track
C1/C2/C3 đều CLOSED_WITH_KNOWN_GAP (pins trong STATUS-LEDGER). Product fail thật đã fix
qua probe runtime (stream chết 100%, WHY loop chưa wire, v106 no-auth, prediction 503
vĩnh viễn, kernel mutation trước authz FA-05, judge dict-contract, cryptography fail-open
plaintext ĐANG SỐNG, crosscheck chết...). B1 fail-loudly: 545 silent-except + 82 print→logging.
EE-G1 egress ĐÓNG: `enforce_egress_policy()` choke point + static gate + container proof
đảo ngược falsification M13 (deny chặn example.com thật). Track D: Playwright backend
opt-in (anti-honeypot giữ), MCP stdio server qua PEP (FA-05 giữ), LiteLLM=KEEP core,
OPA=KEEP, PagerDuty exporter=ADOPT nhỏ.

**WITNESS ĐỘC LẬP W2 (2026-09-11, ngoài SCP lineage — DNA G05)**: report tại
`reports/witness/WITNESS-REPORT-W2-2026-09-11.md`. Witness tự dựng real API cluster
(4 instances) + real cloud LLM, env-only wiring (0 system-code edit): golden chain
/ask → TaskKernel → witness-api → real LLM → cross-verify 2 families → PASS "Paris" 0.85;
R1 N=30: accuracy-answered 1.0, honesty 1.0 (0 hallucination, 19 abstain-by-strict-verify);
R3 chaos: kill -9 → breaker + recovery 21s; 429/500 storm absorbed; R4 soak 5.5 phút:
184,276 requests zero-error, RSS +14MB no-leak. Limits: 1 node, loopback, 1 worker,
answer-rate 0.167 (strict verification), LLM quota scarce (429 measured).

**W2 tìm thấy 4 bug MỚI (post-campaign, CHƯA fix — việc tiếp theo)**:
1. [HIGH] `InvalidTransition: HUMAN_REVIEW->HUMAN_REVIEW` — `scp/ask_kernel_adapter.py:411`
   finalize escalate lần 2 khi task đã HUMAN_REVIEW → /ask 500 (observed live).
2. [HIGH] Zero-cost wall chặn mọi custom OpenAI-compatible provider mặc định
   (`DENY_UNKNOWN_PRICE`) — fail-closed đúng nhưng chỉ log debug, operator không có tín hiệu.
3. [MEDIUM] Provider degradation → /ask latency collapse 30–180s (timeout × rotation × retry),
   không early bail-out.
4. [LOW] `.env` duplicate-key footgun: 2 dòng `OPENAI_API_KEY=` (dotenv last-wins).

Known-red ĐÃ GIẢI QUYẾT: T00 policy A (allowlist `declared_infra_skips.json`, commit
`cb1c99e`); SCP_EGRESS_MODE=deny không chặn urllib → EE ĐÓNG (choke `enforce_egress_policy`);
SCP_ENCRYPTION_KEY ĐÃ ROTATE (passphrase mới, re-encrypt — bypasses rỗng 0 record);
basetemp corrupt ĐÃ XÓA (owner). Còn: supervisor restart-budget behavior bị xóa
pre-campaign (owner xác nhận ý định). Verdict tool vẫn là authority duy nhất cho
claim "complete" — mọi closure là PASS_WITHIN_SCOPE.

`work_snapshot_sha` là commit sản phẩm trước commit handoff này; luôn resolve full SHA
từ live Git trước khi dùng. Không kế thừa SHA, branch state hoặc verdict trong phần
này nếu chưa refresh GitHub.

## B2. Release task scope hiện hành

```text
1. Sửa lỗi initialization/product/harness được GitHub-hosted runner chứng minh.
2. Chỉ tạo manifest-only freeze sau khi mandatory gates xanh trên candidate tree.
3. RC_DONE chỉ hợp lệ khi mọi required gate PASS trên đúng frozen SHA và blockers=0.
4. Merge integration -> main chỉ qua guarded workflow khi remote head vẫn đúng frozen SHA.
5. Sau merge, chạy lại full-system customer-handoff trên chính merge SHA của main.
6. Không khởi động SCP/service/runtime trên PC người dùng trong task này.
```

Các claim coverage/architecture trước đây không được dùng thay cho same-SHA release
evidence. Authority machine-readable vẫn là target manifest, test-skill binding,
complete reference và protected invariants được nêu trong phần A.

## B3. Live lineage đã hấp thụ vào candidate

```text
origin/main at task refresh: ac68ba9bfb4d8955c9cfa20e574113feb8f0102b
candidate product snapshot: fc272cf (resolve full SHA live)

candidate contains main plus:
  b94cba0  close missing authoritative TaskKernel paths and acceptance races
  f975210  provision Bun in authoritative RC platform/security jobs
  fc272cf  restore strict TaskKernel HUMAN_REVIEW semantics; isolate acceptance
             pricing proofs; keep multi-provider crosscheck enabled
```

## B4. Root-cause classification và correction

```text
HARNESS_BROKEN:
  main RC referenced deleted/non-existent TaskKernel durability test paths.
  Candidate points the workflow to extant strict durability/recovery tests.

PRODUCT_FAIL / ACCEPTANCE INTEGRATION:
  main acceptance failed verified completion, provider fallback, duplicate
  idempotency and parallel durability behavior.
  Candidate contains the AskKernelAdapter race correction and deterministic
  loopback provider evidence required to exercise those paths.

REJECTED WEAKENING IN EARLIER CANDIDATE:
  Do not accept f975210 as RC evidence by itself. Its acceptance harness disabled
  SCP_MULTI_LLM_CROSSCHECK, wrote fixture proof into shared repository state,
  swallowed proof errors, and excluded HUMAN_REVIEW from in_flight_count.
  fc272cf removes those weakenings and adds regression contracts.
```

Không có test nào bị delete/skip/xfail, không hạ threshold, không ignore exit code,
và không đổi fail-closed thành fail-open để tạo màu xanh.

## B5. Evidence đã quan sát trong task

```text
main ac68ba9 diagnostic rerun:
  baseline: PASS
  authoritative RC: FAIL
    - TaskKernel durability gate: missing workflow test path
    - behavioral acceptance: A03/A05/A08/A11 failed
  conclusion: main is not releasable; runner initialization was not the only issue

candidate fc272cf local non-runtime evidence:
  py_compile of changed executable/tests: PASS
  tests/T04_kernel + tests/T05_gateway + tests/T11_release: 85 PASS
  SCP process/service started on user PC: NO

candidate same-SHA GitHub mandatory gates: PENDING
manifest-only freeze: PENDING
RC_DONE/blockers=0: NOT YET DERIVED
main merge and fresh customer handoff: PENDING
```

Local PASS chỉ có nghĩa không thấy lỗi trong scope đã nêu; nó không thay thế GitHub
multi-platform, acceptance, mutation, security, manifest hoặc customer-handoff gate.

## B6. Finite next sequence

```text
1. Push candidate và fast-forward designated integration branch only after live refresh.
2. Let GitHub-hosted mandatory gates test the exact candidate SHA.
3. If a gate fails, classify and repair product/harness at the failure point.
4. When pre-freeze gates pass, allow workflow to create exactly one manifest-only child.
5. Verify every mandatory gate and blockers=0 on that frozen child SHA.
6. Dispatch guarded merge with frozen-head equality check.
7. Require a fresh full-system customer-handoff PASS on resulting main merge SHA.
```

## B7. Completion language

Allowed now:

> Candidate strict-correction đã qua 85 unit-contract test local không chạy SCP service; release vẫn BLOCKED cho tới khi toàn bộ mandatory gate PASS trên cùng frozen SHA, blockers=0, guarded merge thành công và customer-handoff mới PASS trên merge SHA của main.

Forbidden now:

> Candidate/main đã DONE, RC/release-ready, production-ready hoặc customer-handoff hoàn tất trước khi có đúng chuỗi evidence nêu trên.

## B8. RC continuation — 2026-09-04, registry and handoff closure

- Frozen `f2ded72de8a13e92dc28ae06649ed83f3e1d5bad` reached `RC_DONE`, blockers=0
  in run `33843699788`. The approved promotion run `33845108898` then failed
  Ubuntu dashboard audit twice, both with npm registry `E503` at the quick-audit
  endpoint. No PR or main merge occurred. Earlier green evidence is retained,
  but is not reused for a changed candidate.
- `f0ed761` repairs the harness with at most three bounded native npm audit
  attempts, retries only transient registry/network failures, keeps
  `--omit=dev --audit-level=high`, rejects missing/invalid reports and records
  command, exits, exact SHA, lockfile hash and Skill/DNA hashes. `npm ci` no longer
  performs its duplicate best-effort advisory query; the separate mandatory
  audit and dashboard build both remain blocking.
- Bot-token writes do not trigger a fresh GitHub push workflow. Promotion now
  explicitly dispatches the complete handoff on main with `handoff_merge_sha`.
  The lineage gate requires live main == checkout == dispatched merge SHA,
  exactly one merged integration PR and byte-identical Git trees between main
  and the immutable frozen PR head (including the manifest). Linear squash
  lineage is supported to preserve the live main ruleset; for a two-parent
  merge, the PR head must additionally equal the second parent.
  Unknown/mismatched lineage blocks handoff.
- Local non-runtime verification: `python -m pytest -q tests/T11_release --tb=short`
  returned 59 passed; focused Ruff, py_compile and Skill/DNA contract passed.
  This is only patch verification, not current-SHA release evidence.
- Remaining sequence: run GitHub mandatory gates on the new candidate, create a
  manifest-only child, verify that exact frozen child, guarded PR merge, then
  fresh full-system handoff on the actual main merge SHA. No SCP service/runtime
  is to be launched on the user's PC. The user's root checkout is preserved;
  the existing RC branch is being worked in `scp-rc-promotion-fix` worktree.
- Rollback is a reviewed Git revert of the scoped harness changes; never remove
  a mandatory gate or lower a security/mutation threshold as a rollback shortcut.
- Continuation findings: Windows npm can emit registry timeout without
  `error.code`; the exact observed audit-endpoint messages are now retryable
  errors, never success. Main requires linear history and the `p0-baseline`
  check. Use SHA-guarded squash merge without changing that ruleset. After
  freeze, sync its exact SHA to the existing RC branch to obtain baseline CI;
  create the integration PR through the authorized user session if bot PR
  creation is unavailable, then let the guarded workflow find and merge it.
- Frozen run on `0856490` exposed the legacy npm 10 Quick Audit fallback returning
  400 from the retired endpoint. The auditor is now pinned to npm 11.19.1 in the
  GitHub runner's task-local directory (Node 20.20.2 satisfies its ^20.17.0 engine).
  Its upstream source uses Bulk Advisory only; no 400 response is treated as a
  pass or generic retry. Response headers/cookies are redacted from new audit
  artifacts. Ref: https://github.com/npm/cli/blob/v11.19.1/workspaces/arborist/lib/audit-report.js

## B9. Security Remediation Handoff — 2026-09-09

- **Commit:** `d0fcb6e` on branch `omega/gap-01-remediation`
- **Test results:** 619 PASS / 1 FAIL (99.8%) — 1 flaky isolation test on Windows
  (`test_autofix_end_to_end_rollback_on_verify_failure`): passes alone, fails in
  full suite due to Windows file locking in `shutil.move` on shadow snapshot dir.
  HARNESS_BROKEN classification; product rollback behavior is correct.

- **R2 (PCController Token Boundary):** COMPLETED.
  HMAC-SHA256 PEP token wraps PCController. 15 new tests in T03. Exploit probe blocked.

- **R3 (Verifier Receipt Cryptographic Provenance):** COMPLETED.
  `scp/core/verifier_receipt.py` created. Kernel verifies HMAC signature before
  COMPLETED commit. 22 new tests in T04.

- **R6 (AutoFix Shadow Rollback):** COMPLETED.
  `scp/autofix/shadow_snapshot.py` created. AutoFix pipeline auto-rollbacks when
  pytest fails. 12 new tests in T07.

- **Bug fixes applied:**
  - `verify_mixin.py`: exit code 4 + "ERROR" = collection error, not test failure
    (`_has_err` only True when returncode ∉ {4,5})
  - `post_fix_verify.py`: `semantic_equiv` fail-open when no backup (DNA #7)
  - `shadow_snapshot.py`: `os.replace` WinError 5 fallback to `shutil.copy2`
  - `shadow_snapshot.py`: removed local `import shutil` that shadowed module global

- **Phase 3 Challenger Audit:** NOT COMPLETED — teamwork_preview quota exhausted
  (~89h reset). Workers completed R2/R3/R6 at 100% unit test pass. Challenger
  subagents were dispatched but quota ran out before results were received.
  Next session should resume Challenger Audit or accept current evidence level.

- **R6 / T07 (fix bổ sung):** COMPLETED — `12e04c1`
  `shadow_snapshot.py`: `shutil.move` → `copytree+rmtree` với retry 5 lần.
  Root cause: Windows giữ file handle trên tx_dir khi full suite chạy.
  `shutil.copytree` đọc từng file riêng (không cần lock dir) nên bypass WinError 5.

- **Final test result:** **620/620 PASS** · 0 FAIL · 245s
  SHA: `12e04c1` — branch `omega/gap-01-remediation`

- **Remaining sequence:**
  1. Start SCP service live trên PC và chạy `scp-runtime-audit`
  2. Push branch và run GitHub mandatory gates
  3. Khi gates pass → manifest-only freeze → guarded PR merge
  4. Fresh full-system customer handoff trên merge SHA
  5. (Optional) Challenger Audit khi teamwork quota reset (~89h)

## B10. Owner correction — zero-cost wall thành opt-in (S18, 2026-09-13)

- **OWNER DIRECTIVE:** "tôi chỉ bảo dùng API free cho trường hợp của tôi, CHỨ KHÔNG phải
  mã hóa cứng bắt buộc SCP đòi API free — xóa cái này đi." → free-only là deployment
  preference, KHÔNG phải compile-time mandate.
- **Correction (commit S18, branch `audit/runtime-guard-AUDIT-20260909`):** zero-cost $0
  wall đổi từ "install vô điều kiện lúc import" → **opt-in qua `SCP_LLM_COST_MODE=free_only`**.
  Single control point = gate trong `scp/llm_gateway/zero_cost_runtime.py`
  (`_free_only_policy_active` + short-circuit trong `authorize_outbound`/`record_outbound_sent`).
  Mặc định (unset) → wall tắt, provider paid/free cấu hình chạy bình thường. Không thêm env
  mới; máy owner vẫn set `free_only` → giữ 100% hành vi cũ. `install_egress_guard` (destination
  authority) KHÔNG đổi, luôn always-on.
- **Tác động runtime:** container hiện tại có `SCP_LLM_COST_MODE` UNSET nên trước fix mọi LLM
  call chết im lặng (accuracy 0%); sau fix default-off → **unblock re-run benchmark** (cần
  `docker compose restart`/rebuild image để mang code mới).
- **Governance:** 4 file `spec/` vẫn tham chiếu zero-cost/$0 — KHÔNG sửa (protected). Đề xuất
  cập nhật spec nằm ở `data/governance/proposals/PROP-zero-cost-optin-2026-09-13.md`,
  trạng thái `PENDING_OWNER_APPROVAL`.
- **Evidence:** T05 + T02 flow02 + T01 boot = 122 passed exit 0; `t00_meta_audit` 0 new
  regressions; `verify_scp_test_skill_contract` PASS_WITHIN_SCOPE exit 0; runtime proof
  default-off (`authorize_outbound` → `(request, None)`, `_guard is None`); strictness TĂNG
  qua `tests/T05_gateway/test_zero_cost_optin.py`. Báo cáo:
  `reports/expert-panel/S18-zero-cost-optin.md`.

## B11. Benchmark live-LLM đầu tiên + ask-kernel fixes (S19/V19, 2026-09-13)

- **Egress de-hardcode (commit 37d7419):** `compose.yml` ghi cứng `SCP_EGRESS_MODE: deny`
  đè `.env` → đổi `${SCP_EGRESS_MODE:-deny}` + passthrough `SCP_LLM_EGRESS_ALLOWLIST`.
  Ma trận verify: loopback OK (mọi mode), provider host OK, host lạ DENY. `.env` sửa
  `SCP_LLM_FALLBACK_PROVIDERS` 6 entry từ 3-field (bị reject "malformed") → 4-field +
  thêm `*_MODEL`. Family sống: OpenRouter + NVIDIA (Gemini/Groq model names chết — nợ).
- **2 bug product tìm ra BẰNG BENCHMARK THẬT (không phải test pass-trắng):** (1) finalize
  giả định task còn RUNNING → `InvalidTransition RECONCILING->VERIFYING` HTTP 500 khi
  provider latency > lease 60s (kèm họ W2 `HUMAN_REVIEW->HUMAN_REVIEW`); (2) idempotency
  key THAY THẾ hash câu hỏi → mọi ask sau collide "stable logical ask already exists".
  Fix `1fc7531` (S19): finalize route theo state hiện tại, stale attempt = fail-closed
  200 `lifecycle_authority_lost` + escalate, KHÔNG thêm edge transition table (17 states
  giữ nguyên, V19 xác minh git-diff=0); `task_id_for` luôn gồm canonical hash.
  Tests mới 12/12; T04 217P/T03 782P/T10 9P; runtime proof container: câu mới không
  collide, chậm → 200 fail-closed (không 500), 2 nhánh mới quan sát live. V19 ACCEPT.
- **Kết quả benchmark `bench_final_seed99.json` (seed 99, HEAD 1fc7531, N=10+3 attacks,
  evaluation-mode auto):** G security **100%** (3/3 blocked); crosscheck hoạt động THẬT —
  22 lần decide, **21 agree / 1 disagree**, 2 family độc lập (OpenRouter+NVIDIA); answer
  corrupted KHÔNG bao giờ được endorse (integrity); **A accuracy 0/8** — SCP không tự sửa
  corrupted answer (self-correction 0/8), + 2/10 fail-closed do lease hết hạn; latency
  mean 85.6s / p95 260.7s (free-tier chậm hơn lease 60s → đường HUMAN_REVIEW kích hoạt
  thường xuyên). Đây là baseline đầu tiên ĐO ĐƯỢC, kèm raw log + SHA `bench_sha.txt`.
- **Nợ mở:** lease TTL vs provider chậm (V19 khuyến nghị tăng/heartbeat — việc kế tiếp
  hợp lý nhất); Gemini/Groq model discovery; free_catalog đọc `SCP_LLM_EGRESS_ALLOWLIST`;
  FA-04 evidence_replay stub.

## B12. Hedge + heartbeat campaign — latency -89.5% (S21/V21, 2026-09-13)

- **S21 hedged-LLM race `41f2135`** (owner design): attempt deadline 10s → bắn song song
  provider kế, first-result-wins (tie-break theo chain), cap 90s fail-closed, kill-switch
  `SCP_LLM_HEDGE=off`. Seam `client.py:chat()` — 690 insertions/0 deletions. V21 ACCEPT
  (spot-check độc lập 2 câu /ask thật; 690/0; crosscheck đi provider.chat trực tiếp nên
  hedge không đụng 2-family judge).
- **Benchmark tổng hợp `bench_combined_seed2026.json`** (seed 2026, HEAD 283fb52, N=10+3):
  latency **mean 85.6s → 9.02s (giảm 89.5%)**, p95 260.7s → 19.4s; security 100% (3/3);
  0 câu chết lease (S20 heartbeat). Accuracy 0/8 — root cause ĐỊNH VỊ ĐƯỢC: crosscheck
  26/26 `missing_distinct_providers` (secondary family không đóng góp) → verdict_pass/
  judge_pass/governance_uphold đều False → withhold. S22 đang xử lý.
- **Dọc-slice arch audit ĐÓNG ĐỦ (worker→verifier riêng từng cái):** S20 heartbeat
  `925592c` (TTL 2s sống 207.5s, renew x307), B-S1 KB-wire `9ba7828` (judge consult KB,
  GAP: data/knowledge rỗng cần nạp data), C-S2 world-state hook `433dcea` (X08
  evidence_refs=[run_id]).
- **Pre-existing RED ghi nhận 3 lần độc lập:** `test_ws_chat_fail_closed` (T02) —
  environmental (.env thật keys vs `_disable_openrouter`), không phải regression S20/S21.

## B13. Phế truất toàn diện Zero-Cost 5 tầng & Khôi phục Acceptance 12/12 PASS (2026-09-21)

- **Trạng thái nhánh:** `feature/autonomous-mode-antigravity-v2` (Base commit: `0569065ccd603c9b2171b49a4e17e023bc46c1d6`)
- **Tóm tắt phiên làm việc:**
  1. **Phế truất hoàn toàn kiến trúc Zero-cost trên toàn bộ 5 tầng:**
     - *Authority:* Loại bỏ bất biến `zero_cost.max_cost` khỏi `spec/protected_invariants.yaml`.
     - *Spec:* Gỡ bỏ capability `intelligence.zero_cost` khỏi `spec/complete_scp_reference.yaml`.
     - *Python Implementation:* Dọn sạch logic zero-cost, catalog lọc giá $0 và dead guards trong `scp/llm_gateway/free_catalog.py`, `scp/llm_gateway/client.py`, `scp/llm_gateway/drift_guard.py`.
     - *Tests:* Cập nhật và thanh lý các test phụ thuộc giả định zero-cost trong suites `T02`, `T04`, `T05`, loại bỏ các placebo assertions không phản ánh hành vi production.
     - *TypeScript Microservice:* Dọn sạch tàn dư trong `mini-services/llm-bridge/` (xóa vật lý `zero_cost_bootstrap.ts`, bỏ chốt chặn `__SCP_ZERO_COST_PEP__` trong `core.ts`, cập nhật `index.ts` boot trực tiếp `./core`, cập nhật `package.json` scripts trỏ về `index.ts`, và cập nhật evidence trong `spec/llm_outbound_paths.yaml`).
  2. **Sửa dứt điểm 4 lỗi gốc rễ của runner Cấp độ 5 (`scripts/run_scp_acceptance.py`), đạt 12/12 PASS tuyệt đối:**
     - Vá lỗ hổng SSRF fail-closed trong `_ask_impl.py` (bảo đảm tác vụ bị từ chối chuyển sang trạng thái kết thúc an toàn thay vì treo/lỗi).
     - Khôi phục mapping provider failover đúng (xử lý chính xác fallback giữa các provider khi gặp lỗi kết nối hoặc quota).
     - Bổ sung or-fallback cho capability secret và mock provider keys để xử lý chuỗi môi trường rỗng.
     - Cơ chế readiness handshake 30s sau crash restart, loại bỏ triệt để race condition giữa test runner và daemon process.
  3. **Chuẩn hóa và lưu trữ bài học vào quy trình Agent:**
     - `.agents/EXECUTION_PROTOCOL.md` (Phase 1.1: 5-Layer Architectural Deprecation Protocol — quy chuẩn 5 tầng phế truất kiến trúc).
     - `.agents/AGENTS.md` (FA-01: Nghiêm cấm Placebo Assertions — assert True, assert 1 == 1, assert x == x; FA-02: Hướng dẫn deprecation chuẩn hóa).
     - `.agents/skills/scp-delta-audit/SKILL.md` (Phase 5.1: Kiểm toán delta chống suy giảm chất lượng kiểm thử).
- **Trạng thái kiểm chứng (Reality Evidence):**
  - `python tools/t00_meta_audit.py`: **0 new regressions** (Baseline debt được theo dõi, không có vi phạm mới).
  - `python scripts/run_scp_acceptance.py`: **12/12 PASSED** tuyệt đối (SCP-A01 đến SCP-A12).
