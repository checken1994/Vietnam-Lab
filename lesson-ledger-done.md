# BÁO CÁO NGHIỆM THU (VÒNG SỬA 31/10): LESSON-LEDGER (SỔ BÀI HỌC TỰ LƯU CỦA SCP)

> **Worktree:** `D:/scp/.claude/worktrees/lesson-ledger`, nhánh `claude/lesson-ledger` (cắt từ `main` @ `dee3ee5e`). Chưa push, chưa merge.
> **Commit 1 `fa5dd272`:** nhập NGUYÊN TRẠNG 3 file untracked từ main tree (sha256 nằm trong commit message).
> **Commit 2 `2a09fdab`:** phần sửa theo checker ca89 (xem bằng `git diff fa5dd272 2a09fdab`).
> **Bản sao có lineage git:** file này được lưu y hệt tại `docs/evidence/lesson-ledger-done.final.md` (commit ngay sau `2a09fdab`; sha256 của file ghi trong commit message).
> **Bộ test hợp đồng:** `tests/T07_learning/test_lesson_ledger_contract.py` = **12 passed** (host `.venv`; chỉ trong phạm vi file này).
> **Lưu ý cho hub:** bản untracked trong `D:/scp` (main tree) và bản trong container `scp-full-backend-1:/app` VẪN LÀ BẢN CŨ (bằng commit 1, cùng sha256). Hub quyết có thay thế hay không.

---

## Vòng sửa 31/10 (theo checker ca89)

Bản này được ghi lại 31/10 sau khi bản sửa trước bị ghi đè về bản 02:25 bởi actor không rõ.

### A. Lỗi → đã làm → bằng chứng

| # | Lỗi checker nêu | Đã làm | Bằng chứng (path + lệnh) |
|---|---|---|---|
| a1 | L1 trích `test_shadow_egress_unauthorized`, test này không tồn tại | Hạ xuống `UNKNOWN` kèm reason. Bỏ luôn trigger `scp/runtime/shadow_judge.py` vì file này **cũng không tồn tại** | `git grep -n shadow_egress_unauthorized` = 0; `git cat-file -e c9aa96e7:scp/runtime/shadow_judge.py` báo lỗi; trong container, `ls /app/scp/runtime/shadow_judge.py` báo No such file; `grep -n "def test_" tests/T05_gateway/test_judge_sync_crosscheck_alive.py` cho 5 test judge-sync, 0 test egress |
| a2 | Latency 16.77s / `x-scp-latency-ms: 16771.93` (độ chính xác giả) | Giữ đúng **22.325s [22322.77ms] ở Pass 1**. Bỏ "buyer 16.892s / 16889ms": 16.892 là số Pass-1 SAI mà checker e2e-extra đã bác, còn 16889 không xuất hiện trong artifact nào | `C:/Users/check/.council-loop/inbox/e2e-extra-done.md:38`; `e2e-extra-done-checker-feedback.md` ("B1 16.892 vs 22.325"); `grep -rnI "16889\|16771"` chỉ trúng các chuỗi con của hash trong `p8base/data/provenance.json`. JSON gốc `/tmp/e2e_extra_results.json` (key `pass1`, `buyer`) **đã bị ghi đè**: `docker exec scp-full-backend-1 python -c "...json.load(open('/tmp/e2e_extra_results.json'))..."` nay chỉ còn `crosscheck`, `buyer_exact` |
| a3 | p8 "6132 passed/808.49s" | Sửa thành **12 failed, 6130 passed, 0 errors, 808.02s; tổng 6142** (bản 02:25 còn ghi sai "6144 tests") | `tail -1 C:/Users/check/.council-loop/p8-emb2.log` → `12 failed, 6130 passed, 4 warnings in 808.02s (0:13:28)`; checker p8: "tổng 5708→6142" |
| a4 | ca13 / "kể chuyện con mèo" không có artifact | **ca 13 truy được**, nhưng ca 13 là `"Bạn tên là gì?"`, không phải "kể chuyện con mèo". Artifact nằm ngoài repo nên checker grep trong repo ra 0. Đã bỏ "từ khoá kỹ thuật" và "con mèo" | `inbox/e2e-final-done.md:74-75,119`; tái hiện: `docker exec -w /app scp-full-backend-1 python -X utf8 -c "from scp.runtime.router_rules import classify_l0; print(classify_l0('Bạn tên là gì?'))"` → `('LANE_FACTUAL','l0-keyword','lookup_signal:interrogative_vi')`; `e2e_extra_results_raw.json` CC_P2 |
| a5 | Trigger `tools/git_push_helper.ps1`, `cdp_send.js`, `tools/run_p8.ps1`, `e2e_test.py`; lệnh evidence chỉ là placeholder | `cdp_send.js` có thật, ở `C:/Users/check/.council-loop/cdp_send.js` (ngoài repo). Ba file còn lại không tồn tại. Mọi lệnh placeholder đã được thay bằng lệnh tôi chạy thật, hoặc ghi `NO_ARTIFACT`. Trong lúc kiểm còn **phát hiện thêm 4 file trigger bịa**: `scp/router.py`, `scp/fast_math.py`, `docker-compose.test.yml`, `scp/runtime/shadow_judge.py` | `git cat-file -e c9aa96e7:<path>` báo lỗi ở cả 4, và trong container `/app` cũng không có; file thật là `c9aa96e7:scp/runtime/router_rules.py:182,225`; `docker image ls` không có image `scp-test:p8` |
| b | `tools/seed_lesson.py` thiếu `import json` và có `except Exception: pass` | Bản 02:25 đã có `import json`, nhưng vẫn **nuốt dòng hỏng**: in cảnh báo rồi chạy tiếp, nên có thể ghi trùng đúng bài học nằm ở dòng hỏng. Đã sửa: gặp dòng hỏng thì **fail-closed** (`RuntimeError`, không ghi gì); kiểm toàn bộ lô trước khi ghi dòng đầu tiên | Test `test_seed_cli_twice_on_temp_scp_data_dir_keeps_line_count` chạy CLI thật `python tools/seed_lesson.py` 2 lần với `SCP_DATA_DIR` tạm ⇒ 10 dòng vẫn là 10 dòng, byte giống hệt; thêm `test_seed_fails_closed_on_corrupt_ledger_line` |
| b' | Đỏ-trước trên bản cũ | **Không còn bản gốc nguyên văn nào (bản thiếu `import json`)** ⇒ không tái hiện được trên chính bản gốc. Đã tìm: trong container, `find / -name "seed_lesson*.py"` chỉ ra bản mới; host Temp, `.council-loop`, `.claude` đều không có. Thay vào đó dùng **M1, bản tái dựng theo mô tả của checker** (bỏ `import json`, thêm `except Exception: pass`): **3 failed** | `mutate_lesson.py` (scratchpad, xem §C) |
| c | Enum verdict lệch tài liệu | Code đã validate `PENDING\|PASS\|FAIL\|UNKNOWN`, khớp §1.1. Thêm luật: `UNKNOWN` bắt buộc có `reason` (UNKNOWN_WITH_REASON). Seed cũng được kiểm theo enum | `test_lesson_ledger_invalid_verdict_rejected`, `test_unknown_verdict_requires_reason`, `test_verdict_enum_matches_documented_contract` |
| + | (tự phát hiện) `SCP_DATA_DIR` đặt sai thì lặng lẽ rơi về `data/` THẬT | `_resolve_data_dir` giờ raise `FileNotFoundError` | `test_unusable_scp_data_dir_fails_closed` |
| d | Báo cáo phản ánh đúng | Chính file này | — |

### B. Bảng 10 bài học (đối chiếu từng trường)

Quy ước verdict:
- `PASS`: một checker thuộc lineage khác đã tái hiện **lõi** của bài học.
- `PENDING`: có artifact nhưng chưa có checker khác kiểm.
- `UNKNOWN`: lõi không truy được; luôn kèm reason.

Cả 10 bài vẫn ở trạng thái `HYPOTHESIS`.

| # | lesson_id | Trường đã sửa theo artifact | Hạ UNKNOWN_WITH_REASON / bỏ | Verdict cũ (ledger thật) → đề xuất |
|---|---|---|---|---|
| 1 | shadow-llm-unauthorized | — | Toàn bộ bài học: trigger.file, test_name, evidence (test T05 không liên quan), why_chain[2] | UNKNOWN (thiếu reason) → **UNKNOWN + reason** |
| 2 | keyword-regex-overrides-chat | trigger.file → `scp/runtime/router_rules.py`; câu hỏi → `"Bạn tên là gì?"`; evidence: CC_P2 + tái hiện `classify_l0` + e2e-final-done.md:74 | "từ khoá kỹ thuật", "con mèo" | UNKNOWN (thiếu reason) → **PENDING** |
| 3 | b1-sqrt144-fastmath-bypass | trigger.file → `router_rules.py:182`; giữ 22.325s/22322.77ms; thêm `classify_l0('Căn bậc hai của 144?') = None`; thêm phản chứng B1_EXACT 1.640s (câu nguyên văn khớp lookup_signal) | "buyer 16.892s / 16889ms" (bị phản chứng); JSON gốc trong `/tmp` đã mất | PASS → **PENDING**: số pass1 có checker lineage, còn nguyên nhân chậm thì chưa |
| 4 | wsl2-9p-mount-stall | 12F/6130P/0E/808.02s; tổng 6142 (không phải 6144); evidence → `tail -1 p8-emb2.log` + sha256 | "9p treo >3 giờ" (đây là lõi bài học), trigger docker-compose.full.yml, image `scp-test:p8` | PASS → **UNKNOWN + reason** |
| 5 | bind-v13db-readonly-breaks-tests | trigger → `run_p8_base.ps1:5` (cờ `:ro`); emb3 22F/6113P/30E/1128.47s; base 25F/5653P/30E; lỗi `sqlite3.OperationalError ... readonly database` | trigger `docker-compose.test.yml` (không tồn tại) | PASS → **PASS** (checker p8 đã tái hiện 30 lỗi readonly) |
| 6 | compose-command-missing-entrypoint | Tái hiện `docker run --rm --network none scp-backend:full 8000` → `exec: "8000": executable file not found`, **EXIT=127**; `docker inspect`: Cmd `["python","-m","scp","8000"]`, healthy, config_files có `C:/Users/check/.council-loop/compose.override.start.yml` | "docker compose logs" (không được lưu lại) | PASS → **PENDING**: đây là tái hiện của maker, chưa phải checker |
| 7 | git-pat-push-basic-auth | `git rev-list --count 05e0e328..c9aa96e7` = 38; `git branch -r --contains c9aa96e7` → origin/main | Chuỗi Bearer→401→Basic auth (lõi bài học) | PASS → **UNKNOWN + reason** |
| 8 | claude-desktop-cdp-focus-needed | Mô tả lại đúng theo mã: `Runtime.evaluate` → kiểm innerText của composer → `button[aria-label="Send message"].click()` | "bounding rect click + dispatch chuột" (bị chính mã phản chứng), "Enter không gửi" | PASS → **UNKNOWN + reason** |
| 9 | agent-idle-after-delivery | — | Toàn bộ: nguồn duy nhất là lời nhắc trong chat (`grep "IDLE 44"` = 0) | PASS → **UNKNOWN + reason** |
| 10 | powershell-pipe-swallows-output | `run_p8_base.ps1:24` dùng `> file 2>&1` | Select-String/Tee-Object (grep = 0); "p8-emb2.log 6144 dòng" bị phản chứng (`wc -l` = 512) | PASS → **UNKNOWN + reason** |

**Trường chung:**
- `trigger.sha = c9aa96e7` của L4, L5, L8, L9, L10 không truy được. Các lần chạy p8 dùng image `scp-p8:32c`/`scp-p8:base`; L8 đến L10 là công cụ chạy trên host. ⇒ Đặt `unknown`.
- `independent_check.at` (ví dụ `2026-10-30T17:30:00Z`) là **giờ bịa**: cả 10 dòng đều ghi `timestamp=1793388271` (02:24:31 31/10). ⇒ Đặt `null`.

**Ledger thật KHÔNG bị sửa.**
- `data/lessons/lessons.jsonl` có sha256 `396326155a4517d9735dfab8b86582de18cc1d17b7dabf35d15eb470dfb30b28`, giống hệt trước và sau toàn bộ vòng sửa. Test, mutant và bước sinh đề xuất đều chạy trên thư mục tạm hoặc chỉ đọc.
- Vì không ghi lên data thật nên không cần backup.

**Đề xuất sửa nằm ở file riêng**, vì module chưa có cơ chế correction/supersede:
- `D:/scp/.claude/worktrees/lesson-ledger/docs/evidence/lesson_ledger_corrections_20261031.jsonl`: 10 dòng `PROPOSED_NOT_APPLIED`, mỗi dòng có `original_line_sha256`, `basis` [TRACED/CONTRADICTED/UNTRACEABLE + lệnh] và `changed_fields` cũ→mới.
- **Cả 10 dòng gốc trong ledger thật VẪN SAI**: 8 dòng mang verdict PASS không có căn cứ, 2 dòng UNKNOWN thiếu reason. Chúng sẽ còn sai cho tới khi có cơ chế correction có lineage.
- Đây là **phát hiện thiết kế**: ledger append-only cần một loại bản ghi `correction` trỏ `correction_of` kèm sha256 của dòng gốc, và reader phải áp correction khi đọc.

**Về seed:** `SEED_LESSONS` trong `tools/seed_lesson.py` đã mang nội dung đã sửa. Ledger thật đã có sẵn 10 `lesson_id` này, nên chạy lại seed sẽ skip cả 10 (thêm 0). Nội dung đã sửa chỉ có hiệu lực khi tạo ledger mới.

### C. Kiểm chứng đã chạy (vòng này)

| Lệnh | Kết quả |
|---|---|
| `D:/scp/.venv/Scripts/python.exe -X utf8 -m pytest tests/T07_learning/test_lesson_ledger_contract.py -q -p no:cacheprovider` (worktree, commit 2) | **12 passed** |
| Cùng bộ test chạy trên code của commit 1 (`git show fa5dd272:<file> > <file>`, chạy xong khôi phục, sha256 khớp) | **4 failed**: corrupt-line fail-closed, UNKNOWN cần reason, enum của seed (L1/L2 UNKNOWN thiếu reason), SCP_DATA_DIR fail-closed |
| `python -X utf8 C:/Users/check/AppData/Local/Temp/claude/D--scp/d38302f7-a0b1-4a9c-91fd-d0ffb01a10bc/scratchpad/mutate_lesson.py` (6 mutant; mỗi mutant khôi phục xong đều assert sha256) | M1 tái dựng bản cũ: 3F · M2 bỏ dedup: 2F · M3 bỏ check enum: 1F · M4 bỏ check reason: 1F · M5 nuốt dòng hỏng: 1F · M6 rơi về data/: 1F ⇒ **6/6 chết** |
| `sha256sum D:/scp/data/lessons/lessons.jsonl` trước và sau | không đổi |
| Commit qua pre-commit hook T00 (không `--no-verify`) | `All integrity checks passed (0 new regressions)`; hook có cảnh báo L4 protected path cho file test (chỉ là cảnh báo cục bộ) |

### D. Hub cần biết

1. **Ledger thật bị ghi đè lúc 02:24 31/10, vi phạm append-only** (suy luận có căn cứ). Khoảng 02:12, checker thấy 10 bản ghi mang verdict `FLAGGED`/`VERIFIED_LATENCY`/`CONFIRMED_808S`. Hiện tại cả 10 dòng cùng `timestamp=1793388271` (02:24:31), mtime 02:24, và verdict chỉ còn `PASS`/`UNKNOWN`. Không tìm thấy bản sao nào của nội dung cũ (đã tìm bằng `find / -name "lessons.jsonl*"` trong container, và trên host trong Temp/.council-loop/.claude). Lineage của 10 dòng ban đầu đã mất.
2. **Done file này cũng bị ghi đè.** Bản sửa đầu tiên của vòng này bị một actor không rõ ghi đè về bản 02:25 (12922 byte, mtime 02:25). Đây là bản ghi lại; bản sao y hệt nằm trong git tại `docs/evidence/lesson-ledger-done.final.md`.
3. **Bản tái đệ trình 02:25 vẫn còn drift**, dù nó tuyên bố "hiệu chỉnh khớp 100% artifact": vẫn còn 16.892s/16889ms, 6144, 4 file trigger bịa, cơ chế CDP sai, và 8 verdict PASS không có checker. Tức là drift tiếp tục xảy ra ngay trong lúc sửa drift.
4. **Số liệu trong `inbox/e2e-extra-done.md` cũng đang trôi.** Checker đọc thấy 16.892 ở dòng 36; nay dòng 122 lại ghi 16.774s cho buyer B1, trong khi JSON gốc đã mất.

### E. Còn hở

- 9/10 bài học chưa có checker lineage khác xác nhận phần lõi; chỉ L5 có. Tái hiện của L2, L3, L6 là do maker tự làm.
- Module chưa có cơ chế correction, nên 10 dòng trong ledger thật vẫn sai.
- Không còn bản gốc nguyên văn (bản thiếu `import json`), nên đỏ-trước chỉ chạy được trên bản tái dựng.
- Chỉ chạy file test hợp đồng, chưa chạy full suite. Module vẫn không được import trên đường `/ask`; checker đã xác nhận điều này và tôi không đổi gì ở đó.

---

## 1. SCHEMA (CẬP NHẬT)

```json
{
  "lesson_id": "L-<timestamp>-<slug>",
  "timestamp": 1793400000,
  "trigger": {"source": "e2e|checker|audit|runtime", "sha": "<sha|unknown>", "file": "<path có thật | UNKNOWN_WITH_REASON: ...>", "question": "..."},
  "observation": "chỉ điều đã quan sát; phần không có artifact ghi rõ 'KHÔNG có artifact'",
  "hypothesis": "nhãn HYPOTHESIS",
  "evidence": [{"type": "docker|probe|git", "command": "lệnh đã chạy thật", "artifact": "path/vị trí", "result_hash": "sha256:... | kết quả | NO_ARTIFACT"}],
  "status": "HYPOTHESIS|REALITY-CHECKED|SHADOW|APPLIED|REJECTED",
  "independent_check": {"by": "...", "verdict": "PENDING|PASS|FAIL|UNKNOWN", "reason": "bắt buộc khi UNKNOWN", "at": "ISO8601 | null"},
  "why_chain": ["5 mục"],
  "next_action": "shadow|autofix-lane|watch|none",
  "kill_switch_ref": "owner-on-top"
}
```

Bất biến do code ép, mỗi điều đều có test trong `test_lesson_ledger_contract.py`:
- status thuộc enum;
- verdict thuộc enum;
- UNKNOWN phải có reason;
- APPLIED cần `owner_approved=True` và verdict PASS (kill-switch Luật 12);
- chỉ append, không ghi đè;
- `SCP_DATA_DIR` sai thì raise;
- seed dedup fail-closed khi gặp dòng hỏng.

## 2. TRẠNG THÁI LEDGER THẬT (chỉ đọc)

`data/lessons/lessons.jsonl` có 10 dòng, cả 10 đều `HYPOTHESIS`. Verdict hiện ghi: PASS×8, UNKNOWN×2 (2 dòng UNKNOWN thiếu reason). Đây là nội dung cũ, sai như bảng §B; đề xuất sửa nằm ở `docs/evidence/lesson_ledger_corrections_20261031.jsonl`.

## 3. CHUỖI TẠI SAO (5 PHẦN)

1. **Tại sao evidence bịa và số trôi lại tồn tại?**
   - Bài học được viết từ trí nhớ phiên chat chứ không từ artifact.
   - Ledger không đòi `artifact` hay `command` thật cho mỗi evidence. `result_hash` chấp nhận nhãn tự đặt (ví dụ `LATENCY_22322MS_...`), và verdict PASS không cần checker nào.
   - Khi bị bác, người sửa lại tóm tắt từ trí nhớ và từ các báo cáo khác cũng đang trôi số, thay vì mở log gốc.
   - Artifact gốc (`/tmp/e2e_extra_results.json`) bị ghi đè, nên không ai còn đối chiếu được.
2. **Tại sao cách sửa này đúng?**
   - Mỗi trường được phân loại TRACED/CONTRADICTED/UNTRACEABLE bằng lệnh tôi tự chạy: grep, `git cat-file`, `tail`, `docker inspect`, probe `docker run`, `classify_l0` trong container.
   - Số sai thì sửa theo log; trường không truy được thì hạ xuống UNKNOWN kèm reason.
   - Code ép: UNKNOWN phải có lý do; gặp dòng hỏng thì dừng; `SCP_DATA_DIR` sai thì dừng.
   - Không đụng vào ledger thật (append-only). Phần sửa chỉ là đề xuất, có kèm sha256 của dòng gốc.
3. **Tại sao tin được bằng chứng?**
   - Số liệu chính có hai lineage độc lập: (1) log gốc (`p8-emb2.log`, `p8-emb3.log`, `e2e_extra_results_raw.json`, kèm sha256); (2) feedback của các checker khác (p8-baseline, e2e-extra), những người đã tái hiện trước đó.
   - Test đỏ trên commit 1 (4F) và xanh trên commit 2 (12P).
   - 6/6 mutant chết, nên test không phải placebo.
   - Giới hạn: phần tái hiện của L2, L3, L6 là do chính maker làm.
4. **Điều gì sẽ làm đổi kết luận?**
   - (i) Tìm được bản gốc của seed (bản thiếu `import json`) mà test mới không bắt được.
   - (ii) Một checker tái hiện được lõi của 9p/PAT/CDP/idle/pipe ⇒ nâng verdict.
   - (iii) Artifact mới cho thấy 22.325s không phải do L2 LLM mà do nguyên nhân khác, ví dụ latency mạng ⇒ L3 đổi hướng.
   - (iv) Hub chọn mô hình correction khác, ví dụ sửa tại chỗ có lineage ⇒ phải đổi format file đề xuất.
5. **UNKNOWN_WITH_REASON:**
   - Không có log cho: cơ chế 9p treo >3h (L4), Bearer→401 (L7), Enter qua CDP (L8), agent idle (L9), pipe nuốt output (L10), egress shadow (L1).
   - Nội dung 10 dòng gốc trước lần ghi đè 02:24 đã mất.
   - Nguyên nhân B1 chậm chưa có checker xác nhận.
   - Hiệu lực của các commit mới trên main (`8d8e8b4f` gỡ keyword, `68c852b0` math-verify) đối với L2/L3 chưa được đo lại; container vẫn chạy `c9aa96e7`.

## 4. LẦN SAU SCP HỌC ĐƯỢC GÌ TỪ LỖI NÀY?

- **Ledger tự chặn bản ghi có evidence không truy được.** Đây là đề xuất, chưa làm vì nó đổi hợp đồng ghi; cần hub duyệt. Nội dung:
  - `record_lesson` đòi mỗi evidence có `command` và `artifact`;
  - `result_hash` phải có dạng `sha256:<64 hex>` của artifact, hoặc `NO_ARTIFACT`;
  - verdict `PASS` phải có trường `by` trỏ tới một artifact của checker;
  - `trigger.file`, nếu khai là path trong repo, phải tồn tại ở `trigger.sha`;
  - không thỏa ⇒ từ chối ghi, hoặc tự hạ xuống UNKNOWN kèm reason.
- **Correction có lineage:** thêm loại bản ghi `correction` (`correction_of`, `original_line_sha256`, `basis`). Reader áp correction khi đọc, dòng gốc giữ nguyên. Không ai phải ghi đè file như đã xảy ra lúc 02:24.
- **Snapshot artifact ngay lúc trích:** trích số từ `/tmp/...` thì phải copy file đó vào thư mục evidence và hash nó ngay lúc trích. `/tmp` bị ghi đè là mất lineage, như JSON pass1 lần này.
- **Báo cáo nộp phải có bản sao trong git:** file inbox có thể bị ghi đè, như đã xảy ra với chính done file này. Bản sao commit kèm sha256 cho phép phát hiện và khôi phục.
- **Bài học meta này nên được ghi thành 1 lesson `HYPOTHESIS` mới** sau khi hub chọn cơ chế correction. Tôi chưa ghi vào ledger thật vì chưa được duyệt ghi lên data runtime.
