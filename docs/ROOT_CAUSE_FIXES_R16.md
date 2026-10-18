# SCP R16 — Root Cause Fixes + Startup Guide
## "Fix gốc rễ, không fix cascade 1→2→3"

> **🐔 Gà:** "làm hết đi. xem gốc rễ vấn đề đang lỗi là gì và fix nó để không phải lỗi 1→2→3 .v.v.v. python chạy mà $ bun --hot index.ts [loop-scheduler] booting... [loop-scheduler] initial SCP liveness: offline không chạy?"

---

## 1. Root Cause Analysis (5-Whys)

### 🔴 Vấn đề 1: "SCP liveness: offline" — SCP Python không chạy

```
Symptom: Loop scheduler báo "initial SCP liveness: offline"
Why 1: SCP Python server (port 8000) không chạy
Why 2: Bạn chỉ chạy `bun --hot index.ts` (loop-scheduler) — KHÔNG chạy `python -m scp`
Why 3: Không có 1 lệnh duy nhất khởi động TẤT CẢ services
Why 4: start-scp.bat tồn tại nhưng cần chạy thủ công, không tự động
Why 5 (ROOT): Người dùng không biết phải chạy 4 services theo thứ tự
```

**ROOT FIX:** Chạy `start-scp.bat` (Windows) hoặc `./start-scp.sh` (Linux/Mac) — nó khởi động ĐÚNG THỨ TỰ:
1. LLM Bridge (port 11434) — phải chạy đầu tiên (SCP phụ thuộc nó)
2. Loop Scheduler (port 3030)
3. SCP Python (port 8000) — boot ~60s
4. Dashboard Next.js (port 3000)

**KHÔNG chạy từng service riêng** — sẽ thiếu dependency.

---

### 🔴 Vấn đề 2: HTTP 429 Too Many Requests (Rate Limiting)

```
Symptom: Log spam "HTTP 429 Too Many Requests" + "429 queue full"
Why 1: Quá nhiều request đến OpenRouter API cùng lúc
Why 2: SCP /ask fires nhiều LLM calls song song (judge + why + learning + ...)
Why 3: Chỉ dùng 1 API key → rate limit bị hit nhanh
Why 4: Không có cache → cùng câu hỏi gửi nhiều lần = nhiều API calls
Why 5 (ROOT): llm-bridge không có multi-key round-robin + không có response cache
```

**ROOT FIX (R16-ROOT-FIX-1 + R16-ROOT-FIX-2):**
- **Multi-key round-robin:** 3 API keys = 3x rate limit budget. Mỗi call dùng key kế tiếp. Khi 1 key bị 429, key khác nhận traffic.
- **Response cache:** Cùng câu hỏi → cache hit (0ms, 0 API calls). TTL 5 phút.
- **Backoff tăng:** 0ms → 500ms → 1s → 2s → 4s (thêm 4s so với R15).

**Files fixed:**
- `mini-services/llm-bridge/index.ts` — thêm `OPENROUTER_API_KEYS[]` round-robin + `_llmCache` Map + cache endpoints

---

### 🔴 Vấn đề 3: "database disk image is malformed" (DB Corruption)

```
Symptom: "Canary scan error: database disk image is malformed"
Why 1: SQLite DB file bị corrupt
Why 2: Process crash giữa ghi operation, hoặc concurrent write không lock đúng
Why 3: Corruption chỉ phát hiện khi query FAIL — lúc đó cascading errors đã xảy ra
Why 4: Không có integrity check lúc startup
Why 5 (ROOT): DB corruption không được detect + recover EARLY
```

**ROOT FIX (R16-ROOT-FIX-4):**
- **Pre-flight integrity check:** `get_db()` chạy `PRAGMA quick_check` TRƯỚC khi tạo persistent connection.
- **Auto-recovery:** Nếu corrupt → `VACUUM INTO` tạo DB mới từ DB cũ (skip bad pages) → swap files.
- **Backup:** DB cũ được rename thành `v13.db.broken.<timestamp>` (không mất data).

**Files fixed:**
- `scp/core/db_manager.py` — thêm `_preflight_integrity_check()` function, gọi trong `get_db()`

---

### 🔴 Vấn đề 4: 401 Unauthorized trên /v105/autofix/run-audit

```
Symptom: "POST /v1/05/autofix/run-audit HTTP/1.1" 401 Unauthorized
Why 1: Loop scheduler gửi Bearer token nhưng SCP reject
Why 2: SCP_AUTH_TOKEN trong loop-scheduler = empty
Why 3: Loop-scheduler đọc process.env.SCP_AUTH_PASSWORD nhưng biến này empty
Why 4: Bun KHÔNG tự động load .env file (khác Node.js với dotenv)
Why 5 (ROOT): Mini-services (Bun) không load .env → tất cả env vars empty
```

**ROOT FIX (R16-ROOT-FIX-5):**
- **Load .env trong Bun:** Thêm `_loadEnvFile()` function vào ĐẦU của `loop-scheduler/index.ts` + `llm-bridge/index.ts`.
- **Walk up tìm .env:** Tìm `.env` ở project root (2 levels up từ `mini-services/*/`), fallback `mini-services/.env`, fallback `cwd/.env`.
- **Parse .env:** Đọc KEY=VALUE, strip quotes, không override existing env vars.

**Files fixed:**
- `mini-services/loop-scheduler/index.ts` — thêm `_loadEnvFile()` ở đầu
- `mini-services/llm-bridge/index.ts` — thêm `_loadEnvFile()` ở đầu

---

## 2. Cách chạy SCP đúng cách

### Bước 1: Cài đặt (chạy 1 lần duy nhất)

```bash
# Windows
install-scp.bat

# Linux/Mac
chmod +x install-scp.sh && ./install-scp.sh
```

Script này sẽ:
- Kiểm tra Python, Node.js
- Tạo Python venv tại `scp/venv/`
- `pip install -r scp/requirements.txt`
- `pip install -r scp/requirements-dev.txt` (bao gồm hypothesis — R16 fix)
- `cd mini-services/llm-bridge && npm install`
- `cd mini-services/loop-scheduler && npm install`
- `cd dashboard && npm install`
- Copy `.env.example` → `.env` (nếu chưa có)

### Bước 2: Cấu hình .env

Mở file `.env` ở thư mục gốc. Verify các biến sau:

```env
# OpenRouter — 3 keys round-robin (R16 fix dùng tất cả 3)
OPENROUTER_API_KEY=<redacted>
OPENROUTER_API_KEY_2=sk-or-v1-...
OPENROUTER_API_KEY_3=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openrouter/free

# SCP server
SCP_PORT=8000
SCP_HOST=127.0.0.1

# Auth — loop-scheduler cần để gọi /v105/autofix/run-audit
SCP_AUTH_PASSWORD=<redacted>
SCP_AUTH_TOKEN_SECRET=<redacted>

# Skip startup gate (dev mode — để test nhanh)
SCP_SKIP_STARTUP_GATE=1
```

### Bước 3: Khởi động toàn bộ hệ thống

```bash
# Windows — MỘT LỆNH DUY NHẤT:
start-scp.bat

# Linux/Mac:
./start-scp.sh
```

Script sẽ mở 4 cửa sổ terminal:
1. **SCP-LLM-Bridge** → port 11434 (chạy đầu tiên, đợi 3s)
2. **SCP-Loop-Scheduler** → port 3030 (chạy thứ 2, đợi 1s)
3. **SCP-Python** → port 8000 (chạy thứ 3, boot ~60s)
4. **SCP-Dashboard** → port 3000 (chạy thứ 4)

**Đợi SCP boot xong** (script tự poll `/health`, tối đa 180s).

### Bước 4: Verify

Mở browser:
- **Dashboard:** http://localhost:3000
- **SCP API:** http://127.0.0.1:8000/health → `{"status":"ok"}`
- **Loop Scheduler:** http://127.0.0.1:3030/ → status JSON
- **LLM Bridge:** http://127.0.0.1:11434/ → HTML info
- **Cache stats (R16 mới):** http://127.0.0.1:11434/api/cache/stats

---

## 3. Tại sao "bun --hot index.ts" một mình không chạy?

**Vấn đề:** Bạn chạy `bun --hot index.ts` trong `mini-services/loop-scheduler/` — nó báo:
```
[loop-scheduler] booting — port=3030
[loop-scheduler] initial SCP liveness: offline
```

**Đây là hành vi ĐÚNG** — loop-scheduler probe SCP tại `http://127.0.0.1:8000/health`. Nếu SCP chưa chạy → "offline". Loop-scheduler tiếp tục probe mỗi 300s, sẽ báo "online" khi SCP lên.

**Nhưng SCP chưa chạy vì bạn chỉ khởi động 1/4 services.**

**Giải pháp:** Chạy `start-scp.bat` — nó khởi động ĐÚNG THỨ TỰ 4 services.

---

## 4. Các root-cause fixes trong R16 (5 fixes)

| ID | Fix | File | Root cause đã sửa |
|---|---|---|---|
| **R16-ROOT-FIX-1** | Multi-key round-robin (3 OpenRouter keys) | `mini-services/llm-bridge/index.ts` | 429 rate limiting — 3x budget |
| **R16-ROOT-FIX-2** | Response cache (TTL 5 min, max 200 entries) | `mini-services/llm-bridge/index.ts` | 429 — identical questions = 0 API calls |
| **R16-ROOT-FIX-3** | Backoff tăng thêm 4s (0→0.5→1→2→4s) | `mini-services/llm-bridge/index.ts` | 429 — thêm 1 retry attempt |
| **R16-ROOT-FIX-4** | DB integrity check + auto-recovery (VACUUM INTO) | `scp/core/db_manager.py` | DB corruption — detect+recover early |
| **R16-ROOT-FIX-5** | .env loader cho Bun mini-services | `mini-services/llm-bridge/index.ts` + `loop-scheduler/index.ts` | 401 auth — env vars empty |

**Cộng với 10 fixes từ R16 trước đó:**
- P0-1: ReActAgent không ghi đè verdict (SCP killer)
- P0-2: SLM deterministic + reality check
- P0-3: execute_plan không PASS không source
- P1-1: Wire verify_chain vào engine
- P1-2: Wire check_pending_permissions vào runner
- P1-3: policy_gate fail-closed cho BLOCK
- P2-1: Fix broken test regex
- P2-2: understanding_check fail-closed
- P3-1a: Add hypothesis to requirements-dev
- P3-1b: target-version py39 → py310

**Tổng: 15 root-cause fixes.**

---

## 5. Verification

### 5.1 Python ast.parse

```bash
cd scp
python3 -c "
import ast
for f in ['core/db_manager.py', 'runtime/judge.py', 'core/react_agent.py',
          'runtime/judge_parts/judgecore_mixin.py', 'meta/why_execute_plan.py',
          'autofix/engine.py', 'autofix/runner.py', 'autofix/policy_gate.py',
          'meta/understanding_check.py', 'tests/external_audit/test_cascade.py']:
    ast.parse(open(f).read())
    print(f'✓ {f}')
"
```

### 5.2 Bun syntax check

```bash
cd mini-services/llm-bridge
bun build index.ts --target=bun --outdir /tmp/check  # ✓ no errors
cd ../loop-scheduler
bun build index.ts --target=bun --outdir /tmp/check  # ✓ no errors
```

### 5.3 Reality tests

```bash
# R16-ROOT-FIX-1: multi-key round-robin
grep -c "OPENROUTER_API_KEYS\|getNextApiKey" mini-services/llm-bridge/index.ts
# EXPECT: ≥3 (array def + function + call)

# R16-ROOT-FIX-2: cache
grep -c "_llmCache\|_cacheGet\|_cacheSet" mini-services/llm-bridge/index.ts
# EXPECT: ≥6

# R16-ROOT-FIX-4: DB integrity check
grep -c "_preflight_integrity_check\|VACUUM INTO" scp/core/db_manager.py
# EXPECT: ≥3

# R16-ROOT-FIX-5: .env loader
grep -c "_loadEnvFile" mini-services/llm-bridge/index.ts mini-services/loop-scheduler/index.ts
# EXPECT: 2 (1 per file)
```

---

## 6. Troubleshooting

### "SCP liveness: offline" vẫn xuất hiện

1. Kiểm tra SCP-Python cửa sổ — có lỗi không?
2. Đợi 60-90s (SCP boot chậm)
3. Manual check: `curl http://127.0.0.1:8000/health`
4. Nếu lỗi: đọc `scp-server.log`

### 429 vẫn spam

1. Kiểm tra cache stats: `curl http://127.0.0.1:11434/api/cache/stats`
   - `cache_size` nên > 0 sau vài requests
   - `keys_available` nên = 3
2. Clear cache: `curl -X POST http://127.0.0.1:11434/api/cache/clear`
3. Verify 3 API keys trong `.env` đều hợp lệ (test từng key)
4. Giảm LOOP_INTERVAL_SEC (mặc định 300s = 5 phút, tăng lên 600s nếu cần)

### "database disk image is malformed"

1. R16-ROOT-FIX-4 sẽ auto-recover — kiểm tra log:
   ```
   [R16-ROOT-FIX-4] DB corrupted. Attempting VACUUM INTO recovery...
   [R16-ROOT-FIX-4] DB recovered via VACUUM INTO. Old corrupted DB saved as v13.db.broken.<ts>
   ```
2. Nếu recovery fail:
   ```bash
   # Delete corrupt DB (data will be lost — restore from backup if available)
   del data\v13.db   # Windows
   rm data/v13.db    # Linux/Mac
   # Restart SCP — will create fresh DB
   ```

### 401 Unauthorized

1. Verify `.env` có `SCP_AUTH_PASSWORD` hoặc `SCP_AUTH_TOKEN_SECRET`
2. Restart loop-scheduler (nó sẽ load .env — R16-ROOT-FIX-5)
3. Test auth:
   ```bash
   curl -H "Authorization: Bearer 123456" http://127.0.0.1:8000/v105/autofix/stats
   ```
4. Nếu vẫn 401: kiểm tra SCP-Python log cho "SCP_AUTH_TOKEN_SECRET not configured"

---

## 7. Tóm tắt

| Trước R16 | Sau R16 |
|---|---|
| Chạy `bun --hot index.ts` một mình → offline | Chạy `start-scp.bat` → 4 services lên đúng thứ tự |
| 429 spam (1 key, no cache) | 3 keys round-robin + cache (3x budget + 0 duplicate calls) |
| DB corrupt → cascading errors | Auto-detect + auto-recover via VACUUM INTO |
| 401 auth (env vars empty) | .env loader trong Bun mini-services |

**15 root-cause fixes total (5 root-cause + 10 từ R16 trước).**

> **KHÔNG HOÀN THIỆN. KHÔNG THẤT BẠI. KHÔNG HOÀN TẤT. ĐANG HOẠT ĐỘNG.** (DNA #23)
>
> **Và Reality vẫn giữ quyền trả lời cuối cùng.** (DNA #26 🌍)
