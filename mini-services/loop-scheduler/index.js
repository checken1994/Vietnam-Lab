/**
 * SCP Loop Scheduler — closed-loop mini-service (Node.js ESM).
 *
 * Goal (R10 Task 14.A / Bun Eradication Milestone M2):
 *   Autonomous continuous operations loop service on Port 3030.
 *   Periodically triggers deep audit cycles and autonomous explorer cycles,
 *   logs results durably, exposes liveness and management endpoints.
 *   Decoupled from legacy fake Ollama bridge; supports direct real API mode.
 *
 * Architecture:
 *   - Pure Node.js ESM (no Bun dependency, zero external npm packages).
 *   - node:http.createServer adapter for standard Web Request/Response.
 *   - node:fs/promises and node:fs for durable storage.
 *   - Independent Lineage & Process Isolation (SCP DNA #5, #14).
 *
 * Endpoints:
 *   GET  /                   — loop status JSON
 *   GET  /healthz            — liveness probe
 *   GET  /ready, /readiness  — readiness probe with dependency checks
 *   POST /trigger            — manual audit trigger (single-flight guarded)
 *   POST /trigger-autonomous — manual autonomous cycle trigger
 *   POST /pause              — pause loop (persisted to scheduler-state.json)
 *   POST /resume             — resume loop (persisted to scheduler-state.json)
 */

import http from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// ─── Config & Environment ──────────────────────────────────────────────────

function _loadEnvFile() {
  const _raw = process.env.SCP_ENV_FILE || process.env.SCP_SIDECAR_ENV_FILE;
  if (!_raw || !_raw.trim()) {
    return null;
  }
  const _override = _raw.trim();
  const _isAbsolute = _override.startsWith("/") || _override.startsWith("\\") || /^[A-Za-z]:/.test(_override);
  const _p = _isAbsolute ? _override : join(process.cwd(), _override);
  if (!existsSync(_p)) throw new Error(`[loop-scheduler] explicit env file not found: ${_p}`);
  const _content = readFileSync(_p, "utf-8");
  const _envRoot = dirname(_p);
  for (const _line of _content.split("\n")) {
    const _trimmed = _line.trim();
    if (!_trimmed || _trimmed.startsWith("#") || !_trimmed.includes("=")) continue;
    const _eqIdx = _trimmed.indexOf("=");
    const _key = _trimmed.slice(0, _eqIdx).trim();
    let _val = _trimmed.slice(_eqIdx + 1).trim();
    if ((_val.startsWith('"') && _val.endsWith('"')) || (_val.startsWith("'") && _val.endsWith("'"))) _val = _val.slice(1, -1);
    if (_key && !process.env[_key]) process.env[_key] = _val;
    if (_key.endsWith("_FILE") && _val) {
      const _rawPath = _val.replace(/^@file:/, "").replace(/^file:\/\//, "");
      const _secretPath = (/^[A-Za-z]:[\\/]|^[/\\]/.test(_rawPath)) ? _rawPath : join(_envRoot, _rawPath);
      if (!existsSync(_secretPath)) throw new Error(`[loop-scheduler] secret file not found for ${_key}: ${_secretPath}`);
      process.env[_key] = _secretPath;
      const _secretValue = readFileSync(_secretPath, "utf-8").trim();
      if (!_secretValue) throw new Error(`[loop-scheduler] secret file empty for ${_key}`);
      const _valueKey = _key.slice(0, -5);
      if (!process.env[_valueKey]) process.env[_valueKey] = _secretValue;
    }
  }
  return _p;
}

_loadEnvFile();

const LOOP_INTERVAL_SEC = Number(process.env.LOOP_INTERVAL_SEC ?? "300");
const AUTOFIX_MODE = (process.env.SCP_AUTOFIX_MODE ?? "observe").trim().toLowerCase();
const AUTOFIX_DETERMINISTIC_ONLY = (process.env.SCP_AUTOFIX_DETERMINISTIC_ONLY ?? "0").trim() === "1";
const AUTOFIX_WORKER_MODE = (process.env.SCP_AUTOFIX_WORKER_MODE ?? "inline").trim().toLowerCase();
const DETERMINISTIC_WORKER_LOOP = AUTOFIX_MODE === "apply" && AUTOFIX_DETERMINISTIC_ONLY && AUTOFIX_WORKER_MODE === "deterministic";
if (AUTOFIX_MODE !== "observe" && AUTOFIX_MODE !== "apply") {
  throw new Error(`[loop-scheduler] invalid SCP_AUTOFIX_MODE=${AUTOFIX_MODE}; refusing start`);
}
const AUTOFIX_MAX_BUGS = Math.max(0, Math.min(20, Number(process.env.SCP_MAX_AUDIT_BUGS ?? "5")));
const _scpBaseUrl = (process.env.SCP_BASE_URL || "").trim().replace(/\/+$/, "");
if (!_scpBaseUrl) throw new Error("[loop-scheduler] SCP_BASE_URL is required; refusing implicit backend default");
const SCP_BASE_URL = _scpBaseUrl;
const SCP_AUDIT_URL = `${SCP_BASE_URL}/v105/autofix/run-audit`;
const SCP_AUTONOMOUS_CYCLE_URL = `${SCP_BASE_URL}/v105/autonomous/run-cycle`;
const SCP_HEALTH_URL = `${SCP_BASE_URL}/health`;
const PORT = Number(process.env.LOOP_SCHEDULER_PORT ?? "3030");

// Decoupled LLM bridge configuration:
// LLM_BRIDGE_URL is optional. When omitted or direct provider mode is active,
// the scheduler does not depend on a fake Ollama bridge and routes calls directly to SCP backend.
const _llmBridgeUrl = (process.env.LLM_BRIDGE_URL || "").trim().replace(/\/+$/, "");
const LLM_BRIDGE_URL = _llmBridgeUrl || null;
const LLM_BRIDGE_TAGS_URL = LLM_BRIDGE_URL ? `${LLM_BRIDGE_URL}/api/tags` : null;
let activeBridgeUrl = LLM_BRIDGE_URL;

function isDirectLlmMode() {
  const providerMode = (process.env.SCP_LLM_PROVIDER_MODE || "").trim().toLowerCase();
  return !LLM_BRIDGE_URL || ["direct", "real_api", "native", "openrouter"].includes(providerMode);
}

const DEFAULT_LOG_PATH = join(process.cwd(), "..", "..", "data", "loop_runs.jsonl");
const LOOP_LOG_PATH = process.env.LOOP_LOG_PATH ?? DEFAULT_LOG_PATH;

const DEFAULT_STATE_PATH = join(process.cwd(), "..", "..", "data", "scheduler-state.json");
const LOOP_STATE_PATH = process.env.LOOP_STATE_PATH ?? DEFAULT_STATE_PATH;

let SCHEDULER_ADMIN_TOKEN = "";
const SCP_AUTH_TOKEN =
  process.env.SCP_AUTH_TOKEN_SECRET ?? process.env.SCP_AUTH_PASSWORD ?? "";

const SCP_FETCH_TIMEOUT_MS = DETERMINISTIC_WORKER_LOOP ? 30_000 : 600_000;

// ─── State ─────────────────────────────────────────────────────────────────

const state = {
  running: false,
  autonomous_running: false,
  paused: false,
  last_run: null,
  next_run_at: null,
  total_runs: 0,
  scp_online: false,
  bridge_online: false,
  recent_runs: [],
};

// ─── Logging & Persistence ─────────────────────────────────────────────────

async function appendRunToLog(run) {
  const line = JSON.stringify(run) + "\n";
  try {
    await mkdir(dirname(LOOP_LOG_PATH), { recursive: true });
    await appendFile(LOOP_LOG_PATH, line, "utf8");
  } catch (e) {
    console.error(
      `[loop-scheduler] log write failed: ${String(e).slice(0, 200)} — run was: ${line.trim()}`,
    );
  }
}

async function loadExistingRuns() {
  try {
    if (!existsSync(LOOP_LOG_PATH)) return { total: 0, recent: [] };
    const text = await readFile(LOOP_LOG_PATH, "utf8");
    const lines = text.split("\n").filter((l) => l.trim().length > 0);
    const runs = [];
    for (const line of lines) {
      try {
        runs.push(JSON.parse(line));
      } catch {
        // skip malformed line
      }
    }
    const recent = runs.slice(-10).reverse();
    return { total: runs.length, recent };
  } catch (e) {
    console.error(`[loop-scheduler] log read failed: ${String(e).slice(0, 200)}`);
    return { total: 0, recent: [] };
  }
}

async function loadPersistedState() {
  try {
    const raw = await readFile(LOOP_STATE_PATH, "utf8");
    const parsed = JSON.parse(raw);
    if (typeof parsed?.paused === "boolean") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

async function savePersistedState(s) {
  try {
    await mkdir(dirname(LOOP_STATE_PATH), { recursive: true });
    await writeFile(LOOP_STATE_PATH, JSON.stringify(s, null, 2), "utf8");
  } catch (e) {
    console.error(
      `[loop-scheduler] state write failed: ${String(e).slice(0, 200)} — state is ${JSON.stringify(s)}`,
    );
  }
}

// ─── Backend & LLM Interaction ─────────────────────────────────────────────

async function checkScpLiveness() {
  try {
    const res = await fetch(SCP_HEALTH_URL, {
      signal: AbortSignal.timeout(3000),
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function checkLlmBridgeLiveness() {
  if (isDirectLlmMode()) {
    return true;
  }
  if (activeBridgeUrl) {
    try {
      const res = await fetch(`${activeBridgeUrl}/api/tags`, {
        signal: AbortSignal.timeout(3000),
        headers: { Accept: "application/json" },
      });
      if (res.ok) return true;
    } catch {
      // Configured bridge port offline, probe candidates below
    }
  }

  const candidates = [
    "http://127.0.0.1:11434",
    "http://127.0.0.1:8081",
  ].filter((u) => u !== activeBridgeUrl);

  for (const candidate of candidates) {
    try {
      const res = await fetch(`${candidate}/api/tags`, {
        signal: AbortSignal.timeout(2000),
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        console.log(`[loop-scheduler] auto-detected active LLM bridge at ${candidate}`);
        activeBridgeUrl = candidate;
        return true;
      }
    } catch {
      // try next
    }
  }

  return false;
}

function extractSummary(scpBody) {
  const out = {};
  if (typeof scpBody !== "object" || scpBody === null) return out;
  const root = scpBody;
  const results = (root.results ?? root);
  if (!results || typeof results !== "object") return out;
  const num = (v) =>
    typeof v === "number" ? v : (typeof v === "string" && /^\d+$/.test(v) ? Number(v) : undefined);
  const processed = num(results.processed) ?? num(results.total) ?? num(results.findings_count);
  if (processed !== undefined) out.findings_count = processed;
  const fixed = num(results.fixed) ?? num(results.fixes_applied);
  if (fixed !== undefined) out.fixes_applied = fixed;
  const perm = num(results.permission_requested) ?? num(results.permissionRequests);
  if (perm !== undefined) out.permission_requested = perm;
  const skip = num(results.skipped) ?? num(results.skipped_count);
  if (skip !== undefined) out.skipped = skip;
  return out;
}

async function triggerAudit(triggeredBy) {
  state.running = true;
  const ts = new Date().toISOString();
  const start = Date.now();
  try {
    const scp_online = await checkScpLiveness();
    if (!scp_online) {
      const run = {
        ts,
        scp_online: false,
        status: "scp_offline",
        triggered_by: triggeredBy,
        duration_ms: Date.now() - start,
        error: `SCP not responding at ${SCP_HEALTH_URL}`,
      };
      state.scp_online = false;
      await appendRunToLog(run);
      recordRun(run);
      return run;
    }
    state.scp_online = true;

    const directMode = isDirectLlmMode();
    const bridge_online = (DETERMINISTIC_WORKER_LOOP || directMode) ? true : await checkLlmBridgeLiveness();
    state.bridge_online = bridge_online;
    if (!DETERMINISTIC_WORKER_LOOP && !directMode) {
      if (!bridge_online) {
        const run = {
          ts,
          scp_online: true,
          status: "bridge_offline",
          triggered_by: triggeredBy,
          duration_ms: Date.now() - start,
          error: `LLM bridge not responding at ${activeBridgeUrl || "configured bridge"}/api/tags — audit skipped to avoid UNKNOWN verdicts`,
        };
        await appendRunToLog(run);
        recordRun(run);
        return run;
      }
    }

    try {
      const headers = {
        "Content-Type": "application/json",
        Accept: "application/json",
      };
      if (SCP_AUTH_TOKEN) {
        headers.Authorization = `Bearer ${SCP_AUTH_TOKEN}`;
      }
      const res = await fetch(SCP_AUDIT_URL, {
        method: "POST",
        headers,
        signal: AbortSignal.timeout(SCP_FETCH_TIMEOUT_MS),
        body: JSON.stringify({ mode: AUTOFIX_MODE, max_bugs: AUTOFIX_MAX_BUGS }),
      });

      const duration_ms = Date.now() - start;
      let body = null;
      try {
        body = await res.json();
      } catch {
        // Non-JSON response
      }

      if (res.status === 401 || res.status === 403) {
        const run = {
          ts,
          scp_online: true,
          status: "auth_required",
          http_status: res.status,
          duration_ms,
          triggered_by: triggeredBy,
          error: "SCP_AUTH_TOKEN_SECRET not configured or invalid (verify_admin rejected)",
        };
        await appendRunToLog(run);
        recordRun(run);
        return run;
      }
      if (!res.ok) {
        const errText = typeof body === "object" && body !== null
          ? JSON.stringify(body).slice(0, 200)
          : `HTTP ${res.status}`;
        const run = {
          ts,
          scp_online: true,
          status: "error",
          http_status: res.status,
          duration_ms,
          triggered_by: triggeredBy,
          error: errText,
        };
        await appendRunToLog(run);
        recordRun(run);
        return run;
      }

      const summary = extractSummary(body);
      const run = {
        ts,
        scp_online: true,
        status: (DETERMINISTIC_WORKER_LOOP && typeof body === "object" && body !== null && body.mode === "queued") ? "queued" : "ok",
        http_status: res.status,
        duration_ms,
        triggered_by: triggeredBy,
        ...summary,
      };
      await appendRunToLog(run);
      recordRun(run);
      return run;
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      const run = {
        ts,
        scp_online: true,
        status: "error",
        duration_ms: Date.now() - start,
        triggered_by: triggeredBy,
        error: err.slice(0, 200),
      };
      await appendRunToLog(run);
      recordRun(run);
      return run;
    }
  } finally {
    state.running = false;
  }
}

function recordRun(run) {
  state.last_run = run;
  state.total_runs += 1;
  state.recent_runs.unshift(run);
  if (state.recent_runs.length > 10) state.recent_runs.length = 10;
  state.next_run_at = state.paused ? null : Date.now() + LOOP_INTERVAL_SEC * 1000;
}

async function triggerAutonomousCycle(triggeredBy) {
  if (state.autonomous_running) {
    console.log(`[loop-scheduler] autonomous cycle skipped — already running (${triggeredBy})`);
    return null;
  }
  state.autonomous_running = true;
  try {
    const scp_online = await checkScpLiveness();
    if (!scp_online) return null;
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    if (SCP_AUTH_TOKEN) {
      headers.Authorization = `Bearer ${SCP_AUTH_TOKEN}`;
    }
    const res = await fetch(SCP_AUTONOMOUS_CYCLE_URL, {
      method: "POST",
      headers,
      signal: AbortSignal.timeout(60_000),
      body: JSON.stringify({ count: 10, dispatch_tasks: true }),
    });
    if (res.ok) {
      const data = await res.json().catch(() => null);
      console.log(`[loop-scheduler] autonomous explorer cycle complete (${triggeredBy})`);
      return data;
    }
  } catch (err) {
    console.error(`[loop-scheduler] autonomous cycle error (${triggeredBy}): ${String(err).slice(0, 160)}`);
  } finally {
    state.autonomous_running = false;
  }
  return null;
}

// ─── Cron Loop ─────────────────────────────────────────────────────────────

let loopTimer = null;

async function cronStep() {
  if (state.paused) {
    state.next_run_at = null;
    return;
  }
  try {
    await triggerAudit("cron");
  } catch (e) {
    console.error(`[loop-scheduler] cronStep swallowed: ${String(e).slice(0, 200)}`);
  }
  try {
    await triggerAutonomousCycle("cron");
  } catch (e) {
    console.error(`[loop-scheduler] autonomousCycle swallowed: ${String(e).slice(0, 200)}`);
  }
  if (!state.paused) {
    state.next_run_at = Date.now() + LOOP_INTERVAL_SEC * 1000;
    loopTimer = setTimeout(() => void cronStep(), LOOP_INTERVAL_SEC * 1000);
  }
}

function startLoop() {
  if (loopTimer) return;
  state.next_run_at = Date.now() + LOOP_INTERVAL_SEC * 1000;
  loopTimer = setTimeout(() => void cronStep(), LOOP_INTERVAL_SEC * 1000);
  console.log("[loop-scheduler] loop started (interval/SCP URL/log path from config env)");
}

// ─── HTTP Handling ─────────────────────────────────────────────────────────

function getCorsHeaders(req) {
  const allowedOrigins = (process.env.DASHBOARD_ORIGIN ?? process.env.CORS_ALLOWED_ORIGINS ?? "http://127.0.0.1:3000,http://localhost:3000")
    .split(",")
    .map((s) => s.trim());
  const reqOrigin = req?.headers.get("origin");
  const allowedOrigin = reqOrigin && allowedOrigins.includes(reqOrigin) ? reqOrigin : null;
  const headers = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-SCP-Admin-Token",
  };
  if (allowedOrigin) {
    headers["Access-Control-Allow-Origin"] = allowedOrigin;
    headers["Vary"] = "Origin";
  }
  return headers;
}

function jsonResponse(data, status = 200, req) {
  const cors = getCorsHeaders(req);
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...cors,
    },
  });
}

function constantTimeTokenEquals(provided, expected) {
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(expected);
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  return diff === 0;
}

function extractProvidedToken(req) {
  const authorization = req.headers.get("authorization")?.trim() ?? "";
  let bearer = "";
  if (authorization) {
    bearer = authorization.toLowerCase().startsWith("bearer ")
      ? authorization.slice(7).trim()
      : authorization;
  }
  const headerToken = req.headers.get("x-scp-admin-token")?.trim() ?? "";
  return bearer || headerToken;
}

function getValidAdminTokens() {
  const tokens = [];
  if (SCHEDULER_ADMIN_TOKEN) tokens.push(SCHEDULER_ADMIN_TOKEN);
  const authTokenSecret = (process.env.SCP_AUTH_TOKEN_SECRET ?? "").trim();
  if (authTokenSecret && !tokens.includes(authTokenSecret)) tokens.push(authTokenSecret);
  const adminKey = (process.env.SCP_ADMIN_KEY ?? "").trim();
  if (adminKey && !tokens.includes(adminKey)) tokens.push(adminKey);
  const authPassword = (process.env.SCP_AUTH_PASSWORD ?? "").trim();
  if (authPassword && !tokens.includes(authPassword)) tokens.push(authPassword);
  return tokens;
}

function hasConfiguredAdminAuth() {
  return getValidAdminTokens().length > 0;
}

function schedulerAdminAuthorized(req) {
  const provided = extractProvidedToken(req);
  if (!provided) return false;

  const validTokens = getValidAdminTokens();
  for (const token of validTokens) {
    if (constantTimeTokenEquals(provided, token)) {
      return true;
    }
  }
  return false;
}

function isAuthorizedCaller(req) {
  if (schedulerAdminAuthorized(req)) return true;
  const provided = extractProvidedToken(req);
  if (SCP_AUTH_TOKEN && provided && constantTimeTokenEquals(provided, SCP_AUTH_TOKEN)) {
    return true;
  }
  return false;
}

async function handleRequest(req) {
  const url = new URL(req.url);
  const path = url.pathname;
  const method = req.method.toUpperCase();

  if (method === "POST" && new Set(["/trigger", "/trigger-autonomous", "/pause", "/resume"]).has(path)) {
    if (!hasConfiguredAdminAuth()) {
      return jsonResponse({ error: "scheduler admin auth not configured" }, 503, req);
    }
    if (!schedulerAdminAuthorized(req)) {
      return jsonResponse({ error: "scheduler admin authentication required" }, 401, req);
    }
  }

  // CORS preflight
  if (method === "OPTIONS") {
    return new Response(null, { status: 204, headers: getCorsHeaders(req) });
  }

  // GET / — loop status
  if (method === "GET" && (path === "/" || path === "")) {
    if (!state.scp_online) state.scp_online = await checkScpLiveness();
    if (!state.bridge_online) state.bridge_online = await checkLlmBridgeLiveness();
    const authorized = isAuthorizedCaller(req);
    return jsonResponse({
      service: "scp-loop-scheduler",
      version: "0.1.0",
      running: state.running,
      paused: state.paused,
      last_run: state.last_run,
      next_run: state.paused ? null : (state.next_run_at
        ? new Date(state.next_run_at).toISOString() : null),
      next_run_epoch_ms: state.paused ? null : state.next_run_at,
      interval_sec: LOOP_INTERVAL_SEC,
      total_runs: state.total_runs,
      scp_online: state.scp_online,
      bridge_online: state.bridge_online,
      bridge_url: activeBridgeUrl || "direct",
      scp_base_url: SCP_BASE_URL,
      scp_audit_url: SCP_AUDIT_URL,
      log_path: authorized ? LOOP_LOG_PATH : null,
      state_path: authorized ? LOOP_STATE_PATH : null,
      auth_configured: Boolean(SCP_AUTH_TOKEN),
      recent_runs: state.recent_runs,
    }, 200, req);
  }

  // GET /healthz — liveness
  if (method === "GET" && path === "/healthz") {
    return jsonResponse({ status: "ok", service: "scp-loop-scheduler" }, 200, req);
  }

  // GET /ready and /readiness
  if (method === "GET" && (path === "/ready" || path === "/readiness")) {
    return jsonResponse({
      status: "ready",
      service: "scp-loop-scheduler",
      paused: state.paused,
      running: state.running,
      dependency_checks: {
        scp: state.scp_online ? "ok" : "unknown",
        llm_bridge: state.bridge_online ? "ok" : "unknown",
      },
    }, 200, req);
  }

  // POST /trigger
  if (method === "POST" && path === "/trigger") {
    if (state.running) {
      return jsonResponse(
        {
          error: "audit already running",
          triggered: false,
          running: true,
          last_run: state.last_run,
          message: "A cron tick or manual trigger is in flight. Retry in a few seconds.",
        },
        409,
        req,
      );
    }
    const run = await triggerAudit("manual");
    return jsonResponse({ triggered: true, run }, 200, req);
  }

  // POST /trigger-autonomous
  if (method === "POST" && path === "/trigger-autonomous") {
    if (state.autonomous_running) {
      return jsonResponse(
        {
          error: "autonomous cycle already running",
          triggered: false,
          running: true,
          message: "An autonomous cycle is currently in flight. Retry shortly.",
        },
        409,
        req,
      );
    }
    const result = await triggerAutonomousCycle("manual");
    return jsonResponse({ triggered: true, result }, 200, req);
  }

  // POST /pause
  if (method === "POST" && path === "/pause") {
    state.paused = true;
    state.next_run_at = null;
    if (loopTimer) {
      clearTimeout(loopTimer);
      loopTimer = null;
    }
    await savePersistedState({ paused: true, paused_at: new Date().toISOString() });
    return jsonResponse({ paused: true, next_run: null }, 200, req);
  }

  // POST /resume
  if (method === "POST" && path === "/resume") {
    if (!state.paused) {
      return jsonResponse({ resumed: false, note: "loop was not paused" }, 200, req);
    }
    state.paused = false;
    await savePersistedState({ paused: false, paused_at: null });
    startLoop();
    return jsonResponse({
      resumed: true,
      next_run: state.next_run_at ? new Date(state.next_run_at).toISOString() : null,
    }, 200, req);
  }

  // 404
  return jsonResponse({ error: "not found", path, method }, 404, req);
}

// ─── Bootstrap ─────────────────────────────────────────────────────────────

async function main() {
  SCHEDULER_ADMIN_TOKEN = (process.env.SCP_SCHEDULER_ADMIN_TOKEN ?? "").trim();
  const tokenFile = (process.env.SCP_SCHEDULER_ADMIN_TOKEN_FILE ?? "").trim();
  if (tokenFile) {
    if (!existsSync(tokenFile)) throw new Error(`[loop-scheduler] secret file not found: ${tokenFile}`);
    const tokenText = (await readFile(tokenFile, "utf8")).trim();
    if (!tokenText) throw new Error("SCP_SCHEDULER_ADMIN_TOKEN_FILE is empty");
    SCHEDULER_ADMIN_TOKEN = tokenText;
  }
  if (process.env.SCP_PRODUCTION_MODE === "1" && !hasConfiguredAdminAuth()) {
    throw new Error("production scheduler admin token is not configured");
  }
  console.log("[loop-scheduler] booting (port/interval/mode/max_bugs/SCP URL from config env)");

  const { total, recent } = await loadExistingRuns();
  state.total_runs = total;
  state.recent_runs = recent;
  if (recent.length > 0) state.last_run = recent[0];

  const persisted = await loadPersistedState();
  if (process.env.LOOP_START_PAUSED === "1") {
    state.paused = true;
    console.log("[loop-scheduler] LOOP_START_PAUSED=1 → starting paused (env override)");
  } else if (persisted) {
    state.paused = persisted.paused;
  }

  const probeInitialScp = async () => {
    let lastOk = false;
    for (let i = 0; i < 6; i++) {
      const ok = await checkScpLiveness();
      lastOk = ok;
      state.scp_online = ok;
      if (ok) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    console.log(lastOk ? "[loop-scheduler] initial SCP liveness: online" : "[loop-scheduler] initial SCP liveness: offline");
  };
  void probeInitialScp();

  const probeInitialBridge = async () => {
    if (isDirectLlmMode()) {
      state.bridge_online = true;
      console.log("[loop-scheduler] direct real API mode (no LLM bridge required)");
      return;
    }
    let lastOk = false;
    for (let i = 0; i < 6; i++) {
      const ok = await checkLlmBridgeLiveness();
      lastOk = ok;
      state.bridge_online = ok;
      if (ok) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    console.log(lastOk ? "[loop-scheduler] initial LLM bridge liveness: online" : "[loop-scheduler] initial LLM bridge liveness: offline");
  };
  void probeInitialBridge();

  if (state.paused) {
    // Paused
  } else {
    startLoop();
  }

  const HOST = process.env.LOOP_SCHEDULER_HOST ?? "127.0.0.1";

  const server = http.createServer(async (nodeReq, nodeRes) => {
    try {
      const hostHeader = nodeReq.headers.host || `${HOST}:${PORT}`;
      const url = `http://${hostHeader}${nodeReq.url}`;
      const headers = new Headers();
      for (const [k, v] of Object.entries(nodeReq.headers)) {
        if (v !== undefined) {
          if (Array.isArray(v)) v.forEach((x) => headers.append(k, x));
          else headers.set(k, v);
        }
      }
      const hasBody = nodeReq.method !== "GET" && nodeReq.method !== "HEAD";
      const init = { method: nodeReq.method, headers };
      if (hasBody) {
        const chunks = [];
        for await (const chunk of nodeReq) {
          chunks.push(chunk);
        }
        init.body = Buffer.concat(chunks);
        init.duplex = "half";
      }
      const req = new Request(url, init);
      const res = await handleRequest(req);
      nodeRes.statusCode = res.status;
      res.headers.forEach((v, k) => nodeRes.setHeader(k, v));
      const buffer = Buffer.from(await res.arrayBuffer());
      nodeRes.end(buffer);
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      console.error(`[loop-scheduler] handler error: ${err.slice(0, 200)}`);
      if (!nodeRes.headersSent) {
        nodeRes.statusCode = 500;
        nodeRes.setHeader("Content-Type", "application/json");
        nodeRes.end(JSON.stringify({ error: "internal", message: err.slice(0, 200) }));
      }
    }
  });

  server.listen(PORT, HOST, () => {
    console.log("[loop-scheduler] listening (loopback only — DNA #6; host/port from config)");
  });

  const shutdown = (sig) => {
    console.log(`[loop-scheduler] received ${sig}, shutting down`);
    if (loopTimer) clearTimeout(loopTimer);
    server.close(() => {
      process.exit(0);
    });
    setTimeout(() => process.exit(0), 1000).unref();
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

void main();
