# ci-release-gate — DONE (maker, chưa có checker)

- **Lane:** `ci-release-gate` · maker: Claude (1 lineage) · ngày 31/10/2026
- **Nhánh:** `claude/ci-release-gate` (worktree `D:/scp/.claude/worktrees/ci-release-gate`), gốc `main` = `5da3d6d0`
- **Commit sửa:** `bf12ff8d8657b36c0c41d601834c4649b94bdc76` (parent `5da3d6d0`). Commit thứ hai chỉ thêm bản sao file này vào `docs/evidence/ci-release-gate-done.final.md`.
- **Không push, không merge, không thao tác ghi nào trên GitHub** (không re-run, không cancel, không sửa secret/setting). Không đọc `.env`, không in secret. Main tree `D:/scp` không bị sửa.
- **Skill áp dụng:** `scp-dna`, `scp-release-evidence-gate`, `scp-reality-verifier`.
- **Nhãn:** VERIFIED-RUN = tôi chạy lệnh/đọc log trong phiên này · HYPOTHESIS · UNKNOWN. PASS ≠ TRUE: mọi PASS dưới đây chỉ đúng trong phạm vi đã nêu.

---

## 1. Trả lời ngắn

1. **Cổng phát hành `scp-release-gate.yml` không chạy job nào từ 24/10 18:56Z** vì commit `e12283ca` thêm **khóa `with:` thứ hai** vào cùng một bước checkout. GitHub từ chối khóa trùng ("workflow file issue"). PyYAML lại âm thầm giữ khóa cuối, nên mọi kiểm tra cục bộ và test hợp đồng đều báo ổn. **VERIFIED-RUN.**
2. **Container CI:** `docker build .` không có `--target` nên build stage **cuối cùng là `audit`**. Stage này LOCAL-ONLY (chứa test, dev deps, `.git`). Từ E-W1 (23/10), stage audit có lệnh `COPY .git/packed-refs`, mà checkout của CI không có file này ⇒ build đỏ. **Nguy hiểm hơn:** run xanh cuối (19/10) đã **push stage audit lên GHCR**. Nếu chỉ "sửa packed-refs" thì CI sẽ đẩy **toàn bộ lịch sử git** lên registry. **VERIFIED-RUN.**
3. **Môi trường CI lệch khỏi sản phẩm** ở 3 chỗ (gây khoảng 20/57 lỗi đỏ trong full suite của RC Promotion): vẫn cài bun dù luật zero-bun bắt `which("bun") is None`; dùng Node 20 trong khi test chạy `--experimental-strip-types` (cần ≥22.6, Dockerfile đã lên node:24); checkout nông (depth 2/1) nên T00 dừng fail-closed vì không thấy `main`/`origin/main`. **VERIFIED-RUN.**
4. **T00 L3 đỏ ở bước WHY Chain là đỏ THẬT.** Các commit trên main thật sự thiếu "Chuỗi Tại Sao", gate không sai. Gốc: máy hub **chưa cài hook `commit-msg`**. `.git/hooks` chỉ có `pre-commit` bản cũ (trỏ `D:/opt/scp-venv-w16`). **12/12 commit chưa push (`c9aa96e7..5da3d6d0`) cũng bị gate từ chối** ⇒ lần push tới, T00 L3 **vẫn đỏ** ở bước này. **VERIFIED-RUN.**
5. Còn các **test đỏ thật trên main** (sản phẩm/dữ liệu, không phải CI). Tôi **không sửa**; danh sách ở §5.

## 2. Gốc lỗi từng workflow (bằng chứng)

Tất cả run đỏ gần nhất trên `main` đều ở SHA `c9aa96e7` (push lúc 30/10 17:44Z). Main cục bộ đã tới `5da3d6d0` nhưng chưa push.

| Workflow (file) | Thành công cuối trên main | Run đỏ đọc log | Bước đỏ | Loại gốc lỗi |
|---|---|---|---|---|
| SCP Pre-RC Verification (`scp-release-gate.yml`) | **chưa có lần nào trong 200 run gần nhất** (191 failure + 9 cancelled) | `41101176535` | (không job nào chạy) *"This run likely failed because of a workflow file issue"* | **Lỗi cú pháp workflow**: khóa `with:` trùng tại dòng 35/37, do `e12283ca` (24/10 01:56 +07). Ranh giới thấy rõ: run `40120713050` (418ea126) còn mang tên "SCP Pre-RC Verification", còn run `40123024332` (e12283ca) trở đi mang tên đường dẫn file (dấu hiệu GitHub không parse được file). |
| (cùng file, trước 24/10) | — | `40120713050` | Mandatory SCP Skill and DNA contract | `"AGENTS.md guidance is missing"`: AGENTS.md bị gỡ khỏi git ở `1372171d` (24/10), track lại ở `37b1cddc` (26/10). Hiện **đã hết lỗi**: `verify_scp_test_skill_contract.py` = PASS_WITHIN_SCOPE trên 5da3d6d0 (VERIFIED-RUN). |
| SCP Container CI and Publish (`container-publish.yml`) | `e144bea9` 19/10 09:42Z (`39232032652`) | `41101178247` | Build container image | **Lỗi Dockerfile/target**: `[audit 40/44] COPY .git/packed-refs` ⇒ `"/.git/packed-refs": not found`. Gốc sâu hơn: build không có `--target` ⇒ build stage `audit`. Log run xanh 19/10 có `[audit 1/1]` rồi `The push refers to repository [ghcr.io/checken1994/vietnam-lab]`. |
| SCP L3 Verification (T00) (`scp_guardrails.yml`) | không có thành công nào trên main trong cửa sổ | `41101178158` | WHY Chain | **Đỏ thật về quy trình**: `why_chain: REJECT commit 48b159c5 …, 1f1ad04f, 9f8e4bb6, 3a9f5b83, …` (thiếu mục "Chuỗi Tại Sao" hoặc thiếu phần). Gate đúng. |
| SCP Strict System Audit (`scp-system-audit.yml`) | `df9c9d32` 13/10 | `41101178219` | Run strict system audit | 3 bước FAIL: `import_manifest` (thiếu `redis` trong requirements, **sản phẩm**), `full_pytest` (gồm cả đỏ do môi trường lẫn đỏ thật), `reality_suite` 66/68. Môi trường: không fetch-depth, cài bun, không pin Node. |
| SCP RC Promotion (`scp-rc-promotion.yml`) | `df9c9d32` 13/10 | `41101178732` | platform-gates: Full suite (`44 failed, 6114 passed, 13 errors` trên ubuntu); security-mutation-durability: Compile and lint | Lẫn lộn: khoảng 20 đỏ do **môi trường CI** (bun, Node 20, T00 trusted base, dashboard chưa build), phần còn lại **đỏ thật** (§5). |
| SCP Refactor Freeze (`scp-refactor-freeze.yml`) | `78ae265e` 13/10 | `41101178392` | Ruff committed-source syntax and undefined names | **Đỏ thật sản phẩm/test**: 3 lỗi F821 (§5). Thêm lệch môi trường (bun, Node 20/22, depth 2). |
| SCP CI Baseline (`ci.yml`, non-blocking) | `df9c9d32` 13/10 | `41101178521` | Enforce Baseline (P0) | `TimeoutExpired … timed out after 900 seconds` (full suite chạy 881s ở RC; giới hạn 900s quá sát). **Không** nới timeout trong lane này (§6). Có cài bun ⇒ đã gỡ. |

**Secret:** không có bước nào đỏ vì thiếu secret. Workflow tham chiếu theo tên `secrets.SCP_CAPABILITY_SECRET`, `secrets.SCP_JOURNAL_SECRET`, `secrets.GITHUB_TOKEN`; khi trống thì sinh giá trị ngẫu nhiên tạm. Tôi không đọc và không in giá trị nào. *(Ghi chú: log `--log-failed` của GitHub in ra giá trị ngẫu nhiên tạm của mỗi run; đó không phải secret thật, và tôi không chép chúng vào báo cáo này.)*
**Action không tồn tại:** không thấy. Mọi action pin SHA đều chạy được trong các job khác của cùng run.

## 3. Đã sửa gì (commit `bf12ff8d`)

| File | Sửa | Vì sao không làm chuẩn yếu đi |
|---|---|---|
| `.github/workflows/scp-release-gate.yml` | Gộp 2 khóa `with:` thành **một** (`ref` + `fetch-depth: 0`); Node 20→**24** (setup + bước verify); **gỡ setup-bun** | Không bỏ bước nào. Mọi bước cũ (skill contract, ruff, full suite, mutation 0.40, bandit ≤9 medium, reality, smoke, dashboard audit) giữ nguyên ngưỡng. |
| `.github/workflows/container-publish.yml` | `docker build --target runtime`; **thêm 2 guard fail-closed**: ảnh không được có `/app/.git`, `/app/tests/T00_integrity`, `/app/dashboard/node_modules`; ENTRYPOINT phải là `["python","-m","scp"]` | Chặt hơn: trước đây không có guard nào ngăn đẩy ảnh audit lên registry. |
| `.github/workflows/scp-rc-promotion.yml` | Job `platform-gates` và `security-and-durability`: `fetch-depth: 0`, Node 24, gỡ bun. Các job khác giữ depth 2. | Không đổi ngưỡng hay điều kiện phát hành nào. |
| `.github/workflows/scp-refactor-freeze.yml` | `fetch-depth: 0`, Node 24, gỡ bun; bước verify từ "cho phép 20 hoặc 22" thành **đúng 24** | Bước verify chặt hơn (một giá trị thay vì hai). |
| `.github/workflows/scp-system-audit.yml` | `fetch-depth: 0`; gỡ bun; **thêm** setup-node 24 + verify (trước đây phụ thuộc Node mặc định của runner) | Thêm bước. |
| `.github/workflows/ci.yml` | Gỡ bun; thêm setup-node 24 + verify | Thêm bước. |
| `.github/workflows/scp_guardrails.yml` | Thêm bước **"Workflow contract"** (`python tools/check_workflow_contract.py`) chạy **trước** các bước WHY (để WHY đỏ không che lỗi file workflow); chuyển bước `Install dependencies` lên sớm hơn (cần PyYAML) | Thêm bước; không bỏ bước nào. |
| `tools/check_workflow_contract.py` (MỚI) | Loader YAML **từ chối khóa trùng** + 5 bất biến: không bun; Node ≥22 và **bằng** Node trong Dockerfile; bước verify Node phải khớp setup-node; job chạy full-suite/T00 phải `fetch-depth: 0`; job có `docker push` phải `--target` và không được là stage LOCAL-ONLY `audit`. Exit 0/1/2 (2 = lỗi công cụ ⇒ fail-closed). | Gate mới. |
| `tests/T11_release/test_workflow_contract.py` (MỚI) | 11 test: repo thật pass; loader bắt đúng mù điểm của `safe_load`; **7 mutant** áp lên **bản sao file workflow thật**; thiếu thư mục ⇒ exit 2; Dockerfile không có node ⇒ đỏ | — |
| `tests/T11_release/test_rc_workflow_runtime_contract.py` | Pin `"20"` → `"24"` (3 dòng) | Vẫn so sánh bằng đúng một giá trị như cũ, độ chặt giữ nguyên. **Owner/checker cần duyệt quyết định Node 24** (§7 mục 4). |
| `tests/T11_release/test_scp_test_skill_contract.py` | Xoá `import pytest` không dùng (ruff F401). File này nằm trong bước "Focused Ruff correctness" của cổng release, nên nếu để nguyên thì bước đó đỏ. | Không đụng assertion nào. |

**Không sửa** `scp/**`, Dockerfile, `spec/why_chain_policy.json` (epoch), `.git/hooks`.

## 4. Kiểm cục bộ (VERIFIED-RUN, máy Windows của owner, worktree, SHA `5da3d6d0` rồi `bf12ff8d`)

| Kiểm tra | Trước sửa | Sau sửa |
|---|---|---|
| `python tools/check_workflow_contract.py` | **exit 1, 17 vi phạm** (gồm `scp-release-gate.yml: duplicate key 'with' at line 37 (first at line 35)`) | **exit 0**, 0 vi phạm, 8 workflow |
| Quét **28 file YAML** được track bằng loader chặt | — | 0 khóa trùng ở chỗ khác |
| `docker build` (mặc định, giống CI cũ) từ worktree (`.git` là file gitlink, không có packed-refs, giống checkout CI) | **exit 1** `"/.git/packed-refs": not found` (tái hiện đúng lỗi CI) | — |
| `docker build --target runtime` | — | **exit 0**, 0 bước `[audit …]` được build |
| Guard uid 10001 / không-.git / ENTRYPOINT trên ảnh runtime | — | đều pass |
| **Anti-placebo guard** trên ảnh `scp-audit:latest` (local) | — | guard **exit 1** (ảnh có `/app/.git`, `/app/tests/T00_integrity`, `/app/dashboard/node_modules`); ENTRYPOINT = `null` |
| Smoke `/health` giống bước CI (secret ngẫu nhiên, egress deny) | — | `{"status":"ok",…}` ở lần thử thứ 3; đã xoá container |
| T00 `--file` trong checkout mô phỏng actions/checkout **depth 2** | **exit 1** `[T00 FAIL-CLOSED] Trusted base 'main' is invalid or missing` | — |
| T00 `--file` trong checkout **fetch đủ** (`+refs/heads/main:refs/remotes/origin/main`) | — | **exit 0**, `Trusted Base: origin/main`, 0 regressions |
| `tools/verify_scp_test_skill_contract.py` + 2 test contract | PASS_WITHIN_SCOPE, 12 passed | giống |
| `tools/check_why_claims.py` | `why_claims_found=0` | giống |
| `py_compile` (danh sách của release-gate + tool mới) | exit 0 | exit 0 |
| Focused Ruff (danh sách của release-gate) | **exit 1** F401 `test_scp_test_skill_contract.py` | exit 0 |
| Refactor-freeze Ruff `scp scripts tools tests --select E9,F63,F7,F82` | exit 1, 3 F821 | **vẫn exit 1, 3 F821** (đỏ thật, §5) |
| `tools/audit_dependencies.py` | — | exit 0 (DEP-01/04/05 PASS, OSV 0 vuln trên 32 dep) |
| `check_why_chain.py --range 5da3d6d0 --base c9aa96e7` (range hub sẽ push) | **exit 1, 12/12 commit bị từ chối** | không đổi (lịch sử) |
| `check_why_chain.py --range bf12ff8d --base 5da3d6d0` | — | 1 checked, 0 failed |
| pytest `tests/T11_release/` + `T00_integrity/{test_why_chain_gate,test_dependency_audit,test_secret_scanner}.py` | — | **188 passed** |
| Mutant (trong `test_workflow_contract.py`) | — | 7/7 làm gate đỏ: M1 trả lại `with:` trùng · M2 thêm lại setup-bun · M3 Node 20 · M4 bỏ `--target` · M5 fetch-depth 1 cho job full-suite · M6 bước verify Node lệch · M7 `--target audit` |
| Pre-commit hook T00 khi commit | — | `All integrity checks passed (0 new regressions)` (cảnh báo L4 protected path cho workflow/test là cảnh báo cục bộ, đúng như mong đợi) |

**Đã dọn:** image `scp-api:ci-gate-lane` đã xoá; build mặc định thất bại nên không tạo image; các bản clone tạm trong scratch đã xoá. Build cache Docker còn lại (không prune, để khỏi đụng cache của các lane khác).

## 5. Test/bước đỏ THẬT trên main (không sửa ở lane này, cần người xử lý)

Nguồn: log RC Promotion `41101178732` (SHA c9aa96e7), Refactor Freeze `41101178392`, Strict `41101178219`, cùng lệnh chạy lại cục bộ trên 5da3d6d0 ở những chỗ có ghi.

| # | Đỏ | Bằng chứng | Gợi ý chủ xử lý |
|---|---|---|---|
| R1 | Ruff F821 `scp/autofix/llm_fix_parts/process_bug_with_llm.py:203` `generate_fix_for_bug` | Chạy lại cục bộ trên 5da3d6d0: còn. `# noqa: F821` đặt ở dòng 208 (dấu `)` đóng) chứ không ở dòng 203 nên ruff không nhận. Probe: tên này không có trong globals của module (theo comment, nó được `llm_fix.py` rebind lúc chạy). | lane autofix-safety (scp/autofix) |
| R2 | Ruff F821 `scp/autofix/permission.py:78` `Any` | Có `from __future__ import annotations` nên không lỗi lúc chạy, nhưng thiếu import | autofix-safety |
| R3 | Ruff F821 `tests/T02_contract/test_ask_w7_abstain_delivery.py:235` `Any` | Chạy lại cục bộ: còn | chủ test ask-w7 |
| R4 | `import_manifest`: `redis` chưa có trong `scp/requirements.txt` | Chạy lại cục bộ `scripts/check_imports_vs_requirements.py`: `[FAIL] … redis`. Import lazy ở `scp/api/rate_limit_redis.py:118,140` (từ `50597f25`, 25/10) | Owner quyết: Redis rate-limit có là runtime được hỗ trợ không (nếu có thì thêm vào lock + hash + SBOM) |
| R5 | `test_meta_audit.py::test_qlt06_noqa_ceiling_in_scp`: 231 > trần 226 | Log RC | Người thêm noqa except trong scp/ (cần giảm, không nâng trần) |
| R6 | `test_meta_audit.py::test_r2_n03_r7_n02_ga_evidence_artifacts_exist` | Log RC | Hub (GA.md trích artifact không có trên đĩa) |
| R7 | `test_knowledge_reader_tiers_contract.py::test_01/04/05` | Log RC + Strict | Theo orchestrator đã có fix ở `claude/relevance e2a59b39` (chưa kiểm ở lane này) |
| R8 | `test_gd2_lineage_slm.py::test_logs_contain_hash_and_verdict_but_never_raw_text`; `test_gd2_structural.py::test_mutant_m5…` (chỉ windows) | Log RC | Lane L1/gd2 (có thể liên quan model L1 không có trên runner, HYPOTHESIS) |
| R9 | T12 `test_f02_identity_question…`, `test_b01_max_length_8000…`, `test_comb11…` (`'LOOKUP' in ('REASONING','LANE_CHATBOT')`) | Log RC | Lane router/L0-dekeyword |
| R10 | `test_docker_desktop_and_skills_middleware…poisoning_detected_returns_422` (nhận 403) | Log RC | Lane security/middleware |
| R11 | `test_startup_launcher_contract.py::test_launch_bat_has_crlf…` trên ubuntu: `cmd: not found` | Log RC | Chủ test launcher. Test gọi `cmd` trên Linux. Không skip (FA-02); cần chủ test sửa cách kiểm theo OS. |
| R12 | `test_b70…::test_f02_dashboard_binds_loopback_only` (thiếu `dashboard/.next/standalone/server.js`), `test_live_cluster_e2e` (dashboard 3000 quá hạn), `test_brain_console_contract` (exit sớm, ESM) | Log RC | UNKNOWN: có thể do môi trường (dashboard build chạy SAU suite; Node 20 với ESM) nên có thể tự hết khi lên Node 24, cũng có thể là đỏ thật. Cần run GitHub đầu tiên để phân loại. |
| R13 | `goldset S24` (2 case chờ owner) | Theo orchestrator | Owner |
| R14 | Strict `reality_suite` 66/68 | Log Strict (tail bị cắt nên không thấy tên 2 case) | Cần đọc artifact `reports/reality/reality-tests-results.json` của run sau |

**Đỏ do môi trường CI, sẽ hết sau khi sửa (HYPOTHESIS cho tới khi có run GitHub):** 15 test "bun must not be present on PATH"; khoảng 8 test `node: bad option: --experimental-strip-types` / `critical_chain_severed` / `p1_boundary_security`; 5 test T00 semantic "Trusted base 'main'".

## 6. Phần còn UNKNOWN tới khi hub push

1. GitHub có parse được `scp-release-gate.yml` đã sửa không. Rất có thể được, vì gốc lỗi (khóa trùng) đã gỡ và loader chặt xác nhận. Nhưng **chưa có actionlint** trên máy, và tôi không tải về (tải công cụ cần owner đồng ý). ⇒ UNKNOWN.
2. Runner GitHub có sẵn bun trên PATH không, khi không còn setup-bun. Nếu có thì 15 test zero-bun vẫn đỏ ⇒ UNKNOWN.
3. Node 24 trên runner có làm vỡ test dashboard hay brain-console không (R12). Cục bộ là Node 26; Docker audit dùng node:24 ⇒ HYPOTHESIS là ổn.
4. Trivy (CRITICAL/HIGH, `ignore-unfixed`) trên ảnh runtime: `trivy` không có trên PATH của shell này ⇒ chưa chạy. Ảnh runtime ≈ ảnh đã push ngày 19/10 (lúc đó stage audit = runtime + `scripts/`), nên bề mặt rủi ro không lớn hơn (HYPOTHESIS; package hệ thống có thể đã đổi từ đó).
5. Full suite trên runner (khoảng 15 phút): chưa chạy trọn ở máy này. Tôi **không** tuyên bố full suite xanh (FA-03).
6. CI Baseline `ci.yml` timeout 900s: suite mất 881s trên runner. Việc nới timeout là quyết định về ngân sách, không phải về chuẩn kiểm. Workflow này lại là "non-blocking", nên tôi **để nguyên** để không bị coi là hạ chuẩn. ⇒ khả năng cao vẫn đỏ vì timeout; owner/hub quyết.
7. **T00 L3 sẽ vẫn đỏ ở bước WHY Chain** cho lần push `c9aa96e7..` vì 12 commit thiếu chuỗi. Lịch sử main không viết lại được; nâng `epoch_sha` trong `spec/why_chain_policy.json` thì cần người duyệt (file protected) và tương đương miễn trừ 12 commit đó. ⇒ **Owner quyết** (§8).

## 7. Việc hub làm khi push

1. Review rồi merge `claude/ci-release-gate`. **Push** (chỉ hub được làm).
2. Ngay sau push, đọc: `gh run list --branch main --limit 10`. Kỳ vọng: run của `scp-release-gate.yml` mang tên **"SCP Pre-RC Verification (Non-Authoritative)"** (không còn tên đường dẫn file) và **có job chạy**. Nếu vẫn "workflow file issue" thì gốc lỗi còn chỗ khác ⇒ mở lại lane.
3. Container CI: xác nhận log có `[runtime …]` và **không có** `[audit …]`, guard không-.git pass, Trivy pass. Tag `edge`/`sha-…` khi đó mới là ảnh runtime.
4. **Duyệt quyết định Node 24** (cũng là quyết định của RC authority, test `test_rc_workflow_runtime_contract.py` đổi pin 20→24).
5. Trên máy hub chạy `D:/scp/.venv/Scripts/python.exe -X utf8 tools/install_git_hooks.py` để cài hook **commit-msg** (WHY chain) và cập nhật pre-commit (bản hiện tại trỏ `D:/opt/scp-venv-w16`). Đây là cấu hình bền vững trên máy owner, nên lane này không tự làm.
6. Phân loại lại §5 R12 và §6 mục 2–3 từ run đầu tiên; giao R1–R11 cho các lane chủ.
7. Lưu ý **ảnh audit đã nằm trên GHCR** từ các lần publish ≤19/10 (khi đó stage audit chưa COPY `.git`, nhưng có `scripts/`). Owner nên xem lại các tag cũ trên `ghcr.io/checken1994/vietnam-lab`. Lane này không có quyền và không xoá gì.

## 8. Chuỗi Tại Sao (5 phần)

**Câu hỏi gốc: tại sao cổng phát hành đỏ suốt khoảng 12 ngày (RC/Strict từ 13/10, release-gate không chạy job nào từ 24/10) mà không ai thấy?**

1. **GỐC:** VERIFIED: có 3 lớp nguyên nhân đan nhau.
   - (a) Lỗi cú pháp YAML mà công cụ cục bộ không nhìn thấy: PyYAML giữ khóa cuối, nên test hợp đồng workflow dùng `yaml.safe_load` cũng báo ổn.
   - (b) Môi trường CI không được cập nhật khi sản phẩm đổi: zero-bun ngày 18–24/10, node:24 ngày 23/10, E-W1 ngày 23/10. Mỗi thay đổi sửa Dockerfile/test nhưng không sửa workflow.
   - (c) Quy trình merge lấy Docker cục bộ làm nguồn "xanh" và không bao giờ đọc `gh run`. Đỏ trên GitHub không chặn merge, không leo thang, không vào inbox.

   NGUYÊN NHÂN SONG SONG đã xét: thiếu secret (loại trừ, log không có lỗi secret); action biến mất (loại trừ, cùng SHA action chạy ở job khác); test bị nới để xanh (không thấy, độ chặt giữ nguyên); một mình WHY gate gây đỏ (chỉ đúng cho T00 L3, không giải thích release-gate hay container).
2. **SỬA-ĐÚNG:** VERIFIED: `check_workflow_contract.py` trên main cho 17 vi phạm, sau sửa 0. Docker mặc định tái hiện `"/.git/packed-refs": not found`; `--target runtime` exit 0, smoke `/health` ok. Guard đỏ trên ảnh audit, xanh trên ảnh runtime. T00 depth-2 exit 1, fetch đủ exit 0. 7 mutant đều đỏ, 188 test pass.
3. **TIN-BẰNG-CHỨNG:** VERIFIED: lineage độc lập với code của tôi là log GitHub Actions trên runner (run 41101176535, 41101178247, 41101178732, 41101178219, 41101178392, 40120713050, 39232032652), tức môi trường khác máy này. Tái hiện cục bộ khớp đúng thông điệp lỗi của runner. HYPOTHESIS: GitHub chấp nhận file đã sửa. Chưa kiểm độc lập vì không push và không có actionlint.
4. **ĐỔI-QUYẾT-ĐỊNH:** sẽ đổi nếu (i) run đầu tiên sau push vẫn "workflow file issue"; (ii) runner có sẵn bun; (iii) Node 24 làm vỡ test dashboard; (iv) owner muốn Node 20 là chuẩn phát hành (khi đó phải hạ Dockerfile và bỏ test strip-types, không nâng CI); (v) owner quyết đẩy ảnh audit là chủ ý (trái với chính comment trong Dockerfile).
5. **UNKNOWN_WITH_REASON:** kết quả GitHub thật trên SHA mới chưa biết vì lane không được push. Trivy và full suite trên runner chưa chạy được ở máy này. Tên 2 case reality đỏ chưa biết vì log bị cắt. Chưa rõ R12 là đỏ thật hay do môi trường cho tới khi có run với Node 24.

## 9. Lần sau SCP học được gì

1. **Cổng ngoài đỏ liên tục phải tự leo thang.** N run đỏ liên tiếp trên `main` (vd ≥3), hoặc 0 thành công trong 24h, phải sinh `LessonEvent kind=ALARM` / DOUBT và chặn tuyên bố "VERIFIED" cho mọi merge, cho tới khi có người xác nhận. Im lặng 12 ngày là cảnh báo, không phải "không có tin".
2. **"Workflow file issue" là lỗi loại khác hẳn test đỏ:** 0 job chạy nghĩa là cổng *không tồn tại*. SCP cần phân biệt "cổng chạy và đỏ" với "cổng không chạy". Cái sau nguy hiểm hơn, vì nhìn từ ngoài vẫn giống "đỏ như mọi khi".
3. **Parser cục bộ phải chặt bằng parser của nơi thực thi.** Một kiểm tra dùng `safe_load` mù đúng loại lỗi mà GitHub từ chối. Bài học chung: oracle cục bộ phải có cùng ngữ nghĩa với Reality đích, nếu không thì PASS cục bộ là đồng thuận ảo.
4. **Sửa sản phẩm phải kéo theo sửa môi trường kiểm.** Zero-bun, node:24 và E-W1 đều sửa Dockerfile/test mà không sửa workflow. Bất biến "CI dùng đúng runtime sản phẩm ship" giờ đã thành máy kiểm (`check_workflow_contract.py`, Node = Dockerfile).
5. **Sửa triệu chứng có thể mở lỗ to hơn.** Sửa "packed-refs not found" theo cách thẳng nhất sẽ đẩy lịch sử git lên registry. Trước khi vá phải hỏi "tại sao bước này cần .git" thì mới thấy publish nhầm stage.
6. **Hook là một phần của cổng.** WHY chain có 3 điểm thực thi nhưng điểm 1 (commit-msg) chưa từng được cài trên máy hub, nên mọi commit đều lọt và chỉ bị bắt ở CI, mà CI thì không ai đọc. Health check của SCP nên tự kiểm `git rev-parse --git-path hooks`/commit-msg có tồn tại và đúng nội dung installer không.

## 10. Toàn vẹn

- Bản sao: `docs/evidence/ci-release-gate-done.final.md` trên nhánh `claude/ci-release-gate`, sha256 ghi trong commit docs và trong báo cáo cuối.
- Log thô (scratch, không commit): `C:/Users/check/AppData/Local/Temp/claude/D--scp/d38302f7-a0b1-4a9c-91fd-d0ffb01a10bc/scratchpad/cigate/` (`red_before.txt`, `docker_default_target.log`, `docker_runtime_target.log`, `t00_shallow2.txt`, `t00_full.txt`, `local_gate_runs.txt`, `pytest_final.txt`, `audit_deps.txt`, `commit1_hook.txt`) và `scratchpad/run_<id>.log`, `rg_40120713050.log` (log GitHub, có giá trị ngẫu nhiên tạm của mỗi run, không chia sẻ ra ngoài).
