# SCP Loop Scheduler (mini-service)

> Closes the SCP self-healing loop. SCP has all the components of a closed
> loop (scan → detect → fix → verify) but no scheduler to trigger them
> periodically — this mini-service IS the scheduler.

## What it does

Every `LOOP_INTERVAL_SEC` seconds (default 300 = 5 min), this service:

1. **Probes SCP liveness** — `GET http://127.0.0.1:8000/health`
2. **Triggers a deep audit** — `POST http://127.0.0.1:8000/v105/autofix/run-audit`
   (which AST-scans `scp/` for bugs, auto-fixes Tier 1/2, requests
   permission for Tier 3, writes per-bug results to
   `data/deep_audit_results.jsonl`)
3. **Logs the result** to `$SCP_ROOT/data/loop_runs.jsonl`:
   ```json
   {"ts":"2026-08-08T21:30:00Z","scp_online":true,"status":"ok",
    "findings_count":3,"fixes_applied":2,"permission_requested":1,
    "duration_ms":4521,"triggered_by":"cron"}
   ```
4. **Exposes a dashboard** on port **3030** so the Next.js control panel
   can show live loop status.

## Fail-open policy (DNA SCP #7 safe)

The loop **never crashes**. If any of these happen, the scheduler logs
the error to stderr + `loop_runs.jsonl` and keeps ticking:

- SCP offline → status `"scp_offline"`, continues probing every interval
- `loop_runs.jsonl` unwritable → log to stderr, continue
- SCP audit endpoint errors (500/timeout) → status `"error"`, continue
- Auth not configured → status `"auth_required"` (SCP returns 503),
  continue

When SCP comes back online, the next tick succeeds and the loop resumes
as if nothing happened.

## Endpoints (port 3030)

| Method | Path        | Description                                                      |
| ------ | ----------- | ---------------------------------------------------------------- |
| GET    | `/`         | Loop status JSON — `{running, paused, last_run, next_run, interval_sec, total_runs, scp_online, recent_runs: [...last10]}` |
| GET    | `/healthz`  | Liveness probe — `{"status":"ok"}`                               |
| POST   | `/trigger`  | Manually trigger a run now (returns the run result)              |
| POST   | `/pause`    | Pause the loop (clears the next-tick timer)                      |
| POST   | `/resume`   | Resume the loop after a pause                                    |

All responses enforce a restricted CORS allowlist (defaulting to
`http://127.0.0.1:3000` / `http://localhost:3000` via `DASHBOARD_ORIGIN`)
so only trusted dashboard origins can fetch directly. The Next.js
dashboard proxies through `/api/scp/loop` (see
`src/app/api/scp/loop/route.ts`) so end users never need to hit
port 3030 directly.

## Run

```bash
cd $SCP_ROOT/mini-services/loop-scheduler

# Dev
npm run dev

# Production
node index.js
```

No additional install needed — only standard Node.js built-ins.

## Configuration (env vars — all optional)

| Env var                  | Default                                            | Meaning                                                       |
| ------------------------ | -------------------------------------------------- | ------------------------------------------------------------- |
| `LOOP_INTERVAL_SEC`      | `300` (5 min)                                      | Seconds between automatic runs                                |
| `SCP_BASE_URL`           | `http://127.0.0.1:8000`                            | SCP Python base URL                                           |
| `SCP_AUTH_TOKEN_SECRET`  | (none)                                             | Bearer token for SCP admin endpoints (sent as `Authorization`) |
| `SCP_AUTH_PASSWORD`      | (none)                                             | Alt auth (used as Bearer if no token secret set)              |
| `LOOP_SCHEDULER_PORT`    | `3030`                                             | Port to listen on                                             |
| `LOOP_LOG_PATH`          | `$SCP_ROOT/data/loop_runs.jsonl`      | Where to write the JSONL run log                              |

### Auth note

SCP's `/v105/autofix/run-audit` endpoint requires admin auth
(`Depends(verify_admin)` in `scp/api/_shared.py`). Set
`SCP_AUTH_TOKEN_SECRET=...` (or `SCP_AUTH_PASSWORD=...`) before starting
**both** SCP and this scheduler. If neither is set, SCP returns
`503 "Auth not configured"` and the scheduler logs `status:"auth_required"`
— fail-open, the loop keeps ticking.

## Verify

```bash
# Start the scheduler
cd $SCP_ROOT/mini-services/loop-scheduler
node index.js &
sleep 2

# Status
curl -s http://127.0.0.1:3030/ | head -c 500

# Manual trigger (will succeed if SCP is running + auth configured,
# otherwise logs scp_offline/auth_required and continues)
curl -s -X POST http://127.0.0.1:3030/trigger | head -c 500

# Check the log
cat $SCP_ROOT/data/loop_runs.jsonl

# Pause / resume
curl -s -X POST http://127.0.0.1:3030/pause
curl -s -X POST http://127.0.0.1:3030/resume
```

## How it integrates with SCP

```
┌───────────────────┐   every 5 min   ┌────────────────────┐
│  loop-scheduler   │ ──────────────▶ │   SCP /v105/       │
│   (port 3030)     │                 │   autofix/run-audit│
│                   │ ◀────────────── │   (port 8000)      │
│   writes log to   │   audit result  │                    │
│   loop_runs.jsonl │                 │   writes per-bug   │
└───────────────────┘                 │   results to       │
        ▲                             │   deep_audit_      │
        │  GET /                      │   results.jsonl    │
        │  POST /trigger              └────────────────────┘
        │
┌───────────────────┐
│  Next.js dashboard│
│  /api/scp/loop    │
│  (port 3000)      │
└───────────────────┘
```

The scheduler is the **trigger** that turns SCP's manual closed loop into
an automatic one. Without it, an operator has to `curl -X POST
/v105/autofix/run-audit` themselves — which they will forget to do.
