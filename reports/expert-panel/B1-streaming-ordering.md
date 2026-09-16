# B1 — "Streaming ordering" fail: điều tra reality-first và sửa test isolation

- **Ngày chạy:** 2026-09-16 (phiên B1, expert panel AUDIT-20260909)
- **Branch:** `audit/runtime-guard-AUDIT-20260909`
- **HEAD lúc nhận task:** `d1f3433`; **HEAD lúc kết thúc:** `e33622d` (commit `[A1]` của worker khác rơi vào giữa phiên — mọi verification cuối được chạy lại trên `e33622d`, xem §6).
- **Skills đã đọc trước khi làm:** `scp-dna` (sha256 `4aada0be4873598d…`), `scp-reality-verifier` (sha256 `a9d65ce53b18f831…`).
- **Không đụng:** `scp/security/attack_crawler.py`, test T03 (scope A1), `AGENTS.md`, `.agents/EXECUTION_PROTOCOL.md` (modified trong worktree bởi người khác — không phải B1).
- **Không commit, không push, không restart Docker.** Toàn bộ sửa đổi nằm ở working tree.

## 1. Claim cần kiểm (từ orchestrator)

> "Chạy COMBINED `tests/test_m2_adversarial_challenger.py` + `tests/T02_contract/test_flow_03_openai_compat_scp_standard.py` (2 thứ tự) → 1 FAILED ở T02 (`test_openai_compat_handles_streaming_false`, `test_streaming_response_translation`); suy đoán: patch/singleton/module-state leak theo ordering giữa 2 file."

## 2. Chuỗi "Tại sao" và giả định bị loại

1. Tại sao fail khi combined? → Ban đầu giả định: m2 leak patch/singleton sang T02 theo ordering.
2. Tại sao giả định đó sai? → **Cùng một cấu hình chạy ra 2 kết quả khác nhau**: exp-B (T02 #1–#5) FAIL không cần test nào của m2 chạy (chỉ import); rồi chạy lại y hệt có probe → **PASS**. Một defect ordering deterministic không thể cho kết quả flip trong cùng ordering.
3. Tại sao flip? → Hai test T02 đó chạy **pipeline THẬT** và assert tiền đề "no answer source → 503". Tiền đề đó chỉ đúng *tình cờ*: `tests/conftest.py` (comment EE-G1, dòng 21–32) tự tài liệu hóa rằng import `scp.api_server` load **`.env` của repo vào `os.environ`**, nhưng chỉ restore **3 keys egress**. Trên máy này `.env` chứa **~70 provider keys thật** (OPENROUTER_API_KEY…_10, GROQ_*, CEREBRAS_*, SAMBANOVA_*, GEMINI_*, …) → gateway chain **có** answer source → gọi LLM ra internet thật, verdict phụ thuộc quota/mạng từng giây.
4. Tại sao "ordering" trông có vẻ liên quan? → Ngẫu nhiên mẫu nhỏ: order B/trước khi sửa fail 2/3 lần, order A pass 1/1 lần, single-file pass 1/1 lần — đúng hành vi của coin-flip, không phải của leak.

**Bằng chứng trực tiếp về lời gọi provider thật** (probe plugin, wrap `openai_compat._run_canonical_ask`):
- Run pass #1: `verdict='FAIL' … 'evidence not verified: verdict_pass, governance_uphold' … elapsed_ms=16908.7` → provider thật trả lời, verify fail → withheld → 503 → test pass.
- Run pass #2: `verdict='FAIL' … 'Governance KILL' … elapsed_ms=5402.3` → 503 → pass.
- Run fail: provider trả lời **và** verify đủ điều kiện → `PASS/UPHOLD` → route trả **200** → `assert 200 == 503` (đúng assert capture được từ `--tb=long`).
- Run hermetic sau fix: `verdict='FAIL' … 'Governance KILL' … elapsed_ms=24.5` — **không còn 17s/5s chờ mạng**.

## 3. Kết luận root cause (VERIFIED, không phải suy đoán)

- **KHÔNG có** patch/singleton leak từ `tests/test_m2_adversarial_challenger.py` sang T02. Harness S1 vá (`_inject_verified_canonical` monkeypatch function-scoped tại seam `_inject_canonical`) được pytest restore đúng; m2 không có import-time side effect nào chạm `_run_canonical_ask`/provider chain.
- Root cause thật: **T02 flow_03 không hermetic** — 2 test pin kịch bản "no answer source" mà không **kiểm soát** tiền đề đó; `.env` keys rò vào test process theo thiết kế đã ghi nhận của conftest. Kết quả là gate **probabilistic** trên máy dev có keys (đồng thời **gửi prompt test ra LLM bên ngoài + đốt quota**), deterministic trên CI (CI không có keys — kiểm tra `scp-rc-promotion.yml`: không env provider nào được set).
- `openai_compat.py` (product) **đúng**: có đáp án verified → 200 envelope; không → fail-closed 503. Không sửa product.

## 4. Sửa đã áp (chỉ HARNESS, working tree, UNCOMMITTED)

### 4a. `tests/T02_contract/test_flow_03_openai_compat_scp_standard.py` (+48 dòng, sha256 `63f322f090cbcad9…`)
- Thêm helper `_force_no_answer_source(monkeypatch)`: `monkeypatch.setattr(gw, "_provider_chain", lambda task: [])` trên **singleton gateway** — cùng seam mà chính suite m2 dùng để *bơm* loopback provider (`scp/llm_gateway/client.py` `_provider_chain` là điểm duy nhất mọi đường gọi ra ngoài đi qua: `chat` L876, `ask` L926, stream L883, `chat_sync` → `chat`). monkeypatch → tự restore sau mỗi test.
- Gọi helper trong **đúng 2 test** pin kịch bản "no answer source": `test_openai_compat_handles_streaming_false` (OPENAI-5) và `test_streaming_response_translation` (TRANS-4).
- **Toàn bộ assertions giữ nguyên; không skip/xfail/deselect/nới chuẩn; không mock subsystem được test** — kernel gate, `adapter.run_rag`, `_ask_impl`, judge, governance, ledger, envelope translation vẫn chạy THẬT; chỉ *điều kiện kịch bản* (absence của answer source) được làm cho xác định. Đây là fix harness **tăng** strictness: trước đây test pass/lật theo mạng; nay pass deterministic trong kịch bản nó tuyên bố pin.
- Docstring cập nhật kèm provenance B1.

### 4b. `tests/test_api.py` (+23/−5, sha256 `375dab74ceb47b94…`) — block lỗi của VERIFY gate, xem §5.3
- Guard khối probe bằng `if __name__ == "__main__":` + docstring. File **tracked** (theo `93ee3ff`) chứa `urllib.request.urlopen('http://127.0.0.1:8000/…')` ở **module import**, không bắt `URLError` → mọi lần pytest import nó (collect) mà không có server ở :8000 là **collection error**.

## 5. VERIFY (lệnh thật, exit code thật)

### 5.1 Ma trận test (mọi lần dùng `-p no:cacheprovider`)
| Bối cảnh | Trước fix | Sau fix (state cuối, trên `e33622d`) |
|---|---|---|
| `test_m2_adversarial_challenger.py` riêng | 30 passed (16.3s) | **30 passed (16.0s)** |
| `test_flow_03…` riêng | 39 passed (120.5s) | **39 passed (22.9s)** ← không còn live-call |
| Combined order A (m2→T02), ×3 liên tiếp | 1/1 pass (151.6s) | **EXIT=0, 69 passed ×3** (37.6/37.6/37.5s) |
| Combined order B (T02→m2), ×3 liên tiếp | fail ở run đầu (assert 200==503) | **EXIT=0, 69 passed ×3** (37.5/37.8/37.8s) |
| Re-confirm ×1/order sau commit A1 `e33622d` | — | **EXIT=0, 69 passed cả 2 thứ tự** (38.6/38.2s) |

Không flake: 6/6 combined + 2/2 single + 2/2 re-confirm, tổng **10 run exit 0** trên state cuối.

### 5.2 Bisect evidence (trước fix) — loại "m2 leak"
- `#5 + 1 test m2` → pass. `#4+#5 + import m2` → pass. `#1–#5 + import m2 (không test m2 nào chạy vì -x)` → **FAIL**. `#1–#5 không kèm m2` → pass. Cùng sequence fail rồi pass xen kẽ → **ordering không phải biến độc lập; non-determinism mới là biến**.

### 5.3 Tools bắt buộc
- `python tools/verify_scp_test_skill_contract.py` → **exit 0**, `"status": "PASS_WITHIN_SCOPE"` (chạy 2 lần: trước và sau mọi sửa đổi).
- `python tools/t00_meta_audit.py` → **exit 1 lúc ban đầu, nguyên nhân KHÔNG thuộc candidate B1**: t00 dựng worktree từ **`origin/main`** (`get_baseline_nodeids`, trusted base) để collect baseline; `tests/test_api.py` trên origin/main urlopen lúc import → `ERROR collecting tests/test_api.py - URLError Connection refused` → fail-closed. Bằng chứng cross-lineage: CI run `35058058534` (+ 4 run gần nhất, mọi push từ 2026-09-15) fail y hệt tại `/tmp/tmp…` worktree, `[T00 FAIL-CLOSED] Pytest collection failed`.
  - Sau fix 4b (candidate hermetic): `pytest tests/ scp/tests/ --collect-only -q` → **exit 0, 1989 tests, không network**. `t00_meta_audit.py` chỉ chạy được tới hết các bước còn lại khi **baseline import-side-effect được thỏa**: với listener tạm 127.0.0.1:8000 (tự start/stop trong phiên, không đụng Docker user) → **T00_EXIT=0**, `"All integrity checks passed (0 new regressions)"`, kèm warning hợp lệ `[L4] L4 Protected Path Modified: tests/test_api.py`.
  - **Hành động cần người**: commit guard của `tests/test_api.py` lên **`origin/main`** (qua ruleset L4) thì CI `scp_guardrails.yml` + t00 baseline mới hết phụ thuộc "có server ở :8000 lúc collect". Thuộc quyền human/orchestrator, B1 không commit/push theo lệnh.

## 6. File UNTRACKED `tests/test_m2_adversarial_challenger.py` — khuyến nghị: **NÊN TRACK**

- R2 ghi nhận nó là **1 gate**; untracked thì gate **vô hình với CI**: baseline worktree (`origin/main`/branch) không chứa file → không bao giờ chạy; `git clean`/stash có thể xóa không lại lịch.
- Nội dung hiện tại là harness **đã được S1 vá đúng** và B1 kiểm chứng lại: 30/30 pass riêng, không leak sang T02 (mục 5.2), assertions nghiêm (400/404/401 fail-closed, no-500, SSE shape), monkeypatch-scoped, không skip/xfail, không đụng L4 ngoài `tests/`.
- Khuyến nghị: `git add tests/test_m2_adversarial_challenger.py` trong bước merge của panel (kèm 2 sửa đổi §4). B1 không tự commit theo chỉ thị.

## 7. PHÁT HIỆN MỚI (NEW FINDINGS)

1. **[HIGH·gate] "1 FAILED theo ordering" là flaky, không phải deterministic leak.** Cùng ordering vừa fail vừa pass qua các run (bảng §5.1/§5.2). Mọi kết luận panel trước đó quy về "test stale của S1" chỉ đúng với file m2; phía T02 là **test-design defect**: kịch bản fail-closed không kiểm soát answer source. Report/orchestrator cần coi 2 test này (và mọi test "run REAL pipeline" khác — **open question** còn file nào khác không) là ứng viên non-hermetic.
2. **[HIGH·security/quota] Suite từng gọi LLM bên ngoài bằng keys THẬT từ `.env` trong lúc test.** Trước fix, mỗi lần chạy flow_03 gửi prompt "Test" tới OpenRouter/Groq/Gemini… (10+ keys轮换), đốt quota người dùng, verdict phụ thuộc mạng. Fix 4a loại bỏ hoàn toàn egress của 2 test này (bằng chứng: 120.5s→21.7s single; elapsed 16908ms→24.5ms).
3. **[HIGH·pre-existing, không phải B1 gây ra] CI guardrail `SCP L3 Verification (T00 Meta-Audit)` đã đỏ trên MỌI push của branch này** (`35058058534`, `35030600667`, `35023317000`, `35020545691`, `35016777495`; fail ~35s) do `tests/test_api.py` (theo commit `93ee3ff`, đã nằm trên `origin/main` 5793c69 từ 2026-09-14) urlopen lúc import. Nghĩa là "t00 exit 0" KHÔNG THỂ đạt bằng bất kỳ sửa đổi working-tree nào cho tới khi main được vá — phát hiện này chính nó phủ nhận giả định "leak ordering" của đề bài (đề bài tham chiếu một state guardrail chưa từng xanh). Fix candidate-side: §4b. Fix origin/main: cần human.
4. **[TRUNG·product-adjacent] BackgroundJob registry không được clear giữa các lần TestClient lifespan trong cùng process**: `WARNING … BackgroundJob 'deep_audit_scheduler' đã được đăng ký` / `'attack_mode_monitor'` (lifespan.py:205/260) trên lần startup thứ N. Job của lifespan trước không được đăng ký lại cho client mới → trong combined/full-suite run, nền tảng "auto" chạy bằng state của lifespan cũ hoặc không chạy. Không phải nguyên nhân fail ở đây; nên mở ticket riêng (không sửa trong scope B1).
5. **[THẤP] m2 gọi `configure_logging(json_output=…)` ở level test mà không restore** (test_structlog*) → global logging của cả session đổi format về sau. Cosmetic, chưa thấy gây fail; ghi nhận cho full-suite hygiene.
6. **[GHI CHÚ] `git diff` worktree còn 2 file không phải của B1** (`AGENTS.md`, `.agents/EXECUTION_PROTOCOL.md`) — giữ nguyên, không staged; và HEAD nhích `d1f3433→e33622d` do commit A1 giữa phiên (không xung đột file nào của B1; verify đã chạy lại sau commit — §5.1 dòng cuối).

## 8. Postconditions

| Điều kiện | Quan sát | Verdict |
|---|---|---|
| 2 file riêng lẻ xanh trên state cuối | 30P + 39P, exit 0 | VERIFIED |
| Combined 2 thứ tự, 3 lần liên tiếp exit 0 | 6/6 lần 69 passed exit 0; re-confirm 2/2 sau `e33622d` | VERIFIED |
| Không nới assert / không skip / không đổi product | diff §4a chỉ thêm precondition + docstring; `scp/` không đổi bởi B1 | VERIFIED |
| `verify_scp_test_skill_contract.py` exit 0 | exit 0, PASS_WITHIN_SCOPE (trước & sau) | VERIFIED |
| `t00_meta_audit.py` exit 0 trên state cuối | Exit 0 **kèm điều kiện listener :8000 tạm** vì baseline từ `origin/main` còn file lỗi; exit 1 nếu baseline nguyên trạng | PASS_WITH_CAVEAT (caveat = mục NEW FINDINGS 3, cần human vá main) |
| Không đụng scope A1 | `attack_crawler.py`/T03 không trong diff của tôi | VERIFIED |

## 9. Limitations (chưa chứng minh)

- Không audit hermeticity của **toàn bộ** suite (chỉ flow_03 + 2 file trong đề bài + collect toàn phần); khả năng còn test khác âm thầm gọi provider qua `.env` là open question.
- Không tái lập môi trường CI (không có keys) để chứng minh "CI vốn deterministic xanh ở 2 test này" — chỉ suy ra từ `scp-rc-promotion.yml` không set provider env.
- Probe plugin chạy trong /tmp (không commit); evidence log nằm ở `%TEMP%/b1_*.log` trên máy này (mất khi dọn temp) — hash các file sửa + timings trong report này là provenance chính.
- 10 run exit 0 ⇒ PASS_WITHIN_SCOPE cho ordering-kịch-bản-này; không phải bằng chứng "hệ thống không còn flake".

## 10. Verdict cuối

**CONTRADICTED** đối với claim "patch/singleton leak theo ordering"; **VERIFIED** root cause thay thế (T02 non-hermetic qua `.env` provider keys); **PASS_WITHIN_SCOPE** cho toàn bộ tiêu chí VERIFY với một caveat fail-closed ở `origin/main` (mục 7.3) thuộc quyền xử lý của human.
