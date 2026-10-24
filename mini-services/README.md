# SCP Mini-Services

Node.js-based sidecar services that extend SCP Python with capabilities that
don't belong in the FastAPI process (long-running LLM calls, cron loops,
stateful bridges). They run alongside SCP (port 8000) and the Next.js
dashboard (port 3000) and talk to each other over `127.0.0.1`.

## Services

| Service          | Port  | Purpose                                                              | Status      |
| ---------------- | ----- | -------------------------------------------------------------------- | ----------- |
| `llm-bridge`     | 11434 | Bridges SCP's LLM gateway to a local Ollama-compatible HTTP server (so SCP can call LLMs without each call going through the public OpenRouter / OpenAI APIs). | R10 NEW     |
| `loop-scheduler` | 3030  | Closed-loop scheduler. Every N seconds, calls SCP's `POST /v105/autofix/run-audit` to trigger a deep audit cycle, logs the result, exposes a status dashboard. | R10 NEW     |
| `brain-console`  | 8090  | **Read-only live projection of the entire SCP brain.** Task Kernel state machine, event journal (including the global `control` anchor), every SQLite database with physical integrity, audit-ledger effectiveness, service topology, skill pack, and the latest runtime audit verdict. | R11 NEW     |

## Start both

```bash
# Terminal 1 — LLM bridge
cd "$SCP_ROOT/mini-services/llm-bridge"
npm run dev

# Terminal 2 — Loop scheduler
cd "$SCP_ROOT/mini-services/loop-scheduler"
npm run dev

# Terminal 3 — Brain console (read-only brain projection)
cd "$SCP_ROOT/mini-services/brain-console"
SCP_DATA_DIR="$SCP_ROOT/data" npm run dev

# Terminal 4 — SCP
cd "$SCP_ROOT"
python3 -m scp 8000

# Terminal 5 — Next.js dashboard
cd "$SCP_ROOT/dashboard"
npm run dev
```

Both mini-services are **fail-open** — if SCP is offline, they log the
miss and keep ticking. They never crash the SCP process or the dashboard.

## Gateway XTransformPort pattern

If a Caddy gateway is deployed, its config should target the canonical
loopback backend. The repository does not ship a Caddyfile. A gateway may
route traffic based on the `?XTransformPort=<n>` query parameter:

- `?XTransformPort=8000` → SCP Python (port 8000)
- `?XTransformPort=3030` → loop-scheduler (port 3030)
- `?XTransformPort=8090` → brain-console (port 8090)
- `?XTransformPort=11434` → llm-bridge (port 11434)
- (no param) → Next.js dashboard (port 3000)

This follows the orchestrator rule: **"DO NOT write port in the api
request url, only XTransformPort"**. No backend port is exposed publicly; internal calls use loopback `127.0.0.1`
and the SCP backend is `:8000`.

```bash
# Reach the loop scheduler through the gateway:
curl 'https://<gateway-host>:<gateway-port>/?XTransformPort=3030'
```

The Next.js dashboard does NOT use the gateway — it calls the mini-services
directly via `127.0.0.1:3030` (server-side fetch in
`src/app/api/scp/loop/route.ts`). End users only see `/api/scp/loop` on
the dashboard.

## Why mini-services (not part of SCP Python)?

1. **Separate failure domains.** A crash in the LLM bridge or scheduler
   must NOT take down SCP's FastAPI server (which serves /ask, /health,
   the WebSocket). Process isolation gives us that for free.
2. **Different runtime.** Node.js (TypeScript) is faster to iterate on for
   stateful orchestrators + HTTP clients than CPython. SCP's runtime
   (judge / SLMs / streaming) is already CPU-bound on CPython — we don't
   want to add I/O-bound cron work to its event loop.
3. **Independent lifecycle.** Restarting the scheduler shouldn't restart
   SCP (which has a 90-second cold start). Restarting SCP shouldn't drop
   in-flight LLM bridge requests.

## See also

- `llm-bridge/README.md` — LLM bridge details
- `loop-scheduler/README.md` — loop scheduler details
- `brain-console/README.md` — read-only brain projection details
- `scp/GATEWAY.md` — gateway URL pattern (XTransformPort)
- `dashboard/src/app/api/scp/loop/route.ts` — dashboard proxy to loop-scheduler
