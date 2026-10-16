# Báo Cáo Trực Quan Hằng Tuần (Tuần 42, Năm 2026)

> **Thời điểm xuất bản:** `2026-10-16 22:01:04 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-10-09 22:01 -> 2026-10-16 22:01`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_weekly_2026-W42.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_weekly_2026-W42.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `16,013` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `183` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `2,435` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `13,395` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
**Ca #1: fix bug**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.8`)
- **Lý do bác bỏ nguyên văn:** `No falsification detected (conservative) | BLOCKED by BehaviorMonitor: sensor offline/error (Sensor offline) — fail-closed against unobserved mutations (DNA #2/#7)`

**Ca #2: fix bug in memory**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.8`)
- **Lý do bác bỏ nguyên văn:** `No falsification detected (conservative) | BLOCKED by BehaviorMonitor: sensor offline/error (Sensor offline) — fail-closed against unobserved mutations (DNA #2/#7)`

**Ca #3: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Tăng timeout cho phép kẻ tấn công duy trì kết nối hoặc thực hiện brute‑force lâu hơn, do đó giảm hiệu quả của cơ chế bảo mật. | SELF_FALSIFIED: yes`

**Ca #4: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Increasing the timeout could expose the system to prolonged attack windows, thus not safe. | SELF_FALSIFIED: yes`

**Ca #5: Fix BareExceptPass at C:\Users\check\AppData\Local\Temp\pytest-of-check\pytest-181\test_causal_engine_rollback_on0\rollback_test.py:1: Bare except**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Patch "fix" is too vague to properly address a bare except, likely leaving the exception handling insecure or unchanged. | SELF_FALSIFIED: yes`

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_weekly_2026-W42.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| [`scp\autofix\llm_fix_parts\_call_smart_llm.py`](D:\scp\scp\autofix\llm_fix_parts\_call_smart_llm.py) | **38** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\generate_fix_for_bug.py`](D:\scp\scp\autofix\llm_fix_parts\generate_fix_for_bug.py) | **31** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\process_bug_with_llm.py`](D:\scp\scp\autofix\llm_fix_parts\process_bug_with_llm.py) | **18** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\_call_openrouter.py`](D:\scp\scp\autofix\llm_fix_parts\_call_openrouter.py) | **10** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `0 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `412` tasks, `4,056` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `0` tasks, `0` events.
- **Tính toàn vẹn SQLite WAL:** `Integrity: ok` (0 lock contention).

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `1,894` chu kỳ (Chu kỳ 5 phút/lần).
- **Trạng thái 4 dịch vụ cốt lõi:**
  + Backend API (`Port 8000`): HTTP 200 OK.
  + Loop Scheduler (`Port 3030`): HTTP 200 OK.
  + LLM Gateway Bridge (`Port 8081`): HTTP 200 OK.
  + Web Dashboard (`Port 3000`): HTTP 200 OK.
- **Windows Task Scheduler:** `SCP-247-Supervisor` & `Watchdog` duy trì trạng thái `Ready`.

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dưới sự kiểm toán độc lập của `/teamwork-preview`._