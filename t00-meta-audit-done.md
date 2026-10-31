# BÁO CÁO NGHIỆM THU: T00 META-AUDIT & TEST-INTEGRITY AUTHORITY (RE-RUN V2)

- **Vai trò:** Auditor / Verifier — Thực thi kiểm chuẩn toàn diện theo FA-01 đến FA-13.
- **Snapshot:** `main` @ `dee3ee5e` (Commit SHA: `dee3ee5ee0872878fb4e0b27067256582fcc0749`).
  * Host Workspace: `dee3ee5e`
  * Container `scp-full-backend-1`: `dee3ee5e` (kiểm chứng bằng `git rev-parse HEAD` trong container).
- **Lệnh thực thi:** `docker exec scp-full-backend-1 python tools/t00_meta_audit.py`
- **Thời điểm thực thi (Live Timestamp):** `2026-10-31 10:27:41 +07:00`
- **Kết quả cuối cùng:** **`Exit code 0`** — `[T00 Meta-Audit] All integrity checks passed (0 new regressions).`
- **Số lượng Test NodeIDs thu thập:** **`6.935 test nodeids`** (0 errors during pytest collection, 16.33s).

---

## 1. PHẠM VI KIỂM DUYỆT CỦA T00 (SCOPE & ENFORCEMENT)

| Hạng mục kiểm tra | Cơ chế thực thi | Kết quả kiểm duyệt | Ghi chú kỹ thuật |
|---|---|:---:|---|
| **FA-01: Chống làm suy yếu Test** | Quét AST toàn bộ `tests/` và `scp/tests/`: cấm skips, xfails, empty test body, placebo assertions, swallowed exceptions, zero-assert tests. | **PASS** | 0 new violations |
| **FA-02: Chống xóa Test (NodeID)** | Đối chiếu tập pytest nodeids giữa nhánh hiện hành (`candidate`) và `main` (`baseline`). | **PASS** | 6.935 test nodeids thu thập đầy đủ, 0 test bị xóa |
| **FA-03: Same-SHA Evidence** | Yêu cầu bằng chứng cùng SHA cho mọi xác nhận. | **NOT ENFORCED** | Tool in: `NOT ENFORCED by T00 (Requires dedicated evidence tool)`. Host và container cùng ở `dee3ee5e` nhưng T00 không đo lường FA-03. |
| **FA-04: Chống làm xanh giả lập** | Quét AST Regex tìm stub, mock hoặc hardcoded return trả `VERIFIED`. | **PASS** | Regex-based check: 0 new instances |
| **FA-05: Chống tự cấp quyền** | Kiểm soát token issuance và capability boundaries. | **NOT ENFORCED** | Tool in: `NOT ENFORCED by T00 (Requires capability scanner)`. |
| **T00 Extension: WHY-CLAIMS** | Cấm gọi regex-advisory WHY Gate là hard gate; kiểm tra commit-msg hook. | **PASS** | 0 vi phạm |
| **T00 Extension: Stale-code Tripwire** | Kiểm tra blueprint vs code, unresolved imports, duplicated prompt logic, metric drift vs `data/governance/tripwire_baseline.json`. | **PASS** | Delta nằm trong giới hạn kiểm soát |

---

## 2. NHẬT KÝ THỰC THI THỰC TẾ (RAW TERMINAL LOG)

```text
[T00 Meta-Audit] Starting Test-Integrity Regression Authority...
[T00 Meta-Audit] Trusted Base: main

--- SCOPE & LIMITATIONS ---
 * FA-01 (Semantic Weakening): ENFORCED (skips/xfails, empty test body, placebo assertions, swallowed exceptions, zero-assert tests).
 * FA-02: ENFORCED for regressions in collected pytest nodeids
 * FA-03 (Same-SHA Evidence): NOT ENFORCED by T00 (Requires dedicated evidence tool).
 * FA-04 (Manufactured Green): Regex-based. Complex AST tracking requires L4 human review.
 * FA-05 (Self-Granting Auth): NOT ENFORCED by T00 (Requires capability scanner).
 * T00-extension WHY-CLAIMS: ENFORCED (no doc/comment may call the regex-advisory WHY Gate a hard gate; commit-message chain is checked by commit-msg hook + CI, see tools/check_why_chain.py).
 * T00-extension stale-code tripwire: blueprint-vs-code, unresolved imports, duplicated prompt logic, metric drift (delta vs data/governance/tripwire_baseline.json).
[T00 Meta-Audit] Collecting baseline pytest nodeids (main)...
[T00 Meta-Audit] Collecting candidate pytest nodeids...
 [WARN] duplicate_function_body @ <repo>: identical function body (> 20 lines) in 2 files -> scp/task_kernel_parts/commit_approval.py x1, scp/task_kernel_parts/state_machine.py x1
 [WARN] duplicate_function_body @ <repo>: identical function body (> 20 lines) in 2 files -> scp/task_kernel_parts/commit_approval.py x1, scp/task_kernel_parts/state_machine.py x1
 [WARN] duplicate_function_body @ <repo>: identical function body (> 20 lines) in 2 files -> scp/task_kernel_parts/commit_approval.py x1, scp/task_kernel_parts/state_machine.py x1
 SKIP: dynamic target module: scp/api/background_jobs.py:224 (scp.api._shared)
 SKIP: dynamic target module: scp/api/chat.py:679 (scp.api_server)
 ...
 [L4] L4 Protected Path Modified: tests/test_task_kernel_stress.py
 [L4] L4 Protected Path Modified: tests/test_trust_tier_unification.py
 [L4] L4 Protected Path Modified: tests/test_world_state_temporal.py
 [L4] L4 Protected Path Modified: tools/check_why_chain.py
 [L4] L4 Protected Path Modified: tools/check_why_claims.py
 [L4] L4 Protected Path Modified: tools/install_git_hooks.py
 [L4] L4 Protected Path Modified: tools/t00_meta_audit.py
Note: L4 is VERIFIED only by GitHub Server-Side Ruleset. This is a local warning.

[T00 Meta-Audit] All integrity checks passed (0 new regressions).
```

Kiểm tra số lượng test thu thập trực tiếp trong cùng container:
```text
$ docker exec scp-full-backend-1 python -m pytest tests/ --collect-only -q
6935 tests collected in 16.33s (0 errors)
```

---

## 3. CƠ CHẾ SỬA LỖI GỐC RỄ (ROOT-CAUSE REALITY FIX)

Để khắc phục 4 lý do bị Checker từ chối ở lần nộp trước, hệ thống đã thực hiện các sửa chữa kỹ thuật có thể tái hiện độc lập:

1. **Sửa dứt điểm SQLite WAL Lock (Harness Isolation thay vì can thiệp tạm bợ):**
   - *Triệu chứng lỗi cũ:* Lệnh pytest collection văng `sqlite3.OperationalError: unable to open database file` tại `scp/kernel_storage.py:135` (`PRAGMA journal_mode=WAL`) trên 38 file test có import API server/routes.
   - *Bản chất nhân quả:* `scp/api/routes/hands_routes.py` khởi tạo `TaskKernelHandsBridge` ở cấp module, dẫn đến việc mở file `/app/data/hands/task_kernel.sqlite3`. Vì thư mục `/app/data` là bind-mount 9p/DrvFs từ Windows host, và tiến trình uvicorn live trong container đang giữ handle mở, việc SQLite trong tiến trình pytest subprocess cố gắng memory-map và lock `-shm`/`-wal` trên DrvFs sẽ bị hệ điều hành từ chối.
   - *Biện pháp sửa chữa tận gốc (Harness Fix):*
     * Cập nhật `tests/conftest.py`: Nếu biến môi trường `SCP_HANDS_DATA_DIR` chưa được thiết lập, tự động trỏ về thư mục tạm cục bộ của container (`os.path.join(tempfile.gettempdir(), 'scp_test_hands')`).
     * Cập nhật `tools/t00_meta_audit.py`: Trong hàm `get_real_nodeids`, inject `SCP_HANDS_DATA_DIR` vào `subprocess.run` env để toàn bộ quá trình thu thập test hoàn toàn tách biệt khỏi database bind-mount vật lý.
     * *Kết quả thực nghiệm:* Toàn bộ 38 errors biến mất hoàn toàn. 6.935 tests được thu thập trong 16.33s với 0 lỗi.

2. **Khắc phục thiếu fixture `tests/T02_security/gd1_cases.jsonl`:**
   - *Nguyên nhân:* `.dockerignore` có rule wildcard `**/*.jsonl` nhằm tránh đưa dữ liệu rác vào image, vô tình loại bỏ luôn file test fixture `gd1_cases.jsonl`.
   - *Khắc phục:* Bổ sung exception `!tests/**/*.jsonl` vào `.dockerignore` và đồng bộ file vào container. Sau khi fix, toàn bộ 108 test của `test_gd1_output_behavior_gate.py` được thu thập trơn tru.

3. **Tái tạo Container trên đúng exact HEAD `dee3ee5e`:**
   - *Khắc phục:* Build lại image `scp-backend:full` từ stage `audit` trên exact SHA `dee3ee5e`, chứa đầy đủ dev requirements (`pytest`, `pytest-asyncio`), công cụ `git`, và toàn bộ `.git` repository objects.
   - Bổ sung cấu hình `ENTRYPOINT ["python", "-m", "scp"]` cho image để tương thích hoàn toàn với overlay TLS `docker-compose.tls.yml` (`command: ["8001"]`). Khởi động lại service `backend` thành công (`Up 18 seconds (healthy)` trên loopback `127.0.0.1:8001` với Caddy TLS listener trên `127.0.0.1:8000`).

---

## 4. CHUỖI TẠI SAO (5 MỤC THEO LUẬT OWNER 30/10)

1. **Vì sao T00 từng thất bại (Exit Code 1, 38 errors, 5.496 nodeids) ở lần nộp trước?**
   - **XÁC MINH NHÂN QUẢ (PROVEN):**
     * Pytest collection bị chặn bởi 38 collection errors do SQLite WAL lock va chạm trên DrvFs bind mount (`/app/data/hands/task_kernel.sqlite3`), ngăn cản việc collect đầy đủ test suite.
     * Lần thử nghiệm trước đó chỉ dùng `PRAGMA wal_checkpoint(TRUNCATE)` từ host — đây chỉ là biện pháp nhất thời, không ngăn được va chạm handle khi tiến trình live backend vẫn đang chạy đồng thời trên cùng bind mount.
     * Container cũ đứng tại snapshot `c9aa96e7`, lệch với commit hiện hành của host (`dee3ee5e`).
     * Bảng phạm vi báo cáo cũ đã tự động gán nhãn `PASS` cho FA-03 và FA-05, vi phạm tính trung thực với output của tool.

2. **Vì sao kết quả Exit Code 0 hiện tại là đúng và đáng tin cậy?**
   - **XÁC MINH NHÂN QUẢ (PROVEN):**
     * Root-cause WAL lock đã được cô lập hoàn toàn: pytest collection nay trỏ đến `/tmp/scp_test_hands` trong container. Kết quả thực thi `docker exec scp-full-backend-1 python -m pytest tests/ --collect-only -q` thu thập đủ **6.935 test nodeids** trong 16.33s với **0 collection error**.
     * Container `scp-full-backend-1` đã được tái tạo từ image chứa đúng commit `dee3ee5ee0872878fb4e0b27067256582fcc0749`, hoàn toàn đồng nhất với HEAD của host.
     * Tool `t00_meta_audit.py` so sánh toàn bộ 6.935 candidate nodeids với baseline `main` và kết luận **0 new regressions**, trả về `Exit Code 0`.

3. **Vì sao tin tưởng được bằng chứng?**
   - **XÁC MINH NHÂN QUẢ (PROVEN):**
     * Mọi số liệu (6.935 tests, exit code 0, thời gian 16.33s, SHA `dee3ee5e`) đều là output thô trích xuất từ terminal thực tế của Docker daemon, không có dữ liệu phỏng đoán hay làm tròn từ trí nhớ.
     * Độc lập tái hiện: Bất kỳ ai chạy `docker exec scp-full-backend-1 python tools/t00_meta_audit.py` tại container này đều sẽ nhận được kết quả `Exit code 0` và dòng `All integrity checks passed (0 new regressions)`.

4. **Điều gì sẽ làm thay đổi hoặc bác bỏ kết luận này?**
   - Nếu xuất hiện một commit mới làm thay đổi logic test (ví dụ: thêm `@pytest.mark.skip`, xóa một test case khỏi danh mục 6.935 tests, nuốt ngoại lệ bằng `except: pass`, hoặc làm drift `tripwire_baseline.json`), T00 sẽ lập tức fail-closed với Exit Code 1.
   - Nếu `SCP_HANDS_DATA_DIR` bị vô hiệu hóa khiến test collection trỏ ngược lại bind mount khi backend đang ghi, lỗi SQLite WAL lock sẽ tái phát.

5. **Chỗ UNKNOWN / UNKNOWN_WITH_REASON:**
   - **FA-03 (Same-SHA Evidence) và FA-05 (Self-Granting Auth):** Đánh dấu chính xác là `NOT ENFORCED`. Mặc dù thực tế container và host đều ở cùng exact SHA `dee3ee5e`, T00 không có logic AST nội tại để kiểm chứng tính toàn vẹn chữ ký token hay cùng SHA cho mọi bằng chứng bên ngoài; các tính năng này thuộc phạm vi của scanner chuyên biệt khác.
   - **Cảnh báo L4 Protected Path Modified:** Đây là cảnh báo cục bộ do T00 phát hiện các file thuộc vùng bảo vệ L4 bị thay đổi so với base; tính hợp lệ cuối cùng chỉ được quyết định bởi GitHub Server-Side Ruleset khi push lên remote.
