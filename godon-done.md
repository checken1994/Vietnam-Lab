# BÁO CÁO CỔNG 1: GỠ DỌN & VỆ SINH TÀI NGUYÊN (GO DON CAMPAIGN)
## Khảo sát Thực tế, Phân loại Rác Ứng viên & Đề xuất Xử lý (Read-Only Audit — Zero Untracked Side Effect)
### Căn cứ Chỉ thị: Chỉ thị trực tiếp của User/Orchestrator trong phiên (TASK MỚI CỔNG 1 GỠ DỌN) + `PROCESS-V3.md:57` (.guard-seen.txt tracking) + `scripts/ops/generate_visual_reports_schedule.ps1`
### Trạng thái: AUDIT COMPLETED — PROPOSED DISPOSITION MATRIX (ĐÃ CẬP NHẬT CHUỖI TẠI SAO THEO FEEDBACK CHECKER) — TUÂN THỦ KỶ LUẬT KHÔNG TỰ Ý XÓA FILE GIT-TRACKED
### Tác giả: Antigravity (Subagent Lead Orchestrator & Skeptical Auditor)

---

## 1. TỔNG QUAN KHẢO SÁT VẬT LÝ (EXECUTIVE INVENTORY AUDIT)

Tuân thủ nghiêm ngặt chỉ thị của Owner và Hub ("kiểm mọi file rác ứng viên rồi đề nghị xóa/keep, KHÔNG tự xóa git-tracked"), hệ thống đã thực hiện quét thực nghiệm đối chiếu 3 nhóm ứng viên rác trên môi trường đĩa thực tế:

| STT | Ứng viên / Đường dẫn | Loại tài nguyên | Trạng thái Git | Kích thước / Nội dung | Nguồn gốc phát sinh | Đề xuất Xử lý (Proposal) | Lý do chi tiết |
|---|---|---|---|---|---|---|---|
| **1** | `d:\scp\scp-audit-r2-empty-env-kHXw6n` | Thư mục rỗng | **Untracked** (0 files trong git) | 0 bytes / 0 files | Sinh bởi Node.js nhúng tại `tests/test_audit_r2_dashboard_proxy_contract.py:260` thiếu cleanup (10/29 03:45) | **XÓA (DELETE)** | Thư mục tạm vô chủ của bài test, hoàn toàn rỗng, an toàn 100% để dọn dẹp. |
| **2** | `d:\scp\scp-audit-r2-empty-env-pzTQpB` | Thư mục rỗng | **Untracked** (0 files trong git) | 0 bytes / 0 files | Sinh bởi Node.js nhúng tại `tests/test_audit_r2_dashboard_proxy_contract.py:260` thiếu cleanup (10/29 03:32) | **XÓA (DELETE)** | Thư mục tạm vô chủ của bài test, hoàn toàn rỗng, an toàn 100% để dọn dẹp. |
| **3** | `${SEEN_FILE}` (tên biến literal) | File rác shell | **ABSENT / KHÔNG TỒN TẠI** | 0 bytes (không tìm thấy trên đĩa) | Nêu trong chỉ thị; đĩa không có file literal này, git log không có; guard hiện dùng `.guard-seen.txt` (~4.8–5.3 KB, tăng dần mỗi khi guard append tên file mới được verify) | **KHÔNG CẦN XÓA (ĐÃ SẠCH)** | Đã quét toàn bộ `d:\scp` và `C:\Users\check\.council-loop\`: file literal `${SEEN_FILE}` hiện không tồn tại. Nếu phát sinh lại do script cũ: **XÓA TỨC THÌ**. |
| **4** | `d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-28.md` | File Markdown báo cáo | **Untracked** (`??` trong `git status`) | 5,169 bytes | Sinh bởi visual reporter schedule ngày 2026-10-28 | **XÓA (DELETE)** | Báo cáo trực quan ngày cũ (stale), không nằm trong git tracking, đã có báo cáo ngày 30/10 mới hơn thay thế. |
| **5** | `d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-29.md` | File Markdown báo cáo | **Untracked** (`??` trong `git status`) | 4,194 bytes | Sinh bởi visual reporter schedule ngày 2026-10-29 | **XÓA (DELETE)** | Báo cáo trực quan ngày cũ (stale), không nằm trong git tracking, đã có báo cáo ngày 30/10 mới hơn thay thế. |
| **6** | `d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-30.md` | File Markdown báo cáo | **Untracked** (`??` trong `git status`) | 3,828 bytes | Sinh bởi visual reporter schedule ngày 2026-10-30 | **GIỮ (KEEP)** | Báo cáo trực quan ngày gần nhất (Active Reference) phục vụ audit hiện hành, đề nghị giữ lại hoặc commit nếu cần snapshot. |
| **7** | `d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-25.md` | File Markdown báo cáo | **GIT-TRACKED** (Trong `git ls-files`) | 3,171 bytes | Lịch sử commit gốc của dự án | **GIỮ NGUYÊN (KEEP)** | **BẮT BUỘC GIỮ:** File nằm trong Git index, tuyệt đối không tự ý xóa để bảo toàn lịch sử repo. |
| **8** | `d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-27.md` | File Markdown báo cáo | **GIT-TRACKED** (Trong `git ls-files`) | 3,125 bytes | Lịch sử commit gốc của dự án | **GIỮ NGUYÊN (KEEP)** | **BẮT BUỘC GIỮ:** File nằm trong Git index, tuyệt đối không tự ý xóa để bảo toàn lịch sử repo. |

---

## 2. LỆNH DỌN DẸP KHUYẾN NGHỊ (READY-TO-EXECUTE CLEANUP SCRIPT)

Khi được Owner / Hub phê duyệt, lệnh PowerShell dọn dẹp các mục Untracked (không chạm vào bất kỳ file git-tracked nào):

```powershell
# 1. Dọn 2 thư mục audit rỗng tại workspace root:
Remove-Item -Path "d:\scp\scp-audit-r2-empty-env-kHXw6n" -Recurse -Force
Remove-Item -Path "d:\scp\scp-audit-r2-empty-env-pzTQpB" -Recurse -Force

# 2. Dọn 2 file stale visual reports không tracked:
Remove-Item -Path "d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-28.md" -Force
Remove-Item -Path "d:\scp\reports\visual_reports\daily\REPORT_DAILY_2026-10-29.md" -Force
```

---

## 3. CHUỖI TẠI SAO & GHI CHÚ NHẬN THỨC LUẬN (EPISTEMIC CHAIN & OWNER MANDATE)

> [!IMPORTANT] **GHI CHÚ EPISTEMIC CỦA OWNER (30/10/2026):**
> 1. Chuỗi Tại Sao là ĐÈN SOI, không phải TRỌNG TÀI — mắt xích chỉ là GIẢ THUYẾT cho tới khi Reality kiểm chứng độc lập.
> 2. Độ sâu chuỗi KHÔNG đồng nghĩa với độ tin cậy (sai số nhân dồn $0.9^5 \approx 59\%$).
> 3. Nguyên nhân là MẠNG (DAG / Causal Graph), không phải chuỗi tuyến tính đơn độc — xét nhánh song song.
> 4. 'Không biết' (`UNKNOWN_WITH_REASON`) là đầu ra DANH DỰ.
> 5. CHECKER chấm theo BẰNG CHỨNG THẬT từng mắt xích, không chấm theo độ mượt.

### 3.1. (Thành phần a) Tại sao rác tồn tại gốc (Root Cause)
- **Nhóm 1 (`scp-audit-r2-empty-env-*`):**
  - **Bằng chứng xác thực tại mã nguồn:** File `tests/test_audit_r2_dashboard_proxy_contract.py:260` (test `test_backend_jwt_auth_fails_closed_without_any_exchange_key`) nhúng đoạn mã Node.js:
    ```javascript
    // Isolate from the repository .env: point SCP_ROOT at an empty directory so
    // loadServiceAuthValues() finds no operator tokens at all.
    const fs = await import("node:fs");
    process.env.SCP_ROOT = await fs.promises.mkdtemp("scp-audit-r2-empty-env-");
    ```
  - **Cơ chế gây rác:** Vì prefix `"scp-audit-r2-empty-env-"` là đường dẫn tương đối (relative path), Node.js tạo thư mục tạm ngay tại working directory hiện hành (`d:\scp`). Quanh dòng 260 và sau lệnh `_run_node_ts(script)` tại dòng 273, test không hề có lệnh dọn dẹp (`fs.rm`, `fs.rmSync`, hay `finally: shutil.rmtree`), để lại các thư mục tạm mồ côi này tại workspace root.
- **Nhóm 2 (`${SEEN_FILE}`):**
  - **Trạng thái thực tế:** Chuỗi `${SEEN_FILE}` được nhắc trong chỉ thị task ("file ${SEEN_FILE} là rác của guard cũ").
  - **Kiểm chứng thực tế:** File literal `${SEEN_FILE}` hoàn toàn vắng mặt trên đĩa (quét toàn bộ `d:\scp` và `C:\Users\check\.council-loop`). Lệnh `git log --all -S "SEEN_FILE"` trả về RỖNG (không có commit nào chứa chuỗi này).
  - **Phân loại nhận thức:** Giả thuyết về việc script shell cũ từng `touch '${SEEN_FILE}'` chưa có bằng chứng git lineage xác thực, do đó được đưa vào mục **3.5 UNKNOWN_WITH_REASON**. Ngược lại, việc guard hiện nay sử dụng `.guard-seen.txt` là có bằng chứng kiểm chứng độc lập (`PROCESS-V3.md:57`).
- **Nhóm 3 (`REPORT_DAILY_2026-10-28/29.md`):**
  - **Bằng chứng xác thực tại mã nguồn:** Script `scripts/ops/generate_visual_reports_schedule.ps1` tạo báo cáo markdown hằng ngày vào `reports/visual_reports/daily/`.
  - **Cơ chế gây rác:** Trong `.gitignore` (dòng 146–148 theo `GOV-15`), chỉ có thư mục con `reports/visual_reports/hourly/` và `assets/` được ignore, trong khi `reports/visual_reports/daily/` KHÔNG được ignore. Do đó, mỗi ngày scheduler chạy sẽ tạo ra 1 file báo cáo untracked mới.

### 3.2. (Thành phần b) Tại sao cách phân loại đúng theo Reality (Reality Evidence)
- **Bằng chứng Git Tracked vs Untracked:**
  - Lệnh `git ls-files reports/visual_reports/daily/` chứng minh chỉ có đúng 2 file `REPORT_DAILY_2026-10-25.md` và `REPORT_DAILY_2026-10-27.md` nằm trong Git index.
  - Lệnh `git status -s reports/visual_reports/daily/` chứng minh 3 file ngày 28, 29, 30 mang nhãn `??` (Untracked).
  - Tuân thủ nghiêm ngặt DNA #26 và kỷ luật không tự xóa git-tracked: Tuyệt đối không xóa file đã commit lên Git, chỉ đề xuất dọn dẹp các file untracked thực sự vô hiệu lực (ngày 28 và 29).

### 3.3. (Thành phần c) Lineage độc lập & Lệnh thực nghiệm (Independent Lineage & Verified Commands)
Toàn bộ dữ liệu trong báo cáo được đo đạc trực tiếp bằng các lệnh shell/PowerShell/Git sau, không dựa trên phán đoán:
1. **Kiểm tra nhóm 1 (`scp-audit-r2-empty-env-*`):**
   ```powershell
   Get-ChildItem -Path "d:\scp" -Filter "scp-audit-r2-empty-env-*" -Directory | Select-Object Name, CreationTime, @{N='Items';E={(Get-ChildItem $_.FullName).Count}}
   # Kết quả: scp-audit-r2-empty-env-kHXw6n (10/29 03:45, 0 items), scp-audit-r2-empty-env-pzTQpB (10/29 03:32, 0 items)
   ```
2. **Kiểm tra nhóm 2 (`${SEEN_FILE}` & `.guard-seen.txt`):**
   ```powershell
   # Quét tìm literal file trên đĩa:
   Get-ChildItem -Path "d:\scp", "C:\Users\check\.council-loop" -Filter "*SEEN_FILE*" -Recurse -ErrorAction SilentlyContinue
   # Kết quả: RỖNG (không có file nào trên đĩa).

   # Quét lịch sử git:
   git log --all -S "SEEN_FILE"
   # Kết quả: RỖNG (0 commit).

   # Đo lường chính xác file .guard-seen.txt:
   (Get-Item "C:\Users\check\.council-loop\.guard-seen.txt").Length
   # Kết quả: ~4,830–5,260 bytes (~5 KB; tại thời điểm 10/30 22:52 là 4,765 bytes, tại ca85 là 4,830 bytes, tăng tự nhiên mỗi lần guard append tên file mới được verify). Hoàn toàn không phải 27.2 KB.
   ```
3. **Kiểm tra nhóm 3 (`reports/visual_reports/daily`):**
   ```powershell
   git ls-files reports/visual_reports/daily/
   # Kết quả:
   # reports/visual_reports/daily/REPORT_DAILY_2026-10-25.md
   # reports/visual_reports/daily/REPORT_DAILY_2026-10-27.md

   git status -s reports/visual_reports/daily/
   # Kết quả:
   # ?? reports/visual_reports/daily/REPORT_DAILY_2026-10-28.md
   # ?? reports/visual_reports/daily/REPORT_DAILY_2026-10-29.md
   # ?? reports/visual_reports/daily/REPORT_DAILY_2026-10-30.md

   Get-ChildItem -Path "d:\scp\reports\visual_reports\daily" | Select-Object Name, Length
   # Kết quả: 5 file khớp chính xác từng byte: 3171, 3125, 5169, 4194, 3828 bytes.
   ```

### 3.4. (Thành phần d) Anti-Goodhart đổi quyết (Anti-Placebo Distinction)
- Không làm "xanh sạch hình thức" bằng cách xóa đại trà toàn bộ thư mục `reports/visual_reports/daily/`. Nếu xóa nhầm file git-tracked (ngày 25, 27), git status sẽ báo `D` (deleted), gây xung đột commit và vi phạm FA-06.
- Phân biệt rành mạch: File untracked stale (ngày 28, 29) $\rightarrow$ Đề xuất XÓA; File untracked mới nhất (ngày 30) $\rightarrow$ Đề xuất GIỮ làm tham chiếu; File git-tracked $\rightarrow$ BẮT BUỘC GIỮ NGUYÊN.

### 3.5. (Thành phần e) UNKNOWN_WITH_REASON (Khoảng trống Tri thức & Đề xuất Thượng nguồn)
- **UNKNOWN 1 (Nguồn gốc chuỗi `${SEEN_FILE}`):**
  - Vì `git log --all -S 'SEEN_FILE'` trả về rỗng và không file nào trên đĩa chứa chuỗi này, ta ghi nhận trạng thái danh dự `UNKNOWN_WITH_REASON` về nguồn gốc lịch sử của chuỗi `${SEEN_FILE}` (chỉ biết được nêu trong chỉ thị nhiệm vụ; không suy đoán bịa đặt kịch bản shell).
  - Bằng chứng thực tế duy nhất đã được kiểm chứng độc lập là: trên đĩa hiện tại KHÔNG có file `${SEEN_FILE}`, và guard đang vận hành chuẩn hóa bằng `.guard-seen.txt` (`PROCESS-V3.md:57`, ~4.8–5.3 KB, tăng dần khi guard ghi thêm tên file).
- **UNKNOWN 2 (Trạng thái kích hoạt của Scheduler):**
  - Chưa rõ script `generate_visual_reports_schedule.ps1` có đang được đăng ký chạy ngầm trong Windows Task Scheduler hay không. Nếu scheduler tiếp tục kích hoạt mà chưa có retention policy, thư mục `daily/` sẽ tiếp tục tích tụ untracked files.
- **Khuyến nghị Thượng nguồn:** Đề xuất cập nhật `.gitignore` cho `reports/visual_reports/daily/REPORT_DAILY_*.md` (giữ exception cho các file baseline đã commit) HOẶC bổ sung cơ chế tự xoay vòng (retention 3 ngày gần nhất) trong `generate_visual_reports_schedule.ps1`.

---
*Tài liệu được lưu trữ đồng bộ tại:*
- `C:\Users\check\.council-loop\inbox\godon-done.md`
- `d:\scp\godon-done.md`
