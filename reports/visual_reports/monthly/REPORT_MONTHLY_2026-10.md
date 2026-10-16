# Báo Cáo Trực Quan Hằng Tháng (Tháng 10, Năm 2026)

> **Thời điểm xuất bản:** `2026-10-16 20:10:11 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-09-16 20:10 -> 2026-10-16 20:10`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_monthly_2026-10.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_monthly_2026-10.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `38,928` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `1,512` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `8,701` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `28,715` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
**Ca #1: Bug PossiblyUndefinedName at D:\scp\scp\api_server_parts\_async_fact_check.py:46**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The variable `_fact_checker` is imported from `scp.utils.fact_checker` at the top of the file, so it is defined in the scope where it is used on line 46. | SELF_FALSIFIED: yes`

**Ca #2: Bug PossiblyUndefinedName at D:\scp\scp\api_server_parts\_async_fact_check.py:50**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The module imports and assigns `logger` at the module level (e.g., `logger = logging.getLogger(__name__)`), so it is defined when line 50 executes. | SELF_FALSIFIED: yes`

**Ca #3: Bug PossiblyUndefinedName at D:\scp\scp\api_server_parts\_async_fact_check.py:51**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: The variable `_fact_check_retract_queue` is defined earlier in the same module (e.g., at line 12) as a global queue, so it is not undefined at line 51. | SELF_FALSIFIED: yes`

**Ca #4: Bug PossiblyUndefinedName at D:\scp\scp\api_server_parts\_ask_impl.py:159**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: _image_detector được khai báo ở đầu module (hoặc imported từ một tệp khác) nên nó luôn có giá trị khi đến dòng 159. | SELF_FALSIFIED: yes`

**Ca #5: Bug PossiblyUndefinedName at D:\scp\scp\api_server_parts\_ask_impl.py:383**
- **Quyết định:** `REJECT` (Độ tin cậy: `0.0`)
- **Lý do bác bỏ nguyên văn:** `FALSIFICATION: Nếu biến `_multi_turn_tracker` được khai báo trước đó trong cùng module hoặc được import từ một module khác, cảnh báo này sẽ là một false positive và không phản ánh lỗi thực sự. | SELF_`

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_monthly_2026-10.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| [`scp\autofix\llm_fix_parts\_call_smart_llm.py`](D:\scp\scp\autofix\llm_fix_parts\_call_smart_llm.py) | **19** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\generate_fix_for_bug.py`](D:\scp\scp\autofix\llm_fix_parts\generate_fix_for_bug.py) | **13** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\process_bug_with_llm.py`](D:\scp\scp\autofix\llm_fix_parts\process_bug_with_llm.py) | **10** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\api_server_parts\_ask_impl.py`](D:\scp\scp\api_server_parts\_ask_impl.py) | **7** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\api_server_parts\_async_fact_check.py`](D:\scp\scp\api_server_parts\_async_fact_check.py) | **7** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\api_server_parts\lifespan.py`](D:\scp\scp\api_server_parts\lifespan.py) | **6** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\autofix\llm_fix_parts\_call_openrouter.py`](D:\scp\scp\autofix\llm_fix_parts\_call_openrouter.py) | **6** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |
| [`scp\hello_bug.py`](D:\scp\scp\hello_bug.py) | **3** | LLM sinh bản vá -> WHY-Gate kiểm chứng -> Fail-Closed |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `0 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `412` tasks, `4,056` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `0` tasks, `0` events.
- **Tính toàn vẹn SQLite WAL:** `Integrity: ok` (0 lock contention).

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `1,860` chu kỳ (Chu kỳ 5 phút/lần).
- **Trạng thái 4 dịch vụ cốt lõi:**
  + Backend API (`Port 8000`): HTTP 200 OK.
  + Loop Scheduler (`Port 3030`): HTTP 200 OK.
  + LLM Gateway Bridge (`Port 8081`): HTTP 200 OK.
  + Web Dashboard (`Port 3000`): HTTP 200 OK.
- **Windows Task Scheduler:** `SCP-247-Supervisor` & `Watchdog` duy trì trạng thái `Ready`.

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dưới sự kiểm toán độc lập của `/teamwork-preview`._