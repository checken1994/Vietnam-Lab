# journal-chain — DONE (maker: Claude, lane KERNEL/SECURITY) — 2026-10-31

- **Nhánh:** `claude/journal-chain` — worktree `D:/scp/.claude/worktrees/journal-chain`
- **Commit code:** `dbf2c25c` — **parent `f1ea1bcd`** (main lúc commit). Bắt đầu từ `dee3ee5e`; trước commit đã
  fast-forward lên main vì T00 (trusted base = main) coi test mới của main là "bị xóa" (FA-02) — không đè file nào
  (giao 0 file giữa 7 commit main mới và 11 file của lane). Bằng chứng đã chạy lại trên base mới (mục 3, 5).
- **Commit evidence:** commit con của `dbf2c25c` chứa `docs/evidence/journal-chain-done.final.md` (bản giống hệt file
  này). SHA của nó + sha256 file này nằm trong handback (một file không thể tự chứa hash/SHA của chính nó).
- **KHÔNG làm:** push/merge, đụng `D:/scp` main tree, ghi `D:/scp/data`, restart container, đổi config/khóa production,
  đọc `.env`, in secret, bật kill switch toàn cục, sửa file lane heartbeat/prediction-p0/relevance, hiện thực supervisor của AG.

## 0. Kết luận ngắn

9 chuỗi "hỏng" **không phải bị phá**: chúng được ký bằng **khóa test CÔNG KHAI của `tests/conftest.py`** — tiến trình
pytest/test đã ghi vào `D:/scp/data/ask_task_kernel.sqlite3` (DB sống, chia cho container qua bind-mount). Bộ verify đúng
về toán; sai là **phản ứng**: kernel vẫn cho task trên chuỗi đó chạy tiếp, vòng Nghi ngờ hạ cảnh báo lặp xuống DEBUG, không
trạng thái bền, `/ready` vẫn "ready", không bài học, và **không kiểm DB mà /ask thật sự ghi** (`SCP_KERNEL_DB_PATH`).
Fix: HOLD theo task tại kernel (không kill toàn cục, không xóa bằng chứng), báo động bền theo nguyên nhân, `/ready degraded`,
đúng 1 bài học HYPOTHESIS. Vì khóa test là công khai, chuỗi đó **không bao giờ được "chấp nhận"** bằng cách khai báo khóa.

## 1. Nguyên nhân gốc — mạng nhân quả

Bằng chứng nền (đọc ro trên BẢN SAO trong scratch, không mở `D:/scp/data` để ghi):
`C:\Users\check\AppData\Local\Temp\claude\D--scp\d38302f7-a0b1-4a9c-91fd-d0ffb01a10bc\scratchpad\jc\snap\`
(sha256 db `0a216c84…`, shm `fd4c9fda…`, wal `e3b0c442…` = rỗng, + `doubt_ledger.jsonl`); DB /ask production
`…\jc\snap_tk\task_kernel.sqlite3` (sha256 `6266eea5…`, 190 task, 120 HUMAN_REVIEW). Script: `ledger_scan.py`,
`ledger_rate.py`, `db_scan.py`, `classify_chains.py`, `kverify.py`, `prod_view_states.py`, `repro_e.py`, `repro_ctrl.py`.

```
conftest.py setdefault khóa test CÔNG KHAI ─┐
conftest KHÔNG đặt SCP_DATA_DIR             ├─► (e) tiến trình test ghi chuỗi + cờ control vào <repo>/data/ask_task_kernel.sqlite3
runtime_data_dir = <repo>/data (theo __file__)┘
bind-mount ./data:/app/data (d) ───────────► container đọc CÙNG file đó (host + container cùng ghi)
container: /ask -> SCP_KERNEL_DB_PATH=/app/data/task_kernel.sqlite3 ; doubt_cron + boot recovery -> <data_dir>/ask_task_kernel.sqlite3
   => vòng Nghi ngờ kiểm DB "phụ" bị test làm bẩn; DB /ask thật (190 task) CHƯA TỪNG được kiểm chuỗi
verifier giữ khóa production: 9 chuỗi khóa test = "invalid" (toán ĐÚNG) -> DOUBT_DETECTED 2,288/4,225 vòng
   => (f) phản ứng sai: không phân loại, lặp -> DEBUG, không trạng thái bền, /ready ready, 0 bài học, kernel vẫn cho chạy
```

| Nhánh | Kết luận | Bằng chứng |
|---|---|---|
| (e) test ghi vào DB sống | **VERIFIED** | 9/22 chuỗi xác minh TRỌN dưới khóa conftest — hai lineage: `TaskKernel.verify_integrity()` với env conftest trên bản sao + bộ phân loại HMAC viết lại từ đặc tả (`classify_chains.py`, không gọi kernel). 8 CANCELLED 2026-10-29T20:42:46–20:45:18Z dưới conftest K_journal (định dạng R5, begin-failure KillSwitchActive); `…-68eb0ea0` COMPLETED 2026-10-26T23:06Z dưới conftest capability key (legacy) với 1 lời gọi LLM thật 41 s. **Tái hiện hôm nay:** broad run trong checkout mới (`jc-base`) tạo `data/ask_task_kernel.sqlite3` lúc 13:35:43 (+07) với cờ control ký bằng conftest K_journal: khóa conftest đọc `global_kill=0`, khóa khác đọc `global_kill=1` (`repro_ctrl.py`). |
| (d) SQLite trên bind-mount Windows | **VERIFIED là điều kiện cho phép, KHÔNG phải cơ chế hỏng** | `quick_check=ok`, số anchor khớp, seq/prev_hash nguyên vẹn; cùng 1 file do host lẫn container ghi. |
| (b) xoay khóa / dual-accept | **Không phải nguyên nhân** (VERIFIED) | Khóa ký 9 chuỗi là khóa test — container báo chúng invalid nên khóa container ≠ khóa conftest; compose chỉ truyền `SCP_CAPABILITY_SECRET`/`SCP_JOURNAL_SECRET`, không có PREVIOUS. Dual-accept chỉ giải thích vì sao chuỗi legacy production 10-26 vẫn hợp lệ ở view production. |
| (c) tranh chấp transaction (giả thuyết AG) | **BÁC BỎ cho 9 chuỗi này** (VERIFIED) | Cả 22 chuỗi cấu trúc nguyên vẹn; HMAC lệch 100% event của chuỗi (lớp KEY_DOMAIN) chứ không lẻ tẻ/đứt seq như tranh chấp sẽ để lại. |
| (a) tamper thật | **Không được ủng hộ** (HYPOTHESIS-level loại trừ) | Mọi event hợp lệ dưới một khóa đã biết, nhịp thời gian khớp burst test (>95% dòng ledger trong cụm <2 s). NHƯNG khóa công khai ⇒ không phân biệt được với giả mạo ⇒ cách ly là đúng, không bao giờ chấp nhận. |
| (f) verify bug / báo động giả | **Toán đúng; ngữ nghĩa SAI** (VERIFIED) | "invalid" gộp khóa-lạ với tamper (không phân loại); monitor kiểm sai DB; số đếm dao động giữa các dòng ledger (5/1/13/9/22/14/6) = các ngữ cảnh verifier giữ khóa khác nhau (host/test vs container). |
| Ánh xạ "9 chuỗi container báo hỏng" = "9 chuỗi khóa test" | **HỖ TRỢ MẠNH, chưa chứng minh trực tiếp** | Số đếm khớp; `sample_invalid` ledger (`022e…`, `022e…-d6e88539`, `1dffde…-4844a0fc`, đều CANCELLED) trùng id; phép bù 22−9=13 chuỗi hợp lệ ở view production. Lệnh `docker exec` phân loại bằng khóa production bị từ chối ([Production Reads]) — **không lách**; đề xuất hub chạy `prod_classify.py`. |
| Ai ghi 13 chuỗi khóa production trên host (10-26T22:00–22:43Z, 10-29T20:41–20:43Z, xen kẽ tiến trình test) | **UNKNOWN_WITH_REASON** | Không phải container (`SCP_KERNEL_DB_PATH` đặt từ 10-18); có thể uvicorn/probe host chạy từ `D:/scp` không đặt `SCP_DATA_DIR` — event không mang danh tính tiến trình/commit/kid. |

Từ commit/thời điểm nào: thời điểm VERIFIED (10-26T23:06Z; 10-29T20:42–20:45Z; báo động đầu tiên 10-27T09:21Z theo ledger).
Commit/test cụ thể đã ghi: **UNKNOWN_WITH_REASON** (journal không ghi ai ghi). 17 file test tham chiếu `ask_task_kernel`
(vd `tests/T03_capability/test_ask_kill_switch_gate.py`, `tests/T01_boot/test_flow_01_boot_background_scp_standard.py`) là ứng viên.

Phát hiện ngoại vi (FA-11 — báo, KHÔNG sửa vì ngoài phạm vi):
1. `scp/api_server_parts/lifespan.py:425-431` boot recovery + DoubtCron dùng `<data_dir>/ask_task_kernel.sqlite3`, /ask dùng `SCP_KERNEL_DB_PATH` (doubt của tôi nay kiểm CẢ HAI; boot recovery vẫn sai DB).
2. Cờ control của `ask_task_kernel.sqlite3` ký bằng conftest K_journal ⇒ container (khóa prod) đọc `global_kill=1` cho DB đó (mặc định an toàn) — tái hiện được.
3. `recover_on_boot` đã ghi lại `updated_at` của 5 task HR lúc 03:46Z qua `rebuild_projection` (thấy trên bản sao).
4. Cô lập test: conftest không đặt `SCP_DATA_DIR` (lane l0a). Probe còn cho thấy Hands kernel ghi `data/hands/task_kernel.sqlite3` theo đường của code (không theo `SCP_DATA_DIR`).
5. ErrorStore mặc định `data/error_store.jsonl` theo cwd ⇒ code của tôi chặn ghi bài học nếu kho không nằm trong chính data_dir (l0a sẽ chuyển sang learning_store_path + guard_write).
6. `.venv` sitecustomize chèn `D:\scp` vào `sys.path` TRƯỚC `PYTHONPATH` ⇒ mọi uvicorn/python chạy từ worktree âm thầm import code main tree (pytest không bị vì `pythonpath=.`). Tôi dính ở lần probe đầu (kết quả bị loại), sửa bằng launcher có assert đường import.
7. `test_pg_storage_chaos` dùng chung container `scp-pg-test` ⇒ chạy song song 2 cây va chạm.
8. QLT-06 (`test_qlt06_noqa_ceiling_in_scp`) đã đỏ sẵn trên main: 239 > 226; commit này giữ nguyên 239 (đã gỡ 3 `noqa` tôi lỡ thêm, thay bằng tuple ngoại lệ hẹp).
9. Main có sổ bài học song song `scp/learning/lesson_ledger.py` (a1019a4f) — brief bắt buộc ErrorStore; đề xuất hợp nhất sau.

## 2. Thiết kế phản ứng (đã hiện thực)

**Kernel — HOLD theo phạm vi task, bằng chứng là chính journal**
- `classify_chain_integrity` (definitions.py): theo CẤU TRÚC dữ liệu — lỗi cấu trúc ⇒ `STRUCTURAL_TAMPER`; một phần event lệch HMAC ⇒ `CONTENT_TAMPER`; mọi event lệch, cấu trúc nguyên ⇒ `KEY_DOMAIN_MISMATCH`. Không regex/mẫu câu (luật 9). `verify_journal` trả `integrity_class` + `hash_failed`; `verify_integrity` thêm `integrity_class` + `state` cho mỗi chuỗi invalid.
- `JournalIntegrityHold(KillSwitchActive)` mang `task_id`, `integrity_class`, `errors[:5]`. `_append_event` raise nó cho mọi **sự kiện tiến triển** (`to_state` ngoài `SAFE_UNDER_KILL`, hoặc `CHECKPOINT_WRITTEN`/`ACTION_DISPATCHED` trước side effect) khi chuỗi của task không chứng minh được dưới khóa của kernel ⇒ rollback cả transaction, journal không bị sửa/xóa.
- Cạnh an toàn (CANCELLED/HUMAN_REVIEW/FAILED…) vẫn đi được — SCP tự chặn mà không kẹt task. `claim_next` bỏ qua task bị HOLD (không chặn cả hàng đợi). `expire_leases` giữ task CHECKPOINTED bị HOLD (lý do `heartbeat_expired_journal_hold`, không requeue). `auto_resolve_human_review` bị chặn (đường thật của backlog HR).
- HOLD **tự gỡ** khi journal chứng minh được lại (vd khóa production đã nghỉ được khai báo qua `SCP_PREVIOUS_JOURNAL_SECRETS`) — không cần người duyệt; **không** đụng `global_kill`.
- Chi phí: `_journal_hold` = verify chuỗi của 1 task cho mỗi sự kiện tiến triển; đo trên bản sao: **1.288 ms/lần** cho chuỗi 10 event (n=200). Tuyến tính theo số event (xem mục 10.4).

**Vòng Nghi ngờ — báo động là TRẠNG THÁI BỀN**
- `_kernel_db_targets`: kiểm `SCP_KERNEL_DB_PATH` (DB /ask thật) + `<data_dir>/ask_task_kernel.sqlite3`, mỗi DB một nhãn; detail giữ khóa cũ (`quick_check`, `invalid_chains_count`, `sample_invalid`) + `by_class`, `held_count`, `targets[]` (có `held_tasks`).
- `scp/core/doubt_alarm.py` (mới): `data_dir/doubt_alarm_state.json` (ghi tmp + `os.replace`), khóa báo động = `(check, DB, lớp nguyên nhân)` ⇒ 2,288 vòng cùng lỗi = MỘT nguyên nhân: `status`, `first_seen`, `last_seen`, `occurrences`, `consecutive`, `opened_count`, `last_detail`, `lesson`.
- Mỗi vòng còn báo động mở = **WARNING** (`opened` có chi tiết, `persisting` có số lần + first_seen); bỏ `DOUBT_REPEATED`/DEBUG. Gỡ chỉ khi chính check đó chạy ra bằng chứng có cấu trúc (check lỗi ≠ đã khỏi; lỗi check là báo động `CHECK_ERROR` riêng).
- **Đúng 1 bài học HYPOTHESIS mỗi lần nguyên nhân mở** qua wire đã duyệt `FalsificationEngine()._get_error_store()` (mẫu math_lessons, không import thẳng `scp.brain`): `verdict=UNVERIFIED`, `epistemic_status=HYPOTHESIS`, `verified=False`, `promotion=never_automatic`, giả thuyết theo lớp + bằng chứng rút gọn. Chỉ ghi vào kho nằm trong chính data_dir đang bị nghi ngờ (`skipped:store_outside_data_dir` nếu không).
- `/ready` (`apply_readiness_checks` trong server_helpers; api_server.py đổi 2 dòng, giữ 799 LOC ≤ 800): `kernel_journal`, `doubt` (unknown/ok/degraded/stale), `doubt_active_alarms`, `doubt_alarm_since`; status `ready → degraded` khi còn báo động hoặc trạng thái quá cũ (> max(2×interval, interval+900 s)). **Mã HTTP giữ nguyên** (healthcheck container dùng `/health`).

## 3. Test đỏ-trước (24 test mới)

- `tests/T04_kernel/test_journal_chain_hold.py` (14) + `tests/T04_kernel/test_journal_chain_doubt_reaction.py` (10). Zero-mock phần đang kiểm (TaskKernel + SQLite thật, ErrorStore thật, `/ready` qua ASGI thật); chỉ `_check_fitness` (Golden Suite) thay bằng ok qua seam đặt tên của `run_doubt_cycle`. "Ghi bằng khóa khác" = kết nối sqlite3 thứ hai ký lại chuỗi bằng HMAC dưới khóa lạ.
- **Base `dee3ee5e`: 22 failed / 2 passed; base `f1ea1bcd` (sau fast-forward): 22 failed / 2 passed** (2 pass = test canh "không bật kill toàn cục", đúng là phải pass cả hai). **Fix: 24/24.**
- Lý do đỏ đúng: `DID NOT RAISE KillSwitchActive` (claim/resume/start/checkpoint/auto-resolve), `hash_failed`/`integrity_class` vắng, mức log `[{30},{10},{10}]` (WARNING rồi DEBUG), `0 bài học`, `KeyError: 'by_class'`, `1 == 3` (DB của SCP_KERNEL_DB_PATH không được kiểm), "state must be durable (none written)", `'ready' == 'degraded'`.
- Suite liên quan trên cây fix sau fast-forward (T04_kernel toàn bộ + T03 flow_12/flow_14 + T01 flow_01 + T02 hc39/ledger_retention/route_auth/docker_desktop + internal hc39/app_state + T07 lesson_ledger + T11): **1284 passed, 1 failed** = `test_pg_migration.py::test_migrate_sqlite_to_pg_end_to_end` (đỏ sẵn trên base: UnicodeDecodeError cp1252 trong subprocess). `tests/T00_integrity`: 289 passed, 1 failed = QLT-06 (239 > 226, giống hệt base).

## 4. Mutant (14/14 chết, sha256 khôi phục khớp)

Script: `…\scratchpad\jc\mutants.py` (đổi 1 chỗ, chạy 2 file test, khôi phục bytes gốc, so sha256). Kết quả: `mutants_a.txt`, `mutants_b.txt`.

| # | Mutant | Kết quả | Test giết |
|---|---|---|---|
| M01 | bỏ guard HOLD trong `_append_event` | KILLED (6F) | claim/checkpoint/held_tasks/hold_lifts… |
| M02 | `claim_next` không bỏ qua task HOLD | KILLED (1F) | test_claim_next_skips_held_task_and_serves_others |
| M03 | đảo CONTENT/KEY_DOMAIN | KILLED (12F) | phân loại + doubt |
| M04 | `is_progress_event` luôn False | KILLED (5F) | claim/resume/hold_lifts |
| M05 | bỏ giữ CHECKPOINTED khi lease hết hạn | KILLED (1F) | test_expired_lease_on_held_checkpointed_task_is_contained_not_requeued |
| M06 | không tăng `occurrences` | KILLED (2F) | durable counts, check_error |
| M07 | hạ báo động lặp về DEBUG | KILLED (1F) | test_repeated_alarm_stays_warning_with_durable_counts |
| M08 | ghi bài học mỗi vòng | KILLED (1F) | test_exactly_one_hypothesis_lesson_per_root_cause |
| M09 | readiness bỏ qua ACTIVE | KILLED (2F) | /ready degraded |
| M10 | bỏ đích SCP_KERNEL_DB_PATH | KILLED (1F) | test_doubt_verifies_configured_kernel_db_path |
| M11 | bỏ phát hiện stale | KILLED (1F) | test_stale_doubt_state_degrades_ready |
| M12 | gỡ báo động khi check không ra bằng chứng | KILLED (1F) | test_check_error_does_not_clear_an_open_alarm |
| M13 | bỏ chốt kho bài học thuộc data_dir | KILLED (1F) | test_no_lesson_written_outside_the_doubted_data_dir |
| M14 | /ready không bao giờ hạ status | KILLED (3F) | ready/stale/check_error |

Mọi dòng: `restore=OK` (sha256 sau = trước). `TOTAL=14 BAD=0`.

## 5. Broad run (foreground, 14 chunk × 40 file, base và fix chạy song song trong cùng lệnh)

- Script `…\scratchpad\jc\broad.py`, kết quả `…\scratchpad\jc\broad_out\` (`base_<i>.txt`, `fix_<i>.txt`, `.summary`, `.log`, `all_base.txt`, `all_fix.txt`). Cùng 532 file cho hai cây (2 file test mới loại khỏi cả hai để so cùng tập).
- **Base `dee3ee5e`: 6894 passed / 34 failed. Fix (dee3ee5e + diff này): 6893 passed / 35 failed.** Không chunk nào TIMEOUT.
- `comm -13 all_base all_fix` = **1**: `tests/T04_kernel/test_pg_storage_chaos.py::test_pg_chaos_backup_to_pg_dump_restore_round_trip` — chạy lại TUẦN TỰ: pass trên base, pass trên fix, cả file 4/4 pass trên fix ⇒ va chạm container dùng chung `scp-pg-test` khi chạy song song, không phải hồi quy.
- `comm -23` = **0**.
- Giới hạn: broad chạy trên base `dee3ee5e`; sau fast-forward lên `f1ea1bcd` (7 commit main không đụng `scp/` ngoài file mới `scp/learning/lesson_ledger.py`; có đổi `tests/conftest.py`, `tools/t00_meta_audit.py`, workflow) tôi chạy lại đỏ-trước + suite liên quan + T00 + hook T00 (0 new regressions), KHÔNG chạy lại toàn bộ 532 file.

## 6. Probe uvicorn thật (port 18811)

- Script `…\scratchpad\jc\probe_run.py` + `probe_server.py` + `probe_post.py` + `wt_path.py`. Bản sao DB thật (sha256 `0a216c84…`) vào `probe/data`; `SCP_ENV_FILE=probe.env` (khóa test conftest + 2 khóa NGẪU NHIÊN dùng-một-lần cho ConfigContract `SCP_JWT_SECRET`/`SCP_ADMIN_KEY`, không in) ⇒ `.env` không bao giờ được nạp; `SCP_DATA_DIR=probe/data`, cwd=probe, `SCP_KERNEL_DB_PATH` không đặt; server log: `scp imported from D:\scp\.claude\worktrees\journal-chain\scp\__init__.py`.
- Lần chạy 1 bị loại: import nhầm code main tree (phát hiện ngoại vi 6) + ConfigContract thiếu 2 khóa ⇒ server exit 3. Lần 2 (hợp lệ):
  - `/health` 200 sau 8.3 s; vòng Nghi ngờ đầu tiên chạy khi boot.
  - `/ready` **HTTP 200, `status: degraded`**, `kernel_journal: degraded`, `doubt: degraded`, `doubt_active_alarms: kernel_integrity:KEY_DOMAIN_MISMATCH@ask_task_kernel.sqlite3x13(held=5)#1`, `judge: ok`.
  - Log server: `WARNING | scp.core.doubt_cron | [DOUBT] DOUBT_DETECTED opened: kernel_integrity|ask_task_kernel.sqlite3|KEY_DOMAIN_MISMATCH {"chains": 13, "held_tasks": [...`.
  - `doubt_alarm_state.json`: ACTIVE, occurrences 1, chains 13, held_count 5, `lesson: recorded`. Ledger: `by_class {"KEY_DOMAIN_MISMATCH": 13}`, held = 5 task HUMAN_REVIEW (`ask-1dffde8c…`, 4× `ask-3ca21fb0…`).
  - `probe/data/error_store.jsonl`: **đúng 1** bài học `doubt_kernel_integrity_key_domain_mismatch`, `UNVERIFIED`, `HYPOTHESIS`, `never_automatic`, chains 13.
  - Dừng server: port 18811 trống lại. Post-check trên cùng bản sao (view khóa test): 22 task, `quick_check ok`, invalid 13 = `KEY_DOMAIN_MISMATCH`; `auto_resolve_human_review` trên task HR bị HOLD ⇒ **REFUSED `JournalIntegrityHold` class=KEY_DOMAIN_MISMATCH**, state vẫn HUMAN_REVIEW, **journal không đổi** (digest trước = sau); `claim_next` không trả task HOLD; **`global_kill` 0 trước và sau**. Ảnh chụp nguồn `snap/` không đổi sau probe.
- Lưu ý đọc đúng: probe ở **view khóa test** nên 13 chuỗi khóa production hiện là "khóa lạ". Ở **view production**, 9 chuỗi bị HOLD là 9 chuỗi khóa test — **cả 9 đều đã kết thúc** (1 COMPLETED, 8 CANCELLED; `prod_view_states.py`) ⇒ khi deploy, container sẽ HOLD **0 task** trên DB này, báo động KEY_DOMAIN_MISMATCH×9 ACTIVE và `/ready degraded` cho tới khi người cách ly DB.

## 7. Đề xuất cho HUB (tôi KHÔNG làm)

1. **Chứng minh trực tiếp view production (1 lệnh đọc, cần người/hub duyệt):** chạy `…\scratchpad\jc\prod_classify.py` trong container trên BẢN SAO DB (ro, không in khóa) ⇒ xác nhận 9 chuỗi invalid ở view prod đúng là 9 id khóa test và cùng lớp KEY_DOMAIN_MISMATCH. Nếu có chuỗi CONTENT/STRUCTURAL_TAMPER ⇒ xử lý như sự cố an ninh thật.
2. **9 chuỗi ô nhiễm:** cách ly `data/ask_task_kernel.sqlite3` (chuyển sang nơi giữ bằng chứng, chỉ đọc) — **KHÔNG reanchor, KHÔNG thêm khóa conftest vào `SCP_PREVIOUS_*`** (khóa công khai ⇒ ai cũng giả được); 9 task đó đã kết thúc nên không mất việc.
3. **Deploy fix** cần rebuild/restart container — quyết định của người. Sau deploy, doubt sẽ kiểm lần đầu DB /ask thật `task_kernel.sqlite3` (190 task, 120 HR) — kết quả đó là bằng chứng mới, chưa ai có.
4. **Lifespan:** boot recovery dùng `SCP_KERNEL_DB_PATH` (ngoài phạm vi lane này; lifespan.py:425-431).
5. **Cô lập test (l0a):** conftest đặt `SCP_DATA_DIR` = tmp; thêm chốt cấm tiến trình pytest mở `<repo>/data/*.sqlite3`; test tái hiện nhánh (e) có sẵn: chạy suite trong checkout sạch rồi kiểm `data/ask_task_kernel.sqlite3` không được tạo.
6. **Đóng cửa sổ dual-accept** chỉ khi: backlog 4+81 (+120 HR của task_kernel.sqlite3) đã kết thúc hoặc được người quyết định, rồi reanchor sạch dưới K_journal production; thêm test tách khóa (khóa test ≠ khóa production, chuỗi khóa test không bao giờ PASS ở view production).
7. **Backlog 4+81 HUMAN_REVIEW:** với fix này, task HR trên chuỗi KHÔNG chứng minh được không thể được `auto_resolve`; task trên chuỗi hợp lệ không đổi hành vi. Trước mọi đợt xử lý hàng loạt: chạy `verify_journal` từng task, chia nhóm theo `integrity_class`; nhóm hợp lệ để người/autonomy xử lý như thường, nhóm không chứng minh được ⇒ CANCELLED/FAILED có lý do (cạnh an toàn vẫn mở), không bao giờ "chấp nhận khóa".
8. **Danh tính người ghi trong journal:** thêm `kid` (vân tay khóa, không phải khóa) + vai trò tiến trình vào thân event ký — để lần sau verify nói thẳng "ký bởi khóa test đã biết" thay vì thử khóa.
9. **Hợp nhất sổ bài học:** ErrorStore (brief) vs `scp/learning/lesson_ledger.py` (main a1019a4f).

## 8. File (commit `dbf2c25c`, 11 file, +1125/−55)

- `scp/task_kernel_parts/definitions.py` — `JournalIntegrityHold`, `INTEGRITY_CLASSES`, `PRE_SIDE_EFFECT_EVENTS`, `classify_chain_integrity`, `is_progress_event`.
- `scp/task_kernel_parts/taskkernel.py` — guard trong `_append_event`, `_journal_hold` (757 LOC).
- `scp/task_kernel_parts/lease_manager.py` — phân loại trong `verify_journal_impl`, `integrity_class`+`state` trong `verify_integrity_impl`, bỏ qua HOLD trong `claim_next_impl` (790 LOC).
- `scp/task_kernel_parts/approval_helpers.py` — giữ CHECKPOINTED bị HOLD khi lease hết hạn.
- `scp/task_kernel.py` — export `JournalIntegrityHold`.
- `scp/core/doubt_cron.py` — đích DB, `_verify_kernel_db`, phản ứng (record_cycle, WARNING mỗi vòng), bỏ DEBUG-dedup.
- `scp/core/doubt_alarm.py` (mới) — trạng thái bền, bài học HYPOTHESIS, readiness.
- `scp/api_server_parts/server_helpers.py` — `apply_readiness_checks`; `scp/api_server.py` — đổi 2 dòng `/ready` (799 LOC).
- `tests/T04_kernel/test_journal_chain_hold.py`, `tests/T04_kernel/test_journal_chain_doubt_reaction.py` (mới).
- Không đụng: `doubt_cron.py` ngoài phần phản ứng/đích DB (l0a chưa sửa file này — không xung đột), heartbeat/monitor, ledger_stage/forecast/calibration, relevance, lifespan.py.

## 9. Dọn dẹp

- Đã dừng server probe (port 18811 trống). Xóa `…\scratchpad\jc\probe\` (chứa probe.env với khóa test + khóa ngẫu nhiên dùng-một-lần) và worktree tạm `D:/scp/.claude/worktrees/jc-base` (base so sánh). Xóa file do test sinh trong worktree lane `reports/visual_reports/daily/REPORT_DAILY_2026-10-31.md` (không commit).
- GIỮ cho CHECKER tái chạy: `…\scratchpad\jc\snap\`, `snap_tk\` (bản sao ro), các script, `broad_out\`, `mutants_*.txt` — xóa sau khi CHECKER xong (bản sao có nội dung task thật).
- `data/` trong worktree lane (gitignored) có file do test/probe sinh — không commit, để nguyên cùng worktree.

## 10. Chuỗi Tại Sao

Chuỗi Tại Sao:
1. GỐC: VERIFIED: báo động lặp 2,288 vòng không phải chuỗi bị phá mà là tiến trình test ghi vào DB sống bằng khóa công khai trong tests/conftest.py, trong khi kernel không có khái niệm "chuỗi không chứng minh được thì không được tiến triển" và vòng Nghi ngờ không biết phân loại, không nhớ, không báo /ready — bằng chứng: 9/22 chuỗi trên bản sao sha256 0a216c84 khớp trọn khóa conftest qua `classify_chains.py` và qua verify của kernel; tái hiện hôm nay bằng `repro_ctrl.py` (checkout mới, broad run tạo DB với cờ control ký khóa conftest). Nguyên nhân SONG SONG đã xét và LOẠI TRỪ: tamper thật (cấu trúc nguyên, khớp khóa đã biết — chỉ loại ở mức HYPOTHESIS), xoay khóa (khóa ký chưa từng là khóa production), tranh chấp transaction (seq/prev_hash nguyên, HMAC lệch toàn chuỗi), bind-mount (quick_check ok — chỉ là điều kiện cho phép), bug toán của verify (hai bộ verify độc lập cùng kết quả).
2. SỬA-ĐÚNG: VERIFIED: `tests/T04_kernel/test_journal_chain_hold.py` + `tests/T04_kernel/test_journal_chain_doubt_reaction.py` đỏ 22/24 trên cả dee3ee5e và f1ea1bcd, xanh 24/24 trên dbf2c25c; mutants.py 14/14 KILLED restore=OK; broad 532 file comm -13 chỉ 1 id va chạm pg (tuần tự pass); probe 18811 trên bản sao DB thật: /ready degraded, 1 bài học HYPOTHESIS, auto_resolve REFUSED, journal digest không đổi, global_kill 0.
3. TIN-BẰNG-CHỨNG: VERIFIED: hai lineage độc lập cho phân loại (verify kernel vs HMAC viết lại theo đặc tả, `classify_chains.py`), probe chạy đúng code worktree (đường import được assert trong `wt_path.py`, lần chạy import nhầm main tree đã bị loại). HYPOTHESIS: 9 chuỗi container báo hỏng chính là 9 chuỗi khóa test — chưa kiểm dưới khóa production vì lệnh đọc trong container bị từ chối; chỉ có số đếm, id mẫu trùng và phép bù.
4. ĐỔI-QUYẾT-ĐỊNH: `prod_classify.py` trong container cho thấy chuỗi không thuộc khóa test bị hỏng dưới khóa production, hoặc có lớp CONTENT_TAMPER/STRUCTURAL_TAMPER ⇒ chuyển sang xử lý sự cố an ninh (người quyết định kill/cách ly), không còn là ô nhiễm test; độ trễ HOLD trên chuỗi dài (hiện 1.288 ms cho 10 event, tuyến tính) vượt ngân sách ⇒ cần verify tăng dần có neo đã kiểm thay vì verify cả chuỗi mỗi sự kiện tiến triển.
5. UNKNOWN_WITH_REASON: chưa biết test/commit/tiến trình nào đã ghi 9 chuỗi khóa test và 13 chuỗi khóa production trên host, vì event journal không mang danh tính người ghi (kid, vai trò, commit) — chỉ còn miền khóa và thời điểm; chưa biết DB /ask thật task_kernel.sqlite3 (190 task) có chuỗi hỏng hay không vì nó chưa từng được kiểm và tôi không được đọc trong container.

## 11. Lần sau SCP học được gì từ lỗi này?

1. **"Không chứng minh được" ≠ "bị phá", nhưng cả hai đều phải DỪNG tiến triển.** Phản ứng đúng là HOLD theo phạm vi task + giữ nguyên bằng chứng, tự gỡ khi chính bằng chứng chứng minh lại — không phải log rồi chạy tiếp, cũng không phải kill toàn hệ thống.
2. **Báo động lặp là MỘT nguyên nhân có tuổi, không phải tiếng ồn để giấu.** Dedup bằng cách hạ log làm nghi ngờ biến mất đúng lúc nó kéo dài nhất; dedup đúng là gộp theo nguyên nhân gốc và ĐẾM (first/last seen, occurrences), rồi báo lên `/ready`.
3. **Monitor phải kiểm đúng thứ runtime dùng.** Doubt kiểm `ask_task_kernel.sqlite3` trong khi /ask ghi `task_kernel.sqlite3`: 2,288 lần báo về DB phụ, 0 lần kiểm DB thật. Mỗi kiểm tra phải lấy đích từ cấu hình runtime thật, không từ tên mặc định.
4. **Khóa test công khai là một "miền khóa" — dữ liệu ký bằng nó không bao giờ được tin ở môi trường thật**, và tiến trình test không bao giờ được chạm data sống (test cô lập bằng `SCP_DATA_DIR` tmp là điều kiện an ninh, không chỉ là vệ sinh).
5. **Bằng chứng nên tự khai ai tạo ra nó** (kid + vai trò ghi trong event ký) — thiếu nó, ta mất nhiều giờ thử khóa và vẫn còn UNKNOWN về người ghi.
6. **Probe phải chứng minh nó đang chạy đúng code** (assert đường import) — môi trường có thể âm thầm thay code đang kiểm (sitecustomize chèn main tree); một PASS trên code sai là placebo.
7. Bài học HYPOTHESIS đã được hệ thống tự ghi qua ErrorStore khi báo động mở (`doubt_kernel_integrity_key_domain_mismatch`, `promotion=never_automatic`) — người/CHECKER nâng thành REALITY-CHECKED sau khi mục 7.1 chạy.
