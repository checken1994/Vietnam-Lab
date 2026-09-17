# SCP trên Windows — Hướng dẫn đầy đủ

> **🐔 Gà:** "Chạy trên máy Windows."
>
> **🤖 SCP:** "3 file `.bat` — install (1 lần) → start (chạy) → stop (dừng). 1 click mỗi cái."

---

## 🚀 Quick Start (3 bước)

### Bước 1 — Cài đặt (chỉ 1 lần đầu)

Double-click **`install-scp.bat`**

Script tự:
- Kiểm tra Python, Bun, Node.js (báo thiếu nếu chưa cài)
- Cài Python deps (fastapi, uvicorn, httpx, pydantic)
- Cài LLM Bridge deps (z-ai-web-dev-sdk)
- Cài Loop Scheduler deps
- Cài Dashboard deps (Next.js, shadcn/ui)
- Tạo `.env` từ template (nếu chưa có)
- Tạo `scp\data\` dirs

**Nếu script báo thiếu Python/Node/Bun:**
- Python: https://www.python.org/downloads/ (tick "Add Python to PATH")
- Node.js: https://nodejs.org/
- Bun: mở PowerShell → `npm install -g bun`

### Bước 2 — Cấu hình .env (chỉ 1 lần)

Mở file `.env` bằng Notepad. Tìm dòng:
```
OPENROUTER_API_KEY=<redacted>
```
Điền key của Gà (lấy ở https://openrouter.ai/keys, tạo free account):
```
OPENROUTER_API_KEY=<redacted>
```

Lưu file. (Nếu Gà có 3 keys, điền cả `OPENROUTER_API_KEY_2` + `_3` cho round-robin.)

### Bước 3 — Chạy SCP

Double-click **`start-scp.bat`**

Script tự:
1. Dừng services cũ (nếu có)
2. Khởi động LLM Bridge (port 11434)
3. Khởi động Loop Scheduler (port 3030)
4. Khởi động SCP Python (port 8000) — đợi ~60s boot
5. Khởi động Dashboard Next.js (port 3000)
6. Mở browser tới http://localhost:3000

**Sẵn sàng khi thấy:**
```
✅ SCP SYSTEM ĐANG CHẠY!
📊 Dashboard:       http://localhost:3000
🔍 SCP /health:     http://localhost:8000/health
```

### Dừng SCP

Double-click **`stop-scp.bat`** — dừng tất cả 4 services.

---

## 📋 3 file Gà cần

| File | Khi nào dùng | Làm gì |
|---|---|---|
| **`install-scp.bat`** | 1 lần đầu, hoặc khi update | Cài tất cả dependencies |
| **`start-scp.bat`** | Mỗi lần muốn chạy SCP | Khởi động 4 services + mở browser |
| **`stop-scp.bat`** | Khi muốn dừng | Dừng tất cả |

---

## 🌐 Ports & URLs

| Service | Port | URL | Mục đích |
|---|---|---|---|
| Dashboard | 3000 | http://localhost:3000 | Giao diện chính (25 sections) |
| SCP API | 8000 | http://localhost:8000 | Python server (73 routes) |
| LLM Bridge | 11434 | http://localhost:11434 | Ollama giả → z-ai-web-dev-sdk |
| Loop Scheduler | 3030 | http://localhost:3030 | Cron audit mỗi 5 phút |

**Test SCP /ask (trong PowerShell hoặc CMD):**
```cmd
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d "{\"question\":\"What is the capital of France?\"}
```
Kết quả mong đợi:
```json
{"verdict":"PASS","final_answer":"thủ đô France = Paris","confidence":0.85,...}
```

> Request `/ask` còn nhận `contexts` / `retrieved_context` (bằng chứng do client gửi) và có cơ chế
> auto-retrieval corpus khi thiếu context — đặc tả theo code:
> [docs/api/ask-request-fields.md](../api/ask-request-fields.md).

---

## 🔧 Troubleshooting

### Lỗi: "Python chưa cài"
- Tải Python 3.12+ từ https://www.python.org/downloads/
- Khi cài, **TICK "Add Python to PATH"** (quan trọng!)
- Restart CMD/PowerShell sau khi cài

### Lỗi: "Bun chưa cài"
- Mở PowerShell, chạy: `npm install -g bun`
- Nếu npm chưa có → cài Node.js trước: https://nodejs.org/

### Lỗi: "port 3000/8000/11434/3030 đã dùng"
- Chạy `stop-scp.bat` trước
- Hoặc mở Task Manager, kill process đang giữ port

### Lỗi: SCP /ask trả 500 hoặc timeout
- Kiểm tra `.env` có `OPENROUTER_API_KEY` không
- Kiểm tra LLM Bridge chạy (mở http://localhost:11434/api/tags — phải trả JSON)
- Xem log SCP: mở cửa sổ "SCP-Python" (minimized trong taskbar)

### Lỗi: Dashboard trắng / không load
- Đợi 30-60s sau khi start (Next.js compile lần đầu chậm)
- Check cửa sổ "SCP-Dashboard" có lỗi gì không

### SCP boot quá lâu (>120s)
- Bình thường: 40-90s (lifespan chạy ThreatSimulator + healing)
- Nếu >120s: có thể Ollama/LLM Bridge không chạy → SCP retry LLM call

---

## 📁 Cấu trúc thư mục (sau khi giải nén zip R11)

```
C:\Users\check\Downloads\scp\
├── install-scp.bat          ← Chạy đầu tiên (1 lần)
├── start-scp.bat            ← Chạy để khởi động
├── stop-scp.bat             ← Chạy để dừng
├── WINDOWS-README.md        ← File này
├── .env                     ← Cấu hình (điền API key ở đây)
├── dashboard\               ← Next.js 16 dashboard
│   ├── src\
│   ├── package.json
│   └── ...
├── scp\                     ← SCP Python codebase (377 .py)
│   ├── __main__.py          ← Entry point (python -m scp 8000)
│   ├── api_server.py
│   ├── autofix\             ← Engine v4 (63 .py, 12/12 modules wired)
│   ├── llm_gateway\
│   ├── .env.example         ← Template (copy thành .env nếu chưa có)
│   └── data\                ← Data dirs (auto-created by install)
├── mini-services\
│   ├── llm-bridge\          ← Port 11434 (Ollama → z-ai-web-dev-sdk)
│   └── loop-scheduler\      ← Port 3030 (cron audit)
└── docs\                    ← Báo cáo R9/R10/R11 + worklog
```

---

## ❓ Câu hỏi thường gặp

**Q: Tại sao phải cài Python + Node + Bun?**
A: SCP Python cần Python. Dashboard + mini-services cần Bun/Node (JavaScript runtime). 3 thứ này là nền tảng, cài 1 lần.

**Q: Có cần Ollama không?**
A: KHÔNG. LLM Bridge (mini-service) thay thế Ollama — forward sang z-ai-web-dev-sdk. Gà không cần cài Ollama.

**Q: Có cần API key trả phí không?**
A: KHÔNG. OpenRouter có 17 models FREE (xem `.env`). `OPENROUTER_API_KEY` free tại https://openrouter.ai/keys.

**Q: Chạy trên Mac/Linux được không?**
A: Được. Dùng `start-scp.sh` + `stop-scp.sh` (đã có sẵn trong zip) thay vì `.bat`.

**Q: Mỗi lần chạy tốn bao lâu?**
A: Install: 5-10 phút (1 lần). Start: 60-90s (mỗi lần). Stop: 2s.

---

**Built by Gà Lab · Windows Edition · "HỎI. THỬ NHỎ. NHÌN THỰC TẾ. SỬA. RỒI HỎI LẠI."**
