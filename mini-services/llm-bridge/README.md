# SCP LLM Bridge — Ollama-compatible HTTP shim → OpenRouter / Groq

A tiny Bun HTTP service that **impersonates Ollama** on port `11434` and
forwards chat/generate requests to **OpenRouter** (primary, multi-key
round-robin) with **Groq** as declared fallback provider. This lets SCP's LLM
Gateway (`scp/llm_gateway/client.py`) work end-to-end **without changing any
SCP code or config** — its default `OLLAMA_HOST=http://127.0.0.1:11434` just
hits the bridge.

> Lịch sử: các phiên bản đầu dùng `z-ai-web-dev-sdk` (backend `internal-api.z.ai`,
> `model` bị bỏ qua). Từ [SCP-DNA-FIX R14-BRIDGE] bridge gọi OpenRouter trực
> tiếp (OpenAI-compatible), model routing qua `resolveModel()` + `TASK_MODEL_MAP`.
> README này mô tả hành vi hiện tại của code — không phải của SDK cũ.

## Why

- SCP's `OllamaProvider.chat()` POSTs to `http://127.0.0.1:11434/api/chat`
  (non-reasoning models: `qwen2.5:7b`, `llama3.2`) and `/api/generate`
  (reasoning model: `deepseek-r1:8b`).
- SCP's `LLMGatewayHealth` GETs `http://127.0.0.1:11434/api/tags` to check
  which models are available.
- Ollama is **not installed** in this environment; the bridge closes that gap
  by proxying to real cloud LLMs over the OpenAI-compatible protocol.

## Endpoints

| Method | Path                | Auth                  | Description                                            |
|--------|---------------------|-----------------------|--------------------------------------------------------|
| GET    | `/`                 | —                     | Health / info HTML page                                |
| GET    | `/health`           | —                     | Same info as `/`                                       |
| GET    | `/api/tags`         | —                     | Fake Ollama model list (qwen2.5:7b, llama3.2, deepseek-r1:8b) |
| GET    | `/api/version`      | —                     | Fake Ollama version (honest `bridge` suffix)           |
| POST   | `/api/chat`         | Bearer (see below)    | `{model, messages, stream}` → OpenRouter/Groq          |
| POST   | `/api/generate`     | Bearer (see below)    | `{model, prompt, stream}` → OpenRouter/Groq            |
| GET    | `/api/cache/stats`  | Bearer (see below)    | Cache/metadata diagnostics (entry count, TTL, key index) |
| POST   | `/api/cache/clear`  | Bearer (see below)    | Clear the response cache                               |

**Auth**: `POST /api/chat`, `/api/generate` and **cả hai `/api/cache/*`
endpoints** ([AUDIT-FIX low-2] — cache endpoints từng mở không auth) require
`Authorization: Bearer <token>` where `<token>` matches `SHARED_SECRET` or
`BEARER_TOKEN` (env). Missing/wrong token → `401 {"error":"Unauthorized"}`.

**Recursion guard**: mọi outbound request do bridge phát ra (qua
`callProviderDirect`) mang header `X-LLM-Bridge-Internal: 1`
([AUDIT-FIX low-3]). Nếu provider URL vô tình trỏ lại chính bridge này,
`/api/chat` nhận request kèm header → trả `503 {"error":"self-call blocked
(recursion guard)"}` ngay tại hop đầu tiên — vòng lặp đệ quy không thể hình
thành.

**Egress guard** (`egress-guard.ts` + `egress-url.ts`): deny-by-default host
allowlist cho toàn bộ outbound fetch của bridge — chỉ `openrouter.ai` và
`api.groq.com` (exact match) cộng thêm các host khai báo qua
`LLM_EGRESS_ALLOWED_HOSTS`. Loopback/private/metadata host bị chặn trừ khi
được allowlist tường minh.

## Run

```bash
cd $SCP_ROOT/mini-services/llm-bridge
bun run dev           # bun --hot index.ts  (auto-restart on change)
# → [scp-llm-bridge] listening (host/port from config env)
```

Override port/host via env (new name first, legacy name still honored):

```bash
SCP_LLM_BRIDGE_PORT=11435 ZAI_BRIDGE_HOST=127.0.0.1 bun run dev
```

Env vars chính:

| Env | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENROUTER_API_KEY` / `_2` / `_3` | (bắt buộc cho /api/chat) | Key pool, round-robin trên 429 |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Base URL (qua egress allowlist) |
| `OPENROUTER_MODEL` | `openrouter/free` | Model mặc định |
| `OPENROUTER_MODEL_{AUTOFIX,JUDGE,WHY,LEARNING,FAST_LEARNING,CHAT}` | — | Per-task model map |
| `GROQ_BASE_URL` / `GROQ_API_KEY` / `GROQ_MODEL` | — | Fallback provider khi OpenRouter hết 429 |
| `SHARED_SECRET` / `BEARER_TOKEN` | — | Bearer token cho các endpoint có auth |
| `LLM_EGRESS_ALLOWED_HOSTS` | — | Host thêm cho egress allowlist (comma-separated) |
| `SCP_LLM_BRIDGE_PORT` / `ZAI_BRIDGE_PORT` | `11434` | Port |
| `ZAI_BRIDGE_HOST` | `127.0.0.1` | Host bind |
| `CORS_ALLOWED_ORIGINS` | dashboard localhost origins | Allowlist CORS (không dùng `*`) |

## Test

```bash
TOKEN=your-shared-secret

# 1. Tags (no auth)
curl -s http://127.0.0.1:11434/api/tags | jq '.models[].name'

# 2. Non-streaming chat (Bearer required)
curl -s -X POST http://127.0.0.1:11434/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"What is 2+2? Reply with just the number."}],"stream":false}' \
  | jq '.message.content'

# 3. Generate (single prompt — used by SCP for reasoning models)
curl -s -X POST http://127.0.0.1:11434/api/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-r1:8b","prompt":"What is the capital of France? Reply with just the name.","stream":false}' \
  | jq '.response'

# 4. Cache diagnostics (Bearer required)
curl -s http://127.0.0.1:11434/api/cache/stats -H "Authorization: Bearer $TOKEN" | jq .
curl -s -X POST http://127.0.0.1:11434/api/cache/clear -H "Authorization: Bearer $TOKEN" | jq .
```

## SCP integration

Start the bridge first, then start SCP — its default `OLLAMA_HOST` already
points at `127.0.0.1:11434`:

```bash
# Terminal 1: bridge
cd $SCP_ROOT/mini-services/llm-bridge && bun run dev

# Terminal 2: SCP
cd $SCP_ROOT && python3 -m scp 8000

# Test
curl -s -X POST http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of France?"}' | jq .
```

## Model handling

`resolveModel()` checks direct OpenRouter model IDs first, then
`TASK_MODEL_MAP` (per-task `OPENROUTER_MODEL_*` env vars), then falls back to
`OPENROUTER_MODEL`. SCP's legacy model names route per task:
`deepseek-r1:8b`→AUTOFIX, `qwen2.5:7b`→JUDGE, `llama3.2`→CHAT. `/api/tags`
advertises exactly the three models SCP's `TASK_MODEL_MAP` uses
(`deepseek-r1:8b`, `qwen2.5:7b`, `llama3.2`).

## Streaming

For `stream: true` requests we emit NDJSON with one content chunk + a final
`done: true` envelope (buffered single-chunk, made explicit via the
`X-Stream-Mode` response header). SCP's `OllamaProvider` only uses
`stream: false`, so this is sufficient for end-to-end `/ask` to work. Real
token-by-token streaming is a future enhancement.

## Rate-limit handling

OpenRouter (and Groq) enforce strict request rates. SCP's `/ask` fires
~20–50 parallel LLM calls (judge / why / learning / fast_learning / etc.) per
request, which would all 429 if fired simultaneously. The bridge has layers
of defense:

1. **Response cache** (`LLM_CACHE_TTL_MS`, default 5 min): identical questions
   return cached answers — 0 API calls. Inspect/clear via the authed
   `/api/cache/stats` + `/api/cache/clear` endpoints.
2. **Concurrency queue** (`ZAI_BRIDGE_CONCURRENCY=1` by default,
   `ZAI_BRIDGE_QUEUE_DEPTH=16`): all provider calls are serialized through a
   single slot so we don't burst-fire. If the queue is full, the bridge
   returns HTTP 502 immediately so SCP can fall back to its SLMs fast instead
   of stalling. Tune via env: `ZAI_BRIDGE_CONCURRENCY=2 ZAI_BRIDGE_QUEUE_DEPTH=32`.
3. **Exponential backoff retries on 429**: 500ms → 1000ms → 2000ms → 4000ms,
   with **multi-key round-robin** (`OPENROUTER_API_KEY`, `_2`, `_3`) — mỗi lần
   retry dùng key kế tiếp.
4. **Fallback provider**: OpenRouter exhausted → Groq (`GROQ_*` env) qua
   `callProviderDirect()`.

When all retries + fallbacks are exhausted, the bridge returns HTTP 502 with
an Ollama-style `{"error": "..."}` body. SCP's `OllamaProvider.chat()` catches
the exception and returns `(None, "ollama:<model>")`, which triggers SCP's
SLM-fallback path — so `/ask` still returns a valid answer even when the LLM
is fully rate-limited.

## Build

```bash
bun run build         # bun build index.ts --target=bun --outdir dist
```

## Honest limitations

- This is a **shim**, not a real Ollama. No model files, no real digests.
- The `total_duration`/`eval_count` etc. timing fields are stubbed to `0`.
- The streaming implementation sends the full content as a single chunk
  rather than token-by-token.
- Provider latency/status depends on the configured OpenRouter/Groq quota —
  khi key hết hạn hoặc model ID bị provider gỡ, `/api/chat` trả 502 và SCP
  rơi về SLM fallback.
