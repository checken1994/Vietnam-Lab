# Báo Cáo Trực Quan Hằng Ngày (2026-10-25)

> **Thời điểm xuất bản:** `2026-10-25 11:01:12 (UTC+7)`  
> **Khoảng thời gian giám sát:** `2026-10-24 11:01 -> 2026-10-25 11:01`  
> **Cơ chế vận hành:** `SCP_AUTOFIX_MODE=apply` | `SCP_EVOLUTION_ENABLED=1` | `SCP_CAPABILITY_LEVEL=FULL_PRODUCTION`  

---

## 1. BẢNG TRỰC QUAN HÓA 6 CHIỀU TIẾN HÓA (KPI DASHBOARD)

![KPI Dashboard](../assets/kpi_daily_2026-10-25.svg)

---

## 2. PHÂN TÍCH PHẢN BIỆN TỰ THÂN (WHY-GATE FALSIFICATION)

![WHY-Gate Donut Chart](../assets/why_donut_daily_2026-10-25.svg)

### Chi Tiết Phán Quyết Phản Biện Của WHY-Gate:
- **Tổng số quyết định ghi nhận:** `927` lượt.
- **REJECT (Bác bỏ bản vá xấu):** `0` lượt — Ngăn chặn code nuốt lỗi, cắt xén bảo mật.
- **ALLOW (Chấp thuận an toàn):** `430` lượt — Bản vá hợp lệ về mặt cấu trúc.
- **UPHOLD (Cần thêm bằng chứng):** `497` lượt.

#### Danh sách các trường hợp gian lận điển hình bị WHY-Gate tóm gọn gần nhất:
_Không có vụ việc bác bỏ nào trong khung thời gian này._

---

## 3. PHÂN BỐ LỖI AST VÀ CÁC FILE ĐƯỢC LLM VÁ MÃ

![Top Files Bar Chart](../assets/top_files_daily_2026-10-25.svg)

| Vị Trí File Mã Nguồn | Số Lỗi Phát Hiện | Hành Động Sửa Lỗi Của SCP |
|---|:---:|---|
| _Không có lỗi AST phát sinh_ | 0 | - |

---

## 4. TÌNH TRẠNG BỘ NHỚ BỀN VỮNG & TABULA RASA (ĐO ĐẠC VẬT LÝ)

- **Kho tri thức ([`data/knowledge.sqlite3`](file:///d:/scp/data/knowledge.sqlite3)):** `50 rows` (Bảo toàn 100% Tabula Rasa, 0 dữ liệu tĩnh mớm sẵn).
- **Task Kernel ([`data/ask_task_kernel.sqlite3`](file:///d:/scp/data/ask_task_kernel.sqlite3)):** `2` tasks, `20` events nguyên tử.
- **Bàn tay thao tác ([`data/hands/task_kernel.sqlite3`](file:///d:/scp/data/hands/task_kernel.sqlite3)):** `0` tasks, `0` events.
- **Kiểm tra tính toàn vẹn SQLite WAL thực tế (`PRAGMA integrity_check`):** `knowledge:ok(wal); ask_kernel:ok(wal); hands_kernel:ok(wal)`.

---

## 5. HẠ TẦNG VÀ TIẾN TRÌNH VẬN HÀNH 24/7 (ĐO ĐẠC THỜI GIAN THỰC)

- **Tổng số chu kỳ hoàn thành của Loop Scheduler:** `0` chu kỳ (Thành công: `0`, Lỗi: `0`, Tỷ lệ: `100.00%`).
- **Trạng thái thực tế 4 dịch vụ cốt lõi (Live HTTP Socket/Endpoint Probe):**
  + Backend API (`Port 8000`): **OFFLINE ([Errno 111] Connection refused)**
  + Loop Scheduler (`Port 3030`): **OFFLINE ([Errno 111] Connection refused)**
  + Agent Desktop (`Port 6080`): **OFFLINE ([Errno 111] Connection refused)**
  + Web Dashboard (`Port 3000`): **OFFLINE ([Errno 111] Connection refused)**
- **Trạng thái bộ lập lịch hệ điều hành (OS Scheduler Probe):**
  + Windows Task Scheduler: *N/A (Hệ điều hành 'linux' không dùng Windows Task Scheduler)* (Môi trường hiện tại: linux)

---
_Báo cáo được khởi tạo tự động bởi `tools/generate_visual_reports.py` dựa trên đo đạc vật lý 100% (Zero hardcoding)._