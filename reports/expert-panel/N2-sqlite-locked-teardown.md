# N2 — sqlite3 "database is locked" tại teardown flow_06 (AUDIT-20260909)

Worker: N2. Branch: `audit/runtime-guard-AUDIT-20260909`.
Repo: `C:\Users\check\Downloads\scp`. KHÔNG commit (theo chỉ đạo).

## 0. Scope & snapshot (provenance)

| Trường | Giá trị |
|---|---|
| HEAD lúc bắt đầu (reproduce pre-fix) | `9c7a69d` (commit Q02) |
| HEAD lúc verify (post-fix) | `36bb181` (Q07 commit giữa session — branch động, nhiều worker) |
| Working tree | dirty do các worker khác (flow_14, ssrf_sweep, group-A…); N2 chỉ tạo DUY NHẤT `tests/T03_capability/conftest.py` (sha256 `a9aac087242b1fa6…`) + report này |
| Test profile | `python -m pytest -q tests/T03_capability/test_flow_06_prediction_scp_standard.py -p no:cacheprovider` |
| Python / pytest | 3.12 / 9.1.1 |
| Điều kiện môi trường | **Production server `python -m scp 8000` (PID 4844) đang chạy suốt session**; một pytest T01+T02 (PID 81616) chạy song song ở đầu session — cả hai ghi cùng file `data/v13.db` |

## 1. Problem statement

`tests/T03_capability/test_flow_06_prediction_scp_standard.py:114` — teardown fixture autouse
`_cleanup_prediction_fixtures` gọi `db_exec("DELETE FROM predictions WHERE source LIKE ?", ("m6-fixture-%",))`
dying with `sqlite3.OperationalError: database is locked` tại `scp/core/db_manager_parts/db_exec.py:27`.
Pre-existing (Q02 đã xác nhận không phải regression của Q02). Chặn full pytest regression cuối.

## 2. Why chain (đã đi đến tận gốc, không vá triệu chứng)

1. **Tại sao DELETE fail?** Vì `conn.execute(DELETE)` chờ write lock quá `busy_timeout=30000` → SQLite trả SQLITE_BUSY.
2. **Tại sao chờ 30s?** `_db_lock` (RLock) chỉ serialize writer **trong một process**. Ghi ngoài process không bị lock này chặn.
3. **Tại sao có writer ngoài process?** `scp/core/db_manager.py:24` hardcode `DB_PATH = <repo>/data/v13.db` — chính là file DB **production**. Server `python -m scp 8000` (PID 4844) chạy lifespan thật: learning threads, doubt-cron, why-verify, batch flush, và các kết nối raw `sqlite3.connect` riêng (`startup_optimizer.optimize_sqlite_wal`, `FastLearningEngine._init_kb`, `github_backup`, `auto_backup`) đều có thể giữ write transaction trên cùng file. Cộng thêm mọi tiến trình pytest khác trên cùng checkout (PID 81616 được quan sát trực tiếp).
4. **Tại sao teardown là nơi nổ?** Test body có thể đọc (WAL reader không bị writer chặn); chỉ write (`DELETE`) mới cần write lock → lỗi hội tụ ở teardown.
5. **db_exec có bug lifecycle không?** KHÔNG: `db_exec` commit/rollback đúng và luôn giữ `_db_lock` (đã read kỹ). Đây là lỗi **thiết kế dùng chung file DB production với unit tests**, không phải lỗi connection-leak trong helper. → Không sửa `db_exec.py` (giữ semantics; sửa ở đây sẽ là vá sai chỗ).

## 3. Causal map

```
python -m scp 8000 (PID 4844, production, chạy thật)      pytest T01/T02 (PID 81616)
        |  background writers (db_exec, _get_path_conn            |
        |  "data/v13.db" relative, raw sqlite3 checkpoint)        |
        v                                                         v
   +---------------------------- data/v13.db (MỘT FILE duy nhất) ----------------------------+
        ^                                                         ^
        | _db_lock CHỈ serialize trong-process                    |
   pytest flow_06 (process của N2)                          pytest flow_06 (process khác)
   setup: seed qua Predictor (INSERT, ghi file PRODUCTION)
   test body: HTTP + reads
   teardown L114: db_exec(DELETE) ── cần write lock ── busy_timeout 30s hết
        └─> sqlite3.OperationalError: database is locked (db_exec.py:27)   [LỖI QUAN SÁT]
```

Missing piece (nói rõ, không đoán): không thể post-hoc quy kết 100% mỗi lần stall 30s trong run #1 là do server PID 4844 hay PID 81616 hay writer relative-path khác — cần instrumentation writer-side để quy kết từng vụ. **Lớp nguyên nhân** (cross-process contention trên file dùng chung) đã chứng minh deterministic ở mục 4.

## 4. Evidence chain (số THẬT, lệnh thật)

### 4.1 Pre-fix (HEAD `9c7a69d`, server đang chạy)

| # | Lệnh | Kết quả |
|---|---|---|
| E1 | `python -m pytest -q tests/T03_capability/test_flow_06_prediction_scp_standard.py -p no:cacheprovider` | `8 failed, 15 passed, 23 errors in 1999.30s (0:33:19)` — ERROR đúng bằng tổng số test = mọi teardown autouse fail |
| E2 | single test `--tb=long` (pre-fix) | `ERROR at teardown of _cleanup_prediction_fixtures` → `db_exec("DELETE FROM predictions WHERE source LIKE ?", ('m6-fixture-%',))` → `scp\core\db_manager_parts\db_exec.py:27: OperationalError` — `sqlite3.OperationalError: database is locked`; `1 passed, 1 error in 37.25s` |
| E3 | quan sát env | `tasklist`: PID 4844 `python -m scp 8000`, PID 81616 `pytest tests/T01_boot/... tests/T02_contract/...`, PID 55116 = run N2; CPU của pytest N2 delta=0/8s → đang ngủ trong busy handler |

### 4.2 EXP-2 — chứng minh cơ chế, deterministic, KHÔNG đụng DB production

Copy `data/v13.db` → `%TEMP%\n2_exp2\v13copy.db` bằng SQLite backup API (thành công ngay cả khi server đang ghi — đồng thời proof tính khả thi của fix).
- Process A (holder): `BEGIN IMMEDIATE` + giữ 45s.
- Process B (probe): kết nối đúng semantics `get_db()` (`timeout=30.0`, `PRAGMA busy_timeout=30000`) rồi chạy đúng câu DELETE của teardown.
- Kết quả: `PROBE: OperationalError 'database is locked' after 32.9s` (exit 3). → Cơ chế cross-process được chứng minh độc lập với pytest.

### 4.3 FIX (HEAD `36bb181`, server VẪN đang chạy suốt các run)

Fix: **isolation DB per-pytest-session** — `tests/T03_capability/conftest.py` mới:
- `pytest_configure` (trước khi test module import `scp.*`): backup API copy `data/v13.db` → tmp dir riêng, patch `db_manager.DB_PATH` + `_wire_parts()`.
- Guard fail-loudly nếu `_persistent_conn` đã mở (redirect chạy muộn → RuntimeError, không âm thầm chạy chung file).
- Retry 8×1.5s cho backup read (chịu được `wal_checkpoint(TRUNCATE)` của server), rồi fail loudly.
- Session fixture autouse đóng connection cuối phiên + `rmtree` tmp.
- Kill-switch debug `SCP_T03_DB_ISOLATION=off` (in ra config warning, không im lặng).
- Test file flow_06 **không sửa dòng nào** — giữ nguyên teardown/assertions (FA-01/FA-02: không nới, không skip).

| # | Lệnh | Kết quả |
|---|---|---|
| V0 | single test post-fix | `1 passed in 2.35s`, exit 0 (so 37.25s + error) |
| V1 | full flow_06, lần 1 | **`23 passed in 7.10s`, exit 0**, `grep -c "database is locked"` = 0 |
| V2 | full flow_06, lần 2 liên tiếp | **`23 passed in 6.65s`, exit 0**, 0 locked |
| V3 | coexist: flow_06 + flow_26 + flow_27 cùng session | **`25 passed in 8.19s`, exit 0**, 0 locked; `--collect-only` xác nhận flow_26/27 mỗi file có 1 test được thu thập (không vacuous) |
| V4 | A/B song song: **2 tiến trình pytest cùng chạy flow_06** | A: `23 passed in 7.51s` exit 0; B: `23 passed in 6.82s` exit 0; 0 locked — hết lock chéo |
| V5 | control kill-switch: `SCP_T03_DB_ISOLATION=off` single test | `1 passed, 1 warning, 1 error in 35.65s` + `sqlite3.OperationalError: database is locked` → chứng minh lỗi trở lại đúng khi tắt isolation: conftest là nguyên nhân của kết quả, contention live vẫn còn nguyên |
| V6 | `python tools/verify_scp_test_skill_contract.py` | exit 0, `"status": "PASS_WITHIN_SCOPE"` |
| V7 | `python tools/t00_meta_audit.py` | exit 0, `All integrity checks passed (0 new regressions)` (L4 warnings = diff của worker khác, không phải N2) |
| V8 | kiểm tra ô nhiễm production sau tất cả runs | `SELECT COUNT(*) FROM predictions` = 0, `m6-fixture-%` = 0 → không còn garbage; các run post-fix không ghi vào file production |

## 5. Fix đã cho phép sửa so với scope

- `tests/T03_capability/conftest.py` — CREATED (duy nhất, in scope "tests/T03 conftest nếu cần isolation").
- `tests/T03_capability/test_flow_06_prediction_scp_standard.py` — KHÔNG sửa (giữ nguyên).
- `scp/**/db_exec.py` — KHÔNG sửa: audit kết luận lifecycle đúng (commit/rollback dưới `_db_lock`); đổi nó sẽ không giải quyết cross-process contention và có nguy cơ đổi semantics.
- `tests/conftest.py` — KHÔNG sửa (blast radius thu về T03).
- Rollback path: xóa 1 file untracked → về hành vi cũ; hoặc chạy với `SCP_T03_DB_ISOLATION=off`.

## 6. PHÁT HIỆN MỚI (NEW FINDINGS — ngoài scope sửa của N2)

- **NF-N2-01 (HIGH): `scp/api_server.py:305-308` import-time side-effect.** `RealLearningEngine(scp_db_path="data/v13.db")` và `FastLearningEngine(scp_db_path="data/v13.db")` được instantiate lúc import `scp.api_server` với **đường dẫn relative hardcode** → mọi write qua `db_exec(..., db_path="data/v13.db")` của engine bỏ qua redirect của db_manager và vẫn nhắm vào file production nếu thread học không bị suppression (flow_06 tự patch `start_crawl_thread`/`start_fast_learning_thread` tại dòng 87-89 nên an toàn). Test T03 nào kích hoạt learning writes sẽ vẫn contention + ô nhiễm production. Cần product fix (env-resolved DB path cho engine), không phải test fix — out of scope N2.
- **NF-N2-02 (HIGH): unit tests T01/T02 và các suite khác chưa isolation.** Cùng pattern dùng chung `data/v13.db`. PID 81616 (`test_flow_01_boot_background` + `test_god_split_semantic_parity`) chạy song song đã được quan sát. T04 đã có conftest riêng (worker khác tạo giữa session — `tests/T04_kernel/conftest.py`, chưa đọc nội dung). Khuyến nghị nhân bản pattern của `tests/T03_capability/conftest.py` cho T01/T02/T04 (hoặc promote lên `tests/conftest.py` sau khi đo).
- **NF-N2-03 (MED): runtime có server production chạy ngay trên checkout đang audit** (`python -m scp 8000`). Full pytest regression cuối nếu chạy trong điều kiện này mà thiếu isolation sẽ reproduce đúng class lỗi này ở mọi suite ghi v13.db. Khuyến nghị: regression chạy server tắt HOẶC đảm bảo isolation đã phủ suite đó.
- **NF-N2-04 (MED): `checkpoint_wal()` (`scp/core/db_manager.py:160`) execute `PRAGMA wal_checkpoint(TRUNCATE)` trên persistent connection mà KHÔNG giữ `_db_lock`** — caller: `healing_v14.py:223`, `engine_parts/scpv14_process_mixin.py:171-172`. TRUNCATE checkpoint cần không còn reader snapshot; chạy song song với statement khác trên cùng connection là interleaving không được serialize (CPython sqlite3 serialized-mode chỉ bảo vệ từng statement). Không phải nguyên nhân của lỗi này (đã chứng minh cross-process), nhưng là điểm lifecycle cần reviewer group-A quyết (file mixin nằm trong CẤM của N2).
- **NF-N2-05 (LOW): stderr noise sau phiên** — OTLP `BatchSpanProcessor` daemon export vào file đã đóng: `ValueError: I/O operation on closed file` in đè cuối stdout pytest (không đổi exit code). Có thể làm người đọc nhầm "log fail" khi xem `tail`. Pre-existing, độc lập với SQLite.
- **NF-N2-06 (INFO): pre-fix, flow_06 ghi/xóa thẳng vào DB production** (docstring test tự nhận "shared predictions DB"). Post-fix T03 không còn mutate file production nữa — đây là improvement bảo mật dữ liệu thật, nhưng cũng nghĩa là mọi test T03 từng *dựa dẫm* vào dữ liệu do server ghi realtime vào v13.db (nếu có) sẽ không còn thấy nó — chưa phát hiện test nào như vậy trong 3 file đã verify; cần theo dõi ở full regression.

## 7 Verdict (theo scp-reality-verifier)

Claim: "teardown flow_06 không còn `database is locked`; exit 0; không flake; không lock chéo; sửa bằng isolation, không nới assertion/skip."
Bằng chứng: cấp B→C (integration thật: pytest + app lifespan + SQLite file-level, A/B kill-switch, 2 process song song) trong phạm vi các run V0–V8, commit `36bb181` + diff untracked của N2.
Verdict: **VERIFIED_WITHIN_SCOPE**. Không tuyên bố toàn bộ T03 hay full regression xanh — phạm vi đó thuộc run cuối; các file cùng pattern đã ghi nhận ở NF-N2-01/02.

## 8 Open questions

1. Ai là writer cụ thể gây mỗi stall trong run #1 (server vs PID 81616) — cần writer-side logging để quy kết từng vụ.
2. T01/T02 có cần/có thể chịu cùng isolation không — chờ kết quả full regression.
3. `FastLearningEngine` import-time instantiation có nên dời sang lazy trong product không (NF-N2-01) — cần quyết định của owner group-A/ api_server.
