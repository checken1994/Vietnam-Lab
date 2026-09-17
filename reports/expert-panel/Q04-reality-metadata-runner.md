# Q04 — Reality metadata pin, python-shim runner, stale markers

- Worker: Q04 | Branch: `audit/runtime-guard-AUDIT-20260909` | HEAD snapshot: `29dc446` (working tree, KHÔNG commit)
- Ngày: 2026-09-15 | Skills đã đọc: `scp-dna` (SKILL.md) + `scp-reality-verifier` (SKILL.md)
- File đã sửa: `dashboard/src/app/api/scp/status/route.ts` (chỉ metadata pin), `tests/run-reality-tests.sh`, `tests/reality-check.sh`
- File KHÔNG sửa: `tests/reality-tests/reality_4-c-006.py` (assertion giữ nguyên 100% — pin nằm ở route.ts, test chỉ đối chiếu)

## 1. Problem statement + Why chain

**P1 — Pin LOC drift (reality_4-c-006 FAIL):** Test fail vì tổng pin `5007` ≠ `wc -l` thật `5003`.
- Tại sao pin lệch? → Vì S17 (commit `09390a0`, 2026-09-13) đo đúng reality lúc đó (`git show 09390a0:...callgraph_delta.py | wc -l` = 751).
- Tại sao giờ 747? → Commit `a2edec2` (2026-09-14, S33/S35 gateway) rewrite docstring TODO→[WIRED] trong `callgraph_delta.py` (diff: +4/−8 dòng). 5 file còn vẫn khớp pin (924/806/847/784/895 ✓, đo từng file).
- Kết luận: gate **đang hoạt động đúng thiết kế** (bắt drift sau khi pin đóng). Fix đúng = cập nhật pin + date theo procedure mà chính test chỉ dẫn ("Update LAST_VERIFIED_FALLBACK_LOC and LAST_VERIFIED_DATE"). Không nới assertion, không skip.

**P2 — Runner 0 passed/75 failed:** `run-reality-tests.sh:2` chọn python3 chỉ bằng `command -v python3`. Trên Windows box này `python3` trỏ tới shim WindowsApps: probe thực nghiệm `python3 -c 'import sys'` → **exit 49**, trong khi `python` = 3.12.10 thật. Mọi script fail tức thì → 0/75 giả.

**P3 — Marker stale trong reality-check.sh:** 5 marker FAIL + 2 marker PASS-VACUOUS. ROOT CAUSE (audit từng marker, không đoán):
- `global _judge`: refactor `bc40dcc` ("GOD split") dời `get_judge()`/`global _judge` từ `api_server.py` sang `scp/api_server_parts/helpers.py`. Property gốc (module-scope `_judge` có backing, chống NameError /ask 500) **vẫn đúng** — chỉ vị trí code đổi.
- llm-bridge (`4-d-004b/005/005b/009b`): refactor `[Z4]` biến `index.ts` thành shell 4 dòng (`import "./zero_cost_bootstrap"`); server code thật ở `core.ts` (1081 dòng) — recursion header (`headers.get("X-LLM-Bridge-Internal") === "1"`, core.ts:802), `TASK_MODEL_MAP` (core.ts:150), `OPENROUTER_MODEL_AUTOFIX` (core.ts:152/157), bind `ZAI_BRIDGE_HOST ?? "127.0.0.1"` (core.ts:193) + `Bun.serve({ hostname: HOST })` (core.ts:1009) **tất cả còn nguyên**.
- Gate phủ định `4-d-004`, `4-d-009a` grep trên stub 4 dòng → **xanh rỗng** (blind spot nguy hiểm hơn cả marker đỏ).

## 2. Changes (mỗi thay đổi giữ hoặc TĂNG độ nghiêm)

| # | File | Thay đổi |
|---|---|---|
| 1 | `dashboard/src/app/api/scp/status/route.ts` | `callgraph_delta.py` pin 751→747; `LAST_VERIFIED_DATE` → `2026-09-15 (Q04 post-a2edec2 drift refresh)`; thêm comment history Q04. Logic `computeAutofixLoc` không đụng. |
| 2 | `tests/run-reality-tests.sh` | Port block S26 từ reality-check.sh: tôn trọng `SCP_PYTHON_BIN`; chỉ dùng `python3` nếu `python3 -c "import sys"` exit 0; ngược lại `python`. |
| 3 | `tests/reality-check.sh` | Thêm `BRIDGE_DIR`; retarget `4-a-001a`→`_judge: RealityJudge` trong api_server.py + **gate mới** `4-a-001a2` (`global _judge` helpers.py), `4-a-001a3` (`^_judge = None` backing — NameError guard); `4-d-004/004b`→core.ts (positive dùng pattern runtime `headers.get(...)`, không chỉ header-name), thêm `4-d-004d` (self-target phủ egress-url.ts), `4-d-004c` (index.ts phải import bootstrap — chống xanh-rỗng-stub tương lai); `4-d-005/005b`→core.ts; `4-d-009a/009b`→core.ts + **gate mới** `4-d-009c` (`hostname: HOST` chứng minh Bun.serve thật sự consume HOST). Không xóa/skip/xfail gate nào. |

## 3. Evidence table (số THẬT, tái hiện được trên working tree @29dc446)

| Bước | Lệnh | Trước | Sau |
|---|---|---|---|
| E1 | `bash tests/run-reality-tests.sh` | `0 passed, 75 failed, 1 retired` exit **1** | `75 passed, 0 failed, 1 retired` exit **0** |
| E2 | `python tests/reality-tests/reality_4-c-006.py` | `AssertionError: 5007 ≠ 5003` exit 1 | `PASS [4/4] ... 5003 == 5003 — no drift` exit 0 |
| E3 | `wc -l` 6 file v4 | 924+806+847+747+784+895 = **5003** (callgraph = HEAD `a2edec2`, worktree sạch) | (không đổi — chỉ pin đổi) |
| E4 | `git show 09390a0^:.../callgraph_delta.py \| wc -l` | 751 → pin S17 đúng tại thời điểm đóng; `git diff --stat 09390a0 a2edec2` = 4(+)/8(−) → drift hợp pháp sau pin | — |
| E5 | `bash tests/reality-check.sh` | 5 marker ✗ (4-a-001a, 4-d-004b, 4-d-005, 4-d-005b, 4-d-009b) + ✗ reality_4-c-006 exit 1 | `177 passed, 0 failed` exit **0** (TierA 30, Phase2 76, còn lại static) |
| E6 | `python tools/t00_meta_audit.py` | — | exit **0** "All integrity checks passed (0 new regressions)"; cảnh báo L4 = informational (tôi sửa protected-path với tư cách worker được ủy quyền, không commit) |
| E7 | `python tools/verify_scp_test_skill_contract.py` | — | exit **0** `"status": "PASS_WITHIN_SCOPE"` |
| E8 | Shim probe | `command -v python3`→WindowsApps, `python3 -c import sys` exit 49; `python`→3.12.10 | override `SCP_PYTHON_BIN=python` resolve đúng `python` |
| E9 | `python -m pytest reality_4-c-006.py` | — | `no tests ran` — **KHÔNG phải PASS** (script dạng `main()`, không có `test_*`); evidence thật = E2/E1 |
| E10 | Docker `python:3.12-slim` (Linux) | — | `linux python3 OK 3.12.14` → nhánh chọn python3 hoạt động; runner: `65 passed, 10 failed` — **mọi fail container là do image thiếu dep** (`httpx`, `tenacity`, runtime `bun`), không phải red thật của suite |

## 4. PHÁT HIỆN MỚI (NEW FINDINGS)

| # | Vị trí | Severity | Nội dung | Next slot |
|---|---|---|---|---|
| NF-1 | `tests/run-reality-tests.sh:40-42` vs `tests/reality-check.sh:227-230` | LOW | Hai runner có semantics retirement **phân kỳ**: runner skip `reality_4-c-022.py` (RETIRED list), Phase 2 reality-check.sh **vẫn chạy** script đó (hôm nay green nên vô hại, nhưng khi premise retirement gãy, 2 cổng cho 2 verdict khác nhau). Nên share một skip-list duy nhất. | Harness owner / Q04 follow-up |
| NF-2 | `scp/api_server_parts/lifespan.py:98` | LOW | `global _judge` không có khai báo module-level `_judge` backing trong chính file (harmless vì gán trước khi đọc, nhưng là họ hàng xa của root-cause 4-a-001 gốc — silent-attr risk nếu ai đó read `lifespan._judge` trước startup). Ngoài scope sản phẩm Q04 — chỉ report. | Group A / api-split owner |
| NF-3 | `tests/reality-tests/reality_4-b-007.py`, `4-e-001.py`, `4-d-007/008/009/019/023/024` (inventory từ E10) | LOW | 10 reality test có **undeclared environment dependency** (httpx, tenacity, bun). Trên env tối giản fail vì thiếu dep chứ không vì product. Runner không phân loại "dep-missing" vs "assertion-red". | CI docs / reality-harness |
| NF-4 | `tests/*.sh` worktree Windows | INFO | `git ls-files --eol`: index=LF, worktree=CRLF (autocrlf Windows). Không phải bug repo; nhưng mount worktree Windows vào container Linux thì bash fail trên `\r` (tôi normalize trong E10 bằng `sed 's/\r$//'`, chỉ trong container, không sửa file). | Không cần slot — ghi chú vận hành |
| NF-5 | Toàn bộ `tests/reality-tests/` sau fix | — | **Không còn file reality nào đỏ ngoài scope Q04** trên profile Windows-host (E1: 75/0/1). Không nâng cấp thành "suite production-ready" — PASS_WITHIN_SCOPE (DNA #22). | — |

## 5. Causal map (tóm tắt)

```
S17 pin đúng lúc đóng (09390a0, 09-13) ── a2edec2 (09-14) sửa callgraph_delta −4 LOC ──► pin 5007 ≠ 5003
        reality_4-c-006 bắt đúng (fail-closed, designed) ──► Q04 cập nhật pin theo procedure của test ──► E2/E5 green

WindowsApps python3 shim (exit 49) ── runner chỉ probe `command -v` ──► mọi script fail tức thì (0/75)
        port S26 pattern (probe `python3 -c import sys`) ──► E1 75/0; E10 xác nhận nhánh python3 trên Linux

bc40dcc (GOD split) dời get_judge → helpers.py ─┐
[Z4] refactor biến index.ts thành shell 4 dòng, ─┤── 5 marker grep sai file → ĐỎ; 2 gate phủ định grep
server code thật sang core.ts ─────────────────┘    stub → XANH RỖNG (blind spot)
        audit từng property trong source hiện tại (grep count 3/8/3/11, negatives 0 hits code-only)
        → retarget về file thật + thêm 4 gate backing (a2/a3, 004c/004d, 009c) → E5 177/0
```

## 6. Diff stat (chỉ scope Q04)

```
 dashboard/src/app/api/scp/status/route.ts |  9 +++--
 tests/reality-check.sh                    | 58 +++++++++++++++++++++++--------
 tests/run-reality-tests.sh                | 14 +++++++-
 3 files changed, 64 insertions(+), 17 deletions(-)
```
`tests/reality-tests/reality_4-c-006.py`: 0 dòng thay đổi. Không commit (theo chỉ đạo). Các file `M` khác trong `git status` là của worker khác — không đụng.

## 7. Limitations / open questions

- Evidence level A–B (static grep + `wc` + runner exit code). Tier B runtime (`--runtime`, services trên port 8000/11434/3030/3000) **không chạy** — backend không bật; RT-001..006 chưa chứng minh trong phiên này.
- E1/E5 chạy trên working tree @`29dc446` + dirty changes của worker khác (6 file v4 pin đang SẠCH nên pin ổn định với HEAD; nếu worker khác sửa tiếp `callgraph_delta.py`... drift sẽ được chính gate bắt lại — đúng thiết kế).
- Docker E10 dùng image bare, chưa install `requirements.txt`/bun → con số 65/10 là inventory-dependency, không phải regression; một Linux-CI đủ dep sẽ cho kết quả khác (chưa kiểm chứng vì ngoài phạm vi "script/metadata level").
- `LAST_VERIFIED_DATE` format là tự do (test không parse) — chọn theo convention S17 sẵn có.
- Chưa kiểm tra dashboard Next.js build với route.ts sửa (thay đổi chỉ comment + number literal trong Record; `bash`-level gates green; TypeScript compile không nằm trong profile Q04).

**Verdict: PASS_WITHIN_SCOPE** — 3 finding của Q04 đã sửa theo reality hiện tại; toàn bộ gate được giữ hoặc tăng số lượng assertion; không có fail bị che/skip/nới; bằng chứng tái hiện bằng lệnh ghi tại E1–E10.
