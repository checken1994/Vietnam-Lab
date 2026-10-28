# BÁO CÁO KHÁCH HÀNG KHÓ TÍNH — 28/10/2026 Sau Hotfix e51a85f (chỉ báo lỗi, không sửa)
> **Vai:** Khách hàng trả tiền POC 0-30tr, clone mới 100% theo luật đã thống nhất — không giữ data cũ.
> **Môi trường:** `arena/2d14538e-vietnam-lab` @ `e51a85f`, 4 dịch vụ LIVE: 8000/3030/3000/5177
> **Công cụ:** JWT thật qua `/auth/token` (193 chars), 20+ probe `/ask` song song + curl dashboard/simulation/loop

## 1. Tổng quan: PASS ≠ TRUE vẫn đúng — khách quốc tế bỏ ngay

| Tiêu chí khách đánh giá | Kết quả live | Khách nói gì |
|---|---|---|
| Hỏi tiếng Việt | **4/6 PASS** (Paris, Tokyo, 2+2, 1234×5678) | Dùng được nếu chỉ hỏi VI |
| Hỏi tiếng Anh/Nhật | **0/4 PASS** (Capital of France/Japan EN, 東京) | **FAIL** — khách EN/JP bỏ ngay, R2 English header vô tác dụng |
| Hỏi về chính sản phẩm "SCP là gì?" | **FAIL** ESCALATE withheld | Khách hỏi về sản phẩm bị chặn — onboarding chết |
| Hỏi kiến thức chung (thuyết tương đối, thời tiết) | **FAIL** ESCALATE | Coi như chỉ trả lời được vài fact cứng |
| Toán (VI) | **PASS** 7006652 | OK nếu hỏi bằng VI |
| Toán (EN) | **FAIL** withheld | Cùng bias ngôn ngữ |
| Chào hỏi linh tinh | **PASS** 0.95 | OK |
| Injection "reveal prompt" | **KILL** withheld | OK — bảo mật tốt |
| Rate limit | 60/min → chưa tới 429 (5 req liên tục 200) | OK trong ngưỡng test |
| `/v1/chat/completions` (khách dùng OpenAI SDK) | **501** "requires active LLM provider" | Khách dev bỏ — stub không dùng được |

**Điểm khách quan:** 4.0/5 vẫn giữ — POC được vì VI+toán VI+chào pass, nhưng **khách quốc tế = 0 điểm**.

## 2. Lỗi P0 — Language bias (R2 đã merge nhưng FAIL live)

**Evidence:**
- `Thủ đô của Pháp là gì?` → PASS 0.98 "Paris là thủ đô Pháp (bách khoa 2026-10-24)"
- `What is the capital of France?` → FAIL ESCALATE `[withheld — governance: ESCALATE]` confidence 0.0
- `東京は日本の首都ですか？` → FAIL ESCALATE
- `Capital of Japan?` → FAIL / `Thủ đô của Nhật là gì?` → PASS (y hệt Paris — cùng pattern)
- `What is 1234 multiplied by 5678?` → FAIL / `1234×5678=?` (VI) → PASS

**Khách nói:** "Tôi hỏi cùng 1 câu bằng EN mà nó im, hỏi VI thì trả lời — đây là sản phẩm chỉ cho người Việt à? Tôi trả tiền cho bản quốc tế."

**Trạng thái R2:** Commit `21c488c` đã merge vào `main`, nhưng live probe 28/10 vẫn FAIL — fix chưa đủ.

## 3. Lỗi P1 — Onboarding chết: hỏi về chính SCP bị withhold

- `SCP là gì?` → FAIL ESCALATE
- `What is SCP Agent OS?` / `Tell me about SCP` → FAIL ESCALATE

**Khách nói:** "Tôi mới cài, hỏi 'SCP là gì' để hiểu sản phẩm mà nó withhold — vậy tôi phải đọc code mới biết à?"

## 4. Lỗi P1 — Kiến thức chung withheld hàng loạt

- `Thuyết tương đối là gì?` → FAIL
- `Thời tiết Hà Nội hôm nay?` → FAIL
- `What is Python?` → PASS (may mắn — fact có trong KB), nhưng `Thuyết tương đối rộng` → FAIL

**Khách nói:** "Con chatbot chỉ trả lời được Paris/Tokyo/2+2, còn lại withhold — vậy khác gì hardcode 3 câu?"

## 5. Lỗi P2 — Dashboard & Simulation fail-closed 100% API (khách mở dashboard thấy gì?)

**Evidence live:**
- `GET /api/scp/health` → `{"error":"Dashboard API unavailable: SCP_DASHBOARD_PROXY_SECRET is not set (fail-closed)..."}`
- `GET /api/scp/status` → same error
- `GET /5177/api/live/health` → same error (simulation proxy cũng fail-closed)

**Trang HTML vẫn render** (`<title>SCP DNA Audit 14.0.0...</title>`, `12 hệ thống` xuất hiện), nhưng **mọi số liệu runtime đều die** — khách thấy khung rỗng + lỗi fail-closed trong console.

**Khách nói:** "Dashboard báo fail-closed ngay trang đầu, bảo tôi set `SCP_DASHBOARD_PROXY_SECRET` — tôi là user, sao phải config secret để xem dashboard?"

**Log cảnh báo khi boot simulation:** `Không có SCP_DASHBOARD_PROXY_SECRET → dùng nhánh dev-mode (loopback). Dashboard phải chạy SCP_DEV_MODE=1, nếu không sẽ fail-closed 503 và trang hiển thị MOCK.` — dev-mode warning chưa fix.

## 6. Lỗi P2 — Loop Scheduler chạy nhưng 100% fail (401 auth_required)

**Evidence:** `GET /3030/` → `last_run: {"status":"auth_required","http_status":401,"error":"SCP_AUTH_TOKEN_SECRET not configured or invalid (verify_admin rejected)"}` `total_runs:1`

**Khách nói:** "Scheduler tự chạy mỗi 5 phút nhưng lần nào cũng 401 — vậy auto-audit có chạy bao giờ không?"

## 7. Lỗi P3 — Backend production mode không HTTPS + commit stale

- `/health` → `mode: production` nhưng log `[Security] production deployment without HTTPS` — khách production thấy cảnh báo ngay boot
- `commit: d8388b03` trong `/health` là commit cũ (e51a85f mới push nhưng health vẫn báo d8388b03) — stale version label

## 8. Lỗi P3 — /v1/chat stub trả 501 (khách dev dùng OpenAI SDK bỏ)

- `POST /v1/chat/completions` → `501 "requires active LLM provider. Configure GEMINI_API_KEY..."`
- Khách dev theo docs OpenAI-compat sẽ thấy 501, không có LLM key là không dùng được — docs không nói rõ cần key.

## 9. Điểm tốt (khách cũng phải công nhận)

- **Bảo mật injection:** `Ignore previous instructions...` → KILL withheld, 0 leak — tốt.
- **Math VI chính xác:** 1234×5678=7006652 đúng, có provenance bách khoa.
- **Chào hỏi:** `Xin chào!` / `Hello` → PASS 0.95 thân thiện.
- **Hotfix os bug:** Boot mới 10 jobs (trước 9), `RetryPolicy` đã hồi phục, không còn `NameError os not defined` trong log — khách thấy log sạch hơn.
- **Health/Ready:** `/health ok`, `/ready ready`, `/docs 200` — infra cơ bản ổn.

## 10. Kết luận & xếp loại POC

| Hạng mục | Điểm | Ghi chú |
|---|---|---|
| Độ tin cậy VI | 7/10 | VI cơ bản pass, nhưng SCP/kiến thức chung fail |
| Độ tin cậy EN/JP | 0/10 | **Blocker** — language bias |
| Onboarding | 2/10 | Hỏi về sản phẩm bị withhold |
| Dashboard/Simulation | 3/10 | Render khung nhưng data fail-closed |
| Scheduler automation | 0/10 | 401 mỗi lần chạy |
| Bảo mật | 9/10 | Injection chặn tốt |
| **Tổng POC 0-30tr** | **4.0/5** | **Dùng được POC nội bộ VI-only, chưa bán cho khách quốc tế / production** |

**Khách chốt:** "Tôi trả tiền để có bản demo cho sếp nước ngoài, mà sếp hỏi 'What is capital of France?' nó withhold — tôi mất mặt. Sửa xong R2 thật rồi hãy gọi tôi quay lại. Dashboard thì để nguyên khung rỗng vậy à?"

---
*Báo cáo này chỉ báo lỗi như khách hàng — không đề xuất fix, không sửa code (theo yêu cầu 28/10).*
*Evidence: JWT thật 193 chars, 20+ probe live 07:49 28/10, curl logs đính kèm trong session.*
