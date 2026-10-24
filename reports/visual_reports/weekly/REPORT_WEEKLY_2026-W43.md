# Báo Cáo Trực Quan Hằng Tuần (Tuần 43, Năm 2026)

> **Thời điểm xuất bản:** `2026-10-24 16:40:54 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-10-17 16:40 -> 2026-10-24 16:40`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_weekly_2026-W43.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_weekly_2026-W43.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `4,814` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `14` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `530` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `4,270` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
**Ca #1: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Increasing the timeout may give attackers more time to exploit the system, making the patch unsafe. | SELF_FALSIFIED: yes`

**Ca #2: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The patch extends the open‑connection window, potentially enabling attackers to hold connections longer and increase the risk of denial‑of‑service attacks. | SELF_FALSIFIED: yes`

**Ca #3: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The increased timeout could enable attackers to keep connections open longer, weakening security. | SELF_FALSIFIED: yes`

**Ca #4: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The patch actually tightens security by increasing the timeout to prevent premature failures that could be exploited by attackers to trigger denial‑of‑service conditions. | SELF_FALSIFI`

**Ca #5: Fix HardcodedTimeout at scp/security/policy.py:10: Timeout is too short**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Increasing the timeout could allow malicious connections to hang longer, giving attackers more time to exploit vulnerabilities. | SELF_FALSIFIED: yes`

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_weekly_2026-W43.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| [`app/scp/web_control/web_navigator.py`](/app/scp/web_control/web_navigator.py) | **4** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp/core/autonomous_cycle.py`](scp/core/autonomous_cycle.py) | **3** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/autofix/scanners/cross_func_taint_scanner_parts/_get_scp_call_graph.py`](/app/scp/autofix/scanners/cross_func_taint_scanner_parts/_get_scp_call_graph.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/autofix/subsystem_reviewer.py`](/app/scp/autofix/subsystem_reviewer.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/web_control/internet_search.py`](/app/scp/web_control/internet_search.py) | **2** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/core/db_manager_parts/get_db.py`](/app/scp/core/db_manager_parts/get_db.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/core/fast_learning_engine_parts/start_fast_learning_thread.py`](/app/scp/core/fast_learning_engine_parts/start_fast_learning_thread.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`app/scp/security/canary_monitor.py`](/app/scp/security/canary_monitor.py) | **1** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA (ĐO ĐẠC VẬT LÝ)

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `2092 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `1,534` tasks, `13,313` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `275` tasks, `2200` events.
- **Kiểm tra tính toàn vẹn SQLite WAL thực tế (`PRAGMA integrity_check`):** `knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal)`.

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `5,872` chu kỳ (Thành công: `3103`, Lỗi: `2769`, Tỷ lệ: `52.84%`).
- **Trạng thái thực tế 4 dịch vụ cốt lõi (Live HTTP Socket/Endpoint Probe):**
  + Backend API (`Port 8000`): **HTTP 200 OK (status=ok)**
  + Loop Scheduler (`Port 3030`): **HTTP 200 OK**
  + Agent Desktop (`Port 6080`): **HTTP 200 OK**
  + Web Dashboard (`Port 3000`): **HTTP 200 OK**
- **Trạng thái bộ lập lịch hệ điều hành (OS Scheduler Probe):**
  + `SCP-247-Supervisor`: **Disabled (Next run: N/A)**
  + `SCP-247-Recovery-Watchdog`: **Disabled (Next run: N/A)**
  + `SCP-247-Visual-Reports`: **Running (Next run: 24/10/2026 17:40:47)**

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dựa trên đo đạc vật lý 100% (Zero hardcoding)._