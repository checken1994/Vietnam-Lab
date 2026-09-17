# SCP Gateway — How to reach SCP through Caddy

This document explains how SCP (Python FastAPI on `127.0.0.1:8000`) is
exposed through the Caddy gateway on `:81`, alongside the Next.js
dashboard on `127.0.0.1:3000`.

> **Why port `:81`?** The sandbox dev environment runs Caddy on `:81`
> because port `:80`/`:443` would require root + a real domain + TLS
> cert. In production the same `Caddyfile` works unchanged on `:443`
> once you swap `:81` → your domain.

---

## TL;DR — gateway URLs

| Service                | Gateway URL                                              | Backend           |
| ---------------------- | -------------------------------------------------------- | ----------------- |
| Next.js dashboard      | `https://<gateway>/`                                     | `localhost:3000`  |
| SCP root (route list)  | `https://<gateway>/?XTransformPort=8000`                 | `localhost:8000`  |
| SCP health             | `https://<gateway>/health?XTransformPort=8000`           | `localhost:8000`  |
| SCP HTML dashboard     | `https://<gateway>/dashboard?XTransformPort=8000`        | `localhost:8000`  |
| SCP chat (`POST /ask`) | `https://<gateway>/ask?XTransformPort=8000`              | `localhost:8000`  |

**Rule of thumb:** append `?XTransformPort=8000` to ANY SCP path to
route it through the gateway to SCP. Without that query parameter the
request goes to the Next.js dashboard (port 3000) instead.

> The orchestrator/system-prompt rule is:
> *"DO NOT write port in the api request url, only XTransformPort"*.
> The Caddyfile follows this rule strictly — no `:8000` ever appears in
> a public URL.

---

## Caddyfile (current)

```caddyfile
:81 {
    @transform_port_query {
        query XTransformPort=*
    }

    handle @transform_port_query {
        reverse_proxy localhost:{query.XTransformPort} {
            header_up Host {host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }

    handle {
        reverse_proxy localhost:3000 {
            header_up Host {host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }
}
```

### How it works

1. The `@transform_port_query` matcher fires whenever the request has a
   `XTransformPort=<n>` query parameter.
2. The first `handle` block reverse-proxies to
   `localhost:{query.XTransformPort}` — i.e. the value of the query
   parameter becomes the backend port. So `?XTransformPort=8000` →
   `localhost:8000` (SCP), `?XTransformPort=3000` → `localhost:3000`
   (Next.js, same as default).
3. The fallback `handle` block reverse-proxies everything else (no
   `XTransformPort` query) to `localhost:3000` (Next.js dashboard).
4. Both blocks set `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`,
   `X-Real-IP` so the backend sees the original client's IP + scheme.

### Why not add a `:8000` block?

The task description suggested adding a `:8000` reverse_proxy handle
block. **We deliberately did NOT** — that would publish SCP on a
separate public port and break the orchestrator's rule ("DO NOT write
port in the api request url, only XTransformPort"). The
`?XTransformPort=8000` passthrough is already correct and is the
intended SCP access pattern.

---

## Start SCP

```bash
# From $SCP_ROOT
python3 -m scp            # 127.0.0.1:8000 (default)
python3 -m scp 8080       # 127.0.0.1:8080 (custom port — use ?XTransformPort=8080 in URLs)
SCP_PORT=9000 python3 -m scp   # 127.0.0.1:9000
```

Verify SCP is up:
```bash
curl http://127.0.0.1:8000/health
# or via gateway:
curl 'https://<gateway>/health?XTransformPort=8000'
```

---

## SCP route list (73 total — verified via `from scp.api_server import app; len(app.routes)`)

> **R10 Task 14.B clarification:** 12 of the 83 documented routes below
> are **dead code** — defined in `audit_routes.py`, `threat_routes.py`,
> `prediction_routes.py`, and `stream_routes.py` but NOT registered in
> `api_server.py` (no `app.include_router(...)` call). This is
> **intentional, not a bug** — see `scp/api/routes/README.md` for the
> full dead-route map + how to activate any of them in one line of
> `include_router`. The dashboard marks dead routes with a red "dead"
> badge in the route table (filterable via the "Dead code" filter).

### Top-level (defined directly in `scp/api_server.py`)

| Method | Path          | Description                                  | Gateway URL                                         |
| ------ | ------------- | -------------------------------------------- | --------------------------------------------------- |
| POST   | `/ask`        | Main: question → V98 pipeline → verdict      | `/ask?XTransformPort=8000`                          |
| GET    | `/health`     | Health check (liveness)                      | `/health?XTransformPort=8000`                       |
| GET    | `/dashboard`  | SCP HTML dashboard (server-rendered)         | `/dashboard?XTransformPort=8000`                    |
| GET    | `/`           | Root — route listing + version               | `/?XTransformPort=8000`                             |

### V1 (OpenAI-compatible — for PyRIT/garak)

| Method | Path                    | Description              |
| ------ | ----------------------- | ------------------------ |
| POST   | `/v1/chat/completions`  | OpenAI chat completions  |
| GET    | `/v1/models`            | OpenAI models list       |

### V98 (security pipeline)

| Method | Path                            | Description                          |
| ------ | ------------------------------- | ------------------------------------ |
| POST   | `/v98/analyze-session`          | Rogue AI detection on a session     |
| POST   | `/v98/run-simulation`           | Trigger threat simulation           |
| POST   | `/v98/run-intel-crawl`          | Trigger threat intel crawl          |
| GET    | `/v98/status`                   | All V98 module status                |
| GET    | `/v98/counter/stats`            | Counter response stats              |
| GET    | `/v98/canary/triggers`          | Canary token triggers               |
| GET    | `/v98/error-store/stats`        | ErrorStore stats                    |
| GET    | `/v98/attack-memory/stats`      | Attack memory stats                 |

### V100 (admin)

| Method | Path                          | Description                |
| ------ | ----------------------------- | -------------------------- |
| GET    | `/v100/status`                | V100 admin status          |
| POST   | `/v100/crawl`                 | Trigger crawl              |
| GET    | `/v100/antibodies/stats`      | Antibody stats             |
| POST   | `/v100/antibodies/check`      | Run antibody check         |
| GET    | `/v100/knowledge/stats`       | Knowledge stats            |
| GET    | `/v100/knowledge/search`      | Knowledge search           |
| GET    | `/v100/h8/stats`              | H8 stats                   |
| GET    | `/v100/h8/bypasses`           | H8 bypasses                |
| GET    | `/v100/h8/analyses`           | H8 analyses                |

### V102 / V103 (orchestrator + storage + attacks)

| Method | Path                                   | Description                          |
| ------ | -------------------------------------- | ------------------------------------ |
| GET    | `/v102/orchestrator/stats`             | Orchestrator stats                  |
| GET    | `/v102/notifications/recent`           | Recent notifications                |
| GET    | `/v103/storage/stats`                  | Storage manager stats               |
| POST   | `/v103/storage/maintain`               | Trigger storage maintenance         |
| POST   | `/v103/gcg/test`                       | Run GCG attack test                 |
| GET    | `/v103/attacks/crawled`                | Crawled attacks                     |
| POST   | `/v103/attacks/crawl`                  | Trigger attack crawl                |
| GET    | `/v103/status`                         | V103 status                         |

### V104 (multi-turn + image + voice + learning)

| Method | Path                                | Description                          |
| ------ | ----------------------------------- | ------------------------------------ |
| GET    | `/v104/status`                      | V104 status                          |
| POST   | `/v104/multi-turn/check`            | Multi-turn jailbreak check           |
| POST   | `/v104/image/check`                 | Image jailbreak check                |
| POST   | `/v104/voice/check`                 | Voice jailbreak check                |
| GET    | `/v104/cross-language/transfer`     | Cross-language transfer stats        |
| GET    | `/v104/explain`                     | Get simple explainer output          |
| POST   | `/v104/fact-check`                  | Streaming fact check                 |
| POST   | `/v104/learn/ollama`                | Trigger Ollama learning cycle        |
| POST   | `/v104/learn/local`                 | Trigger local learning cycle         |
| POST   | `/v104/learn/news`                  | Trigger news learning cycle          |
| POST   | `/v104/learn/all`                   | Trigger ALL learning sources         |
| GET    | `/v104/learn/status`                | Learning engine status               |
| GET    | `/v104/learn/matrix`                | Ollama learning matrix               |
| POST   | `/v104/learn/ollama-matrix`         | Trigger Ollama matrix learning       |
| POST   | `/v104/learn/fast`                  | Trigger fast learning cycle          |
| GET    | `/v104/learn/fast/status`           | Fast learning status                 |
| GET    | `/v104/learn/fast/benchmark`        | Fast learning benchmark              |

### V105 (autofix engine control panel — R8 + R9 NEW)

| Method | Path                                                    | Description                                  |
| ------ | ------------------------------------------------------- | -------------------------------------------- |
| GET    | `/v105/autofix/permissions`                             | List pending permission requests             |
| POST   | `/v105/autofix/permissions/{request_id}/approve`        | Approve a fix                                |
| POST   | `/v105/autofix/permissions/{request_id}/deny`           | Deny a fix                                   |
| POST   | `/v105/autofix/attack-mode/{enabled}`                   | Toggle attack-mode                           |
| GET    | `/v105/autofix/stats`                                   | Autofix engine stats                         |
| POST   | `/v105/autofix/run-audit`                               | Trigger deep audit (R9-2 FIXED: now async)   |
| GET    | `/v105/autofix/monitor`                                 | Autofix monitor dashboard                    |
| POST   | `/v105/autofix/cleanup-cache`                           | Cleanup LLM fix cache                        |
| POST   | `/v105/autofix/tier3-auto/{enabled}`                    | Toggle tier-3 auto-approve                   |
| POST   | `/v105/autofix/rollback/{rollback_token}`               | Rollback a fix (IMP-17 auto_rollback)        |
| GET    | `/v105/audit/stats`                                     | Audit stats — ✅ LIVE (requires verify_admin; wired in api_server.py)                     |
| GET    | `/v105/audit/findings`                                  | Audit findings — ✅ LIVE (requires verify_admin; wired in api_server.py)                  |
| GET    | `/v105/threats/ai-scan/stats`                           | AI scan stats — ✅ LIVE (requires verify_admin; wired in api_server.py)                  |
| GET    | `/v105/threats/ai-scan/findings`                        | AI scan findings — ✅ LIVE (requires verify_admin; wired in api_server.py)               |
| GET    | `/v105/threats/harm/stats`                              | Harm stats — ✅ LIVE (requires verify_admin; wired in api_server.py)                     |
| GET    | `/v105/threats/harm/incidents`                          | Harm incidents — ✅ LIVE (requires verify_admin; wired in api_server.py)                 |
| GET    | `/v105/predictions/pending`                             | Pending predictions — ✅ LIVE (requires verify_admin; wired in api_server.py)            |
| GET    | `/v105/predictions/all`                                 | All predictions — ✅ LIVE (requires verify_admin; wired in api_server.py)                |
| POST   | `/v105/predictions/run-cycle`                           | Trigger prediction cycle — ✅ LIVE (requires verify_admin; wired in api_server.py)       |
| POST   | `/v105/predictions/verify`                              | Verify a prediction — ✅ LIVE (requires verify_admin; wired in api_server.py)            |
| GET    | `/v105/predictions/stats`                               | Prediction stats — ✅ LIVE (requires verify_admin; wired in api_server.py)               |
| POST   | `/v105/ask/stream`                                      | Streaming chat (SSE) — ⚠️ DEAD (stream_routes.py not registered)                        |

### Import + chat + webhook

| Method | Path                          | Description                       |
| ------ | ----------------------------- | --------------------------------- |
| POST   | `/import/jsonl`               | Import JSONL questions            |
| POST   | `/import/excel`               | Import Excel questions            |
| POST   | `/import/batch`               | Import batch                      |
| GET    | `/chat/sessions`              | List chat sessions                |
| GET    | `/chat/{session_id}/history`  | Get chat session history          |
| POST   | `/api/analyze`                | Webhook: analyze a prompt         |
| POST   | `/api/register`               | Webhook: register external system |
| GET    | `/api/threats`                | Webhook: list threats             |
| GET    | `/api/alerts`                 | Webhook: list alerts              |
| GET    | `/api/systems`                | Webhook: list registered systems  |

### Built-in FastAPI

| Method | Path           | Description                          |
| ------ | -------------- | ------------------------------------ |
| GET    | `/openapi.json`| OpenAPI 3 schema (auto-generated)    |
| GET    | `/docs`        | Swagger UI (auto-generated)          |
| GET    | `/redoc`       | ReDoc UI (auto-generated)            |

---

## Gateway URL pattern

For any SCP route above, the public gateway URL is:

```
https://<gateway-host>:<gateway-port>/<scp-path>?XTransformPort=8000
```

- `<scp-path>` is the path column from the tables above (e.g. `/v105/autofix/stats`).
- `XTransformPort=8000` MUST be appended (or merged with existing query
  params via `&`). Without it, the request falls through to Next.js.
- The Next.js dashboard calls SCP through its own Next.js API routes
  (`/api/scp/health`, `/api/scp/routes`, `/api/scp/status`) which
  internally `fetch('http://127.0.0.1:8000/...')` server-side. End
  users never need to add `XTransformPort` themselves — the dashboard
  abstracts it.

---

## Troubleshooting

### SCP offline?

```bash
# Check process:
ps aux | grep -E 'python.*scp' | grep -v grep
# Check port:
curl -s http://127.0.0.1:8000/health || echo "SCP not responding"
# Start SCP:
cd $SCP_ROOT && python3 -m scp
```

The Next.js dashboard `/api/scp/health` route will return
`{ "scp": "offline", "hint": "Run: cd $SCP_ROOT && python3 -m scp 8000" }`
with HTTP 503 when SCP is down — this is the fail-open behavior.

### Gateway returns Next.js page when expecting SCP?

You forgot `?XTransformPort=8000` in the URL. Add it.

### Auth failures on admin endpoints?

Most `/v100/*`, `/v102/*`, `/v103/*`, `/v104/*`, `/v105/*` routes
require admin auth (`Depends(verify_admin)`). Set either:
- `SCP_AUTH_PASSWORD=...` (HTTP Basic), or
- `SCP_AUTH_TOKEN_SECRET=...` (Bearer token)

…in `.env` and restart SCP.

---

## See also

- `$SCP_ROOT/scp/.env.example` — every env var SCP reads
- `$SCP_ROOT/scp/__main__.py` — SCP entry point
- `$SCP_ROOT/scp/api_server.py` — FastAPI app + route definitions
- `$SCP_ROOT/Caddyfile` — the gateway config (24 lines)
