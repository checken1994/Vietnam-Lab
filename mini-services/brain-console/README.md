# scp-brain-console

Live, **read-only** projection of the entire brain of a running SCP deployment.

The brain of SCP is distributed across dozens of stores and processes. The
dashboard reads a handful of endpoints, the audit tools read other stores,
the mini-services read others again — so no single surface shows what is
actually happening. This console reads all of them at once, over one
boundary, and refuses to call anything healthy that it could not observe.

## Why it exists

Three concrete gaps in the existing audit surface, each proven against a
live deployment:

1. `/health` returns a static `{"status": "ok"}` string that is independent
   of the physical state of the storage underneath it. On a deployment where
   two SQLite databases were corrupt, `/health` still answered `ok`.
2. `TaskKernel.verify_journal(task_id)` validates one task's hash chain and
   consults the global `control` anchor only when that task has zero
   events. The global anchor is therefore never cross-checked against the
   physical event table.
3. The self-audit loop reports `status: ok` on every run while emitting zero
   findings. Liveness was reported; effectiveness was not.

The console projects all three honestly, side by side.

## Design contract

| Property        | Guarantee                                                             |
| --------------- | --------------------------------------------------------------------- |
| Read-only       | Every database is opened with `node:sqlite` `readOnly: true`.         |
| Zero deps       | `node:http`, `node:sqlite`, `node:fs`. No npm install, no build step. |
| Loopback        | Binds `127.0.0.1` by default (DNA #6).                               |
| No secrets      | Only secret *presence* is projected; values never enter a response.  |
| Honest surfaces | Anything unobservable is reported as such, never as "healthy".       |

> Deviation from the sibling services: this one ships JavaScript only, with
> `index.d.ts` describing the typed surface. A `.ts` twin that is never
> executed would be a drift hazard for the stale-code tripwire.

## Endpoints

| Method | Path            | Purpose                                                              |
| ------ | --------------- | -------------------------------------------------------------------- |
| GET    | `/`             | Live HTML brain dashboard (self-contained, no CDN, no build).        |
| GET    | `/healthz`      | `{"status":"ok","service":"scp-brain-console"}`                       |
| GET    | `/ready`        | Readiness of the console itself (`/readiness` is an alias).          |
| GET    | `/api/brain`    | The complete projection: every section below in one document.        |
| GET    | `/api/kernel`   | Task Kernel state occupancy, live tasks, leases, checkpoints.        |
| GET    | `/api/journal`  | Event journal: physical count vs `control` anchor, chain integrity. |
| GET    | `/api/storage`  | Every SQLite database: integrity, journal mode, table and row census.|
| GET    | `/api/services` | Service topology: reachability, status, latency.                    |
| GET    | `/api/loops`    | Audit-loop and deep-audit ledger effectiveness.                      |
| GET    | `/api/skills`   | The normative SCP skill pack, inventoried from disk.                 |
| GET    | `/api/audit`    | The latest runtime audit verdict written by the Python authority.    |
| GET    | `/api/stream`   | Server-Sent Events stream of live snapshots.                         |

## Configuration

| Variable                      | Default                        | Purpose                                   |
| ----------------------------- | ------------------------------ | ----------------------------------------- |
| `SCP_BRAIN_CONSOLE_PORT`      | `8090`                         | Listen port.                              |
| `SCP_BRAIN_CONSOLE_HOST`      | `127.0.0.1`                    | Bind address (loopback by default).       |
| `SCP_DATA_DIR`                | `<repo>/data`                  | Live data directory to project.           |
| `SCP_BRAIN_SKILLS_DIR`        | `<repo>/.agents/skills`        | Skill pack to inventory.                   |
| `SCP_BASE_URL`                | `http://127.0.0.1:8000`        | Running SCP backend.                      |
| `LOOP_SCHEDULER_URL`          | `http://127.0.0.1:3030`        | Running loop scheduler.                   |
| `AGENT_DESKTOP_URL`           | `http://127.0.0.1:6080`        | Running virtual desktop.                  |
| `SCP_BRAIN_ADMIN_TOKEN`       | *(unset)*                      | Optional bearer token for protected views.|
| `SCP_BRAIN_POLL_MS`           | `5000`                         | Browser refresh / SSE interval.           |
| `SCP_BRAIN_HTTP_TIMEOUT_MS`   | `5000`                         | Per-probe timeout.                        |
| `SCP_ENV_FILE`                | *(unset)*                      | Explicit env file (never auto-loaded).    |

Without an admin token the protected backend surfaces are observed refusing
the request. That is reported as `protected`, not as healthy — and not as
broken either.

## Run it

```bash
cd "$SCP_ROOT/mini-services/brain-console"

# Against the live deployment
SCP_DATA_DIR=/var/lib/scp/data \
SCP_BASE_URL=http://backend:8000 \
LOOP_SCHEDULER_URL=http://loop-scheduler:3030 \
npm run dev
```

Then open <http://127.0.0.1:8090>.

## Pair it with the runtime audit authority

The console **projects**. It does not decide. The verdict comes from the
Python runtime audit, which owns the capability secret and therefore the
HMAC half of the journal verification:

```bash
python tools/run_runtime_audit.py --data-dir /var/lib/scp/data
```

That verdict is written to `<data_dir>/runtime_audit.sqlite3`, which this
console reads back and displays under **Runtime audit verdict**, together
with the run history. The two halves are deliberately separate: the
authority decides, the projection shows.

## See also

- `scp/runtime_audit/` — the runtime reality audit (Python authority)
- `tools/run_runtime_audit.py` — CLI entrypoint, emits evidence JSON
- `mini-services/README.md` — the mini-service registry
