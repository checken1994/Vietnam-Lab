# SCP API Routes — Router Registration Map

This directory holds the FastAPI `APIRouter` modules that define SCP's
HTTP endpoints. **Not all of them are registered in `api_server.py`.**

## Router status (R10 — Task 14.B)

| File                  | Routes | Wired? | Notes                                                                  |
| --------------------- | ------ | ------ | ---------------------------------------------------------------------- |
| `admin_v98.py`        | 8      | ✅ live | V98 security pipeline. Imported + `include_router` in api_server.py:514. |
| `admin_v100.py`       | 10     | ✅ live | V100 admin. Imported + registered at api_server.py:515.                 |
| `import_routes.py`    | 3      | ✅ live | JSONL/Excel/batch import. Registered at api_server.py:538.              |
| `openai_compat.py`    | 2      | ✅ live | OpenAI-compatible /v1/* endpoints. Registered at api_server.py:536.     |
| `v102_v103_routes.py` | 9      | ✅ live | Orchestrator + storage + attacks. Registered at api_server.py:537.      |
| `v104_routes.py`      | 18     | ✅ live | Multi-turn + image + voice + learning. Registered at api_server.py:539. |
| `v105_routes.py`      | 10     | ✅ live | AutoFix engine control panel (R8+R9). Registered at api_server.py:540.  |
| `audit_routes.py`      | 2      | ✅ live | Audit stats + findings. Registered in api_server.py:436.              |
| `threat_routes.py`     | 4      | ✅ live | Threat feed & scan stats. Registered in api_server.py:435.            |
| `prediction_routes.py` | 5      | ✅ live | Prediction cycle & stats. Registered in api_server.py:437.            |
| `stream_routes.py`     | 1      | ✅ live | Streaming /ask (SSE). Registered in api_server.py:434.                |

**Live routes total: 83.** Grand total: 83
(all routes registered in `api_server.py` via `_route_enabled(...)` under
the default `full` profile; 56 active unit tests cover these routes).

## What "DEAD" means

A **dead route** is a FastAPI endpoint that is *defined* in a router
module but whose router is **not registered** in `api_server.py` via
`app.include_router(...)`. The code exists (and would work if wired),
but at runtime the path returns 404 — the FastAPI app simply doesn't
know about it.

This is **intentional, not a bug**. These routers were drafted as WIP
for earlier rounds (audit dashboard, threat feed, prediction engine,
streaming /ask) and left unwired because the underlying engines either
(a) hadn't been initialised at boot, (b) had security implications that
needed more review, or (c) were superseded by a different design.

Per Subagent A's SA-R9-6 audit + Subagent G's GATEWAY.md + Subagent J's
R10 Task 14.B, the decision is:

> **Do NOT delete dead routes.** Mark them clearly so future rounds
> (or future maintainers) can decide whether to wire or remove them.
> A dead route is safer than no route — at least the intent + the
> handler code is preserved in version control.

## The 12 routes (wired in `api_server.py:433-453`) — full list

### `audit_routes.py` (2 routes)

| Method | Path                    | Handler              | Notes                                              |
| ------ | ----------------------- | -------------------- | -------------------------------------------------- |
| GET    | `/v105/audit/stats`     | `audit_stats()`      | Returns `scp.core.audit_fetcher.get_audit_stats()`. |
| GET    | `/v105/audit/findings`  | `audit_findings()`   | Reads `data/audit_findings.jsonl` (file may not exist — handler returns `{"findings": [], "total": 0}`). |

### `threat_routes.py` (4 routes)

| Method | Path                              | Handler                | Notes                                                |
| ------ | --------------------------------- | ---------------------- | ---------------------------------------------------- |
| GET    | `/v105/threats/ai-scan/stats`     | `ai_threat_stats()`    | Returns `scp.core.ai_threat_scanner.get_threat_stats()`. |
| GET    | `/v105/threats/ai-scan/findings`  | `ai_threat_findings()` | Reads `data/ai_threats.jsonl`.                       |
| GET    | `/v105/threats/harm/stats`        | `harm_stats()`         | Returns `scp.core.harm_detector.get_harm_stats()`.   |
| GET    | `/v105/threats/harm/incidents`    | `harm_incidents()`     | Reads `data/ai_harm_incidents.jsonl`.                |

### `prediction_routes.py` (5 routes)

| Method | Path                                | Handler                  | Notes                                                            |
| ------ | ----------------------------------- | ------------------------ | ---------------------------------------------------------------- |
| POST   | `/v105/predictions/run-cycle`       | `run_prediction_cycle()` | Calls `_predictive_engine.run_cycle()`. **Requires `_predictive_engine` singleton in api_server.py to be initialised first** — verify before wiring. |
| GET    | `/v105/predictions/pending`         | `get_pending_predictions()` | `_predictive_engine.predictor.get_pending_predictions()`.        |
| GET    | `/v105/predictions/all`             | `get_all_predictions()`   | `_predictive_engine.predictor.get_all_predictions(limit)`.       |
| POST   | `/v105/predictions/verify`          | `verify_predictions()`    | `_predictive_engine.verifier.verify_pending(limit)`.             |
| GET    | `/v105/predictions/stats`           | `prediction_stats()`      | Computes pending/verified/correct/wrong/accuracy from predictor. |

### `stream_routes.py` (1 route)

| Method | Path                | Handler       | Notes                                                                          |
| ------ | ------------------- | ------------- | ------------------------------------------------------------------------------ |
| POST   | `/v105/ask/stream`  | `ask_stream()` | Streaming (SSE) version of `/ask`. Returns judge verdict step-by-step (classify → slm_predict → judge → final). Requires `get_judge()` to be ready. |

## How to activate a dead router

1. Open `scp/api_server.py`.
2. Find the `include_router` block (around lines 510–548).
3. Add an import + registration for the dead router. Example for `audit_routes.py`:

   ```python
   from scp.api.routes.audit_routes import router as audit_router
   # ... inside the create_app() / setup function ...
   app.include_router(audit_router)
   ```

4. Restart SCP: `python3 -m scp 8000`
5. Verify the route is now live:
   ```bash
   curl http://127.0.0.1:8000/v105/audit/stats
   # (or through the gateway)
   curl 'https://<gateway>/v105/audit/stats?XTransformPort=8000'
   ```
6. The dashboard's `/api/scp/routes` endpoint will mark the route
   `wired: true` (green "live" badge) instead of `wired: false`
   (red "dead" badge) automatically.

## Why not just delete them?

1. **WIP preservation.** The handler logic is real, tested code. A
   future round may want to wire these — deleting would lose the work
   + force a rewrite.
2. **Documentation value.** A dead route documents the *intent* of the
   SCP design (we *want* an audit dashboard, a threat feed, a streaming
   /ask) even if the wiring isn't ready yet.
3. **Safety.** Deleting code is irreversible (without git archaeology).
   Marking it dead is reversible in one line of `include_router`.

## See also

- `scp/api_server.py` lines 510–548 — the `include_router` block (the source of truth for "what's wired")
- `scp/GATEWAY.md` — full route table (live + dead, with gateway URLs)
- `src/app/api/scp/routes/route.ts` — dashboard route listing (reads `wired` field + renders badges)
- `docs/R9_FINDINGS.md` SA-R9-6 — original audit finding that flagged these as dead
