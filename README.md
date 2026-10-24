# SCP — Self-Correcting Pipeline (Agent OS)

[![License: AGPL-3.0-only](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](LICENSE)
[![Reality Tests](https://img.shields.io/badge/Reality_Tests-76%2F76_PASS-brightgreen.svg)](scripts/run_reality_tests_portable.py)
[![Contract Tests](https://img.shields.io/badge/Contract_Tests-99%2F99_PASS-brightgreen.svg)](tests/T02_contract/)
[![T00 Meta-Audit](https://img.shields.io/badge/T00_Meta--Audit-0_Regressions-brightgreen.svg)](tools/t00_meta_audit.py)
[![Tabula Rasa](https://img.shields.io/badge/Tabula_Rasa-100%25_Pure-blue.svg)](data/knowledge.sqlite3)

**SCP** là một hệ điều hành tác nhân AI (Agent OS) tự chủ và kiểm soát an toàn, tập trung vào khả năng **thực thi có kiểm soát, tự kiểm tra, tự phát hiện sai, tự học hỏi liên tục và phục hồi an toàn**.

SCP không phải là một mô hình ngôn ngữ độc lập. Hệ thống đóng vai trò tầng điều phối thông minh đứng giữa AI và môi trường thực thi để quản lý vòng đời tác vụ, ma trận quyền hạn, trạng thái bền vững ACID, rào chắn bảo vệ, xác minh thực chứng độc lập và các vòng lặp tự sửa lỗi (Self-Correction Loop).

Nguyên tắc tối thượng:

> **Reality > Model (Thực tế > Mô hình)**  
> Mọi kết luận chỉ có giá trị khi có bằng chứng vật lý kiểm chứng được (SQLite WAL rows, raw terminal stdout/stderr, live sockets). Khi thiếu chứng cứ hoặc trạng thái không chắc chắn, hệ thống luôn Fail-Closed về `UNKNOWN` hoặc `HUMAN_REVIEW` thay vì tự suy đoán.

---

## Kiến Trúc Cốt Lõi Của SCP

- **TaskKernel (17 Trạng thái)** — Quản lý vòng đời tác vụ với State Machine bất biến, Event Journal tuần tự, HMAC Merkle chain, khóa chuyển đổi nguyên tử và cơ chế khôi phục sau sự cố.
- **16-Skill Defense Middleware** — Biên dịch 16 kỹ năng SCP chuẩn thành middleware ASGI (`RuntimeSkillGuardMiddleware`) tự động chặn SSRF, Path Traversal, token HMAC giả mạo, kiểm soát chuyển đổi trạng thái và ngắt mạch Circuit Breaker.
- **Virtual Desktop & Computer Use (`agent-desktop:6080`)** — Container hóa màn hình ảo Xvfb (`:99`), trình duyệt Chromium GUI (`headless=False`), máy chủ noVNC Web và bàn phím/chuột ảo (`xdotool`, `pyautogui`), cho phép AI thao tác GUI hoàn chỉnh mà không bao giờ can thiệp vào chuột/phím máy thật.
- **Free API Adapter Bridge** — Cầu nối động ánh xạ 60 chuyên ngành vào 2.120 Public APIs (1.010 APIs `auth="No"`), truy xuất 4 tầng với bộ nhớ đệm SQLite WAL đạt độ trễ trung bình 0.299ms (p95 0.351ms).
- **Tabula Rasa 100%** — Không nhồi nhét bất kỳ từ điển sự thật tĩnh hay mồi nhân tạo nào vào mã nguồn; toàn bộ tri thức được tự học từ môi trường bên ngoài (Wikipedia Open Data, Web search) và lưu bền vững vào `data/knowledge.sqlite3`.
- **LLM Gateway & Circuit Breakers** — Điều phối nhiều nhà cung cấp (OpenRouter, Groq), cascade dự phòng theo ngân sách kinh tế và tự động ngắt mạch khi upstream quá tải.
- **Vòng Vận Hành Tự Chủ 24/7 (Loop Scheduler)** — Vận hành liên tục các chu trình `discover` → `qualify` → `authorize` → `plan` → `execute` → `verify` → `persist` → `reconcile`.

---

## Trạng Thái Kiểm Chứng Thực Tế (Cập nhật 2026-10-18)

> Báo cáo chi tiết: [GA.md](GA.md) · Kiểm toán tự động: `tools/t00_meta_audit.py` · Bản đồ kiểm thử: `tests/`

Toàn bộ kết quả kiểm thử được xác nhận trực tiếp trên exact Git SHA hiện hành:

| Bộ phận kiểm chứng | Lệnh thực thi thực tế | Kết quả đo được | Đánh giá |
| :--- | :--- | :---: | :---: |
| **Reality Portable Suite** | `python scripts/run_reality_tests_portable.py` | **76/76 PASS** (0 fail, 0 skip) | 🟢 **PASS** |
| **Deprecation Contract** | `pytest tests/T02_contract/test_m3_architectural_deprecation.py` | **67/67 PASS** (2.83s) | 🟢 **PASS** |
| **Docker Desktop & Middleware**| `pytest tests/T02_contract/test_docker_desktop_and_skills_middleware.py` | **32/32 PASS** (4.91s) | 🟢 **PASS** |
| **T00 Meta-Audit Gate** | `python tools/t00_meta_audit.py` | **0 new regressions** | 🟢 **PASS** |
| **Secret Scanner (SEC-02)** | `python tools/scan_secrets.py --path scp` | **0 hardcoded secrets** | 🟢 **CLEAN** |
| **SQLite WAL Benchmark** | 200 iterations lookups | **0.299ms avg / 0.351ms p95** | 🟢 **<1.5ms SLA** |

---

## Triển Khai Toàn Diện Bằng Docker Compose (Khuyến nghị)

SCP đã được container hóa 100% thành cụm 5 microservices. Toàn bộ cơ sở dữ liệu `data/*.sqlite3` ở máy chủ được bind-mount trực tiếp vào container với chế độ SQLite WAL để bảo toàn độ bền dữ liệu.

### Khởi chạy 1 lệnh duy nhất:

```bash
docker compose -f docker-compose.full.yml up -d
```

### Các cổng dịch vụ sẵn sàng:

| Dịch vụ | Cổng | Giao thức | Mục đích sử dụng |
| :--- | :---: | :---: | :--- |
| **Web Dashboard** | `3000` | HTTP | Giao diện điều khiển trung tâm Next.js 16 ([http://127.0.0.1:3000](http://127.0.0.1:3000)) |
| **Virtual Desktop noVNC** | `6080` | HTTP / WS | Xem trực tiếp AI lướt Chromium và thao tác GUI ([http://127.0.0.1:6080](http://127.0.0.1:6080)) |
| **Backend API Server** | `8000` | HTTP / REST | API lõi với 16-Skill Defense Middleware ([http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready)) |
| **Loop Scheduler** | `3030` | HTTP / Node | Điều phối vòng lặp tự học và tự sửa lỗi 24/7 |

> Ghi chú: Cổng LLM Gateway Bridge `:8081` đã bị xoá khỏi kiến trúc cùng
> thư mục `mini-services/llm-bridge/` (Zero-Bun deprecation, 2026-10-24).

### Dừng toàn bộ cụm container:

```bash
docker compose -f docker-compose.full.yml down
```

---

## 🗺️ Bản Đồ 12 Hệ thống (Bản đồ trực quan hoá kiến trúc SCP)

Công cụ trực quan hoá: 12 hệ thống · module/skill thật của repo · bản đồ pixel-art 2D, chạy độc lập, **chỉ đọc**.

| Nơi truy cập | URL | Lớp dữ liệu |
| :--- | :--- | :--- |
| Bản phục vụ bởi Dashboard | [http://127.0.0.1:3000/scp-office/](http://127.0.0.1:3000/scp-office/) | **LIVE** nếu proxy `:5177` đang chạy |
| Máy chủ độc lập (khuyến nghị) | [http://127.0.0.1:5177/](http://127.0.0.1:5177/) | **LIVE** qua proxy chỉ đọc |

```bash
cd simulation
npm start                  # http://127.0.0.1:5177 — bản đồ + proxy chỉ đọc
npm test                   # 50 test contract (node --test)
npm run sync:dashboard     # đồng bộ bản phục vụ tại dashboard/public/scp-office/
```

**Cam kết dữ liệu (DNA #4, FA-04):**
- Trang này **không có dữ liệu mô phỏng**: không nhân vật, không tác vụ giả, không nhật ký giả, không số bịa.
- Mọi số liệu đến từ `/api/scp/{status,health,activity,metrics,permissions}` — chỉ đọc, không có endpoint ghi.
- Trạng thái được xác nhận tường minh: **LIVE** khi đọc được dữ liệu SCP thật; **không đọc được** ⇒ mọi ô hiện “—”, không dùng số liệu cũ.
- **Nhật ký & Kiểm toán chỉ chứa sự kiện LIVE.**
- Cổng LLM `:8081` đã bị xoá khỏi kiến trúc (Zero-Bun) → không được tính là cổng “live”.

### Duyệt thủ công (human-in-the-loop)

Backend có sẵn cơ chế duyệt (`/v105/autofix/permissions`) nhưng trước đây dashboard **không có chỗ nào để người dùng bấm duyệt**. Nay đã có:

- **Panel “Yêu cầu chờ bạn duyệt”** trên trang tổng quan (đọc hàng đợi THẬT từ backend, tự làm mới 10s).
- **`GET /api/scp/permissions`** — danh sách yêu cầu đang chờ.
- **`POST /api/scp/permissions/{request_id}`** — quyết định `approve` / `deny`, **bắt buộc có ghi chú** (backend chạy `UnderstandingChecker`, chặn note rỗng hoặc copy-paste).
- Ô chỉ số **“Đang chờ bạn duyệt”** đọc live (ô “R7 cần human approval” là số lịch sử tĩnh của round-7).

> **Quyền của SCP trong Docker:** `SCP_AUTO_APPROVE_TIER3` **không còn ghi cứng** trong compose — `.env` quyết định (mặc định `0` ⇒ tier-3 dừng lại chờ người, đúng ý operator). Runtime toggle vẫn còn: `POST /v105/autofix/tier3-auto/{0|1}` (có audit log).

Chi tiết: [`simulation/README.md`](simulation/README.md).

---

## Cài Đặt & Khởi Động Thủ Công (Host Environment)

Nếu muốn chạy trực tiếp trên máy chủ mà không dùng Docker:

### 1. Máy chủ Linux / macOS
```bash
./setup-scp.sh
```

### 2. Máy chủ Windows
```bat
setup-scp.bat
```

Script sẽ tự động khởi tạo môi trường Python virtualenv `.venv`, cài đặt các gói phụ thuộc theo bảng băm SHA256 (`requirements.hashes.txt`), tạo các file dữ liệu SQLite WAL và khởi động các dịch vụ nền.

---

## Cấu Hình LLM Gateway

SCP tuân thủ nguyên tắc **API-First Orchestration** (chỉ điều phối API ngoài, không chạy inference ngốn tài nguyên tại local). Cấu hình API key trong `.env`:

```env
# OpenRouter Gateway
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=openrouter/free

# Groq Fast Fallback
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

Xem danh sách đầy đủ tất cả các biến môi trường tại [`.env.example`](.env.example).

---

## Bản Quyền & Giấy Phép (License)

SCP là dự án áp dụng đa giấy phép (Multi-license repository):
- **SCP Core Runtime & Agent State Machine**: Giấy phép `AGPL-3.0-only`.
- **SDK, Connector, Adapter Bridge & Public Integrations**: Giấy phép `Apache-2.0`.

Chi tiết quy hoạch giấy phép xem tại [`docs/legal/LICENSES.md`](docs/legal/LICENSES.md) và văn bản chuẩn tại [`LICENSE`](LICENSE).

---

**Tác giả**: Minh Nguyen Van  
**Email**: checken1994@gmail.com  
