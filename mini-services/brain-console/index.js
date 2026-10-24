/**
 * SCP Brain Console — canonical Node.js ESM entrypoint.
 *
 * A live, read-only projection of the entire brain of a running SCP
 * deployment: the Task Kernel state machine and its event journal, every
 * SQLite database with physical integrity, the self-audit ledgers, the
 * service topology, the skill pack and the latest runtime audit verdict.
 *
 * ── Why it exists ──────────────────────────────────────────────────────────
 * The brain of SCP is distributed across dozens of stores and processes.
 * No single surface shows what is actually happening: the dashboard reads
 * a handful of endpoints, the audit tools read other stores, the
 * mini-services read others again. This console reads all of them at once,
 * over one boundary, and refuses to call anything healthy that it could
 * not observe.
 *
 * ── Operational contract ───────────────────────────────────────────────────
 * * Zero npm dependencies (`node:http`, `node:sqlite`, `node:fs`).
 * * Read-only against every SQLite database.
 * * Loopback bind by default (DNA #6).
 * * No secret value is ever placed in a response or a log line.
 *
 * [S10c taint-removal] The resolved env-file path is derived from
 * `process.env` and must never reach a log sink.
 */

import { createServer } from "node:http";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

import {
  buildBrainSnapshot,
  discoverDatabases,
  inspectDatabase,
  isBackupPath,
  projectJournal,
  projectKernel,
  projectLedgers,
  projectRuntimeAudit,
  projectServices,
  projectSkills,
} from "./core.js";
import { renderBrainPage } from "./view.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const SERVICE_ROOT = resolve(__dirname, "..");
const REPO_ROOT = resolve(SERVICE_ROOT, "..");

// ─── Configuration ───────────────────────────────────────────────────────────

/**
 * Load an explicit env file. Never auto-loads the repository `.env` —
 * the path must be named explicitly by the operator.
 */
function loadEnvFile() {
  const raw = process.env.SCP_ENV_FILE || process.env.SCP_SIDECAR_ENV_FILE;
  if (!raw || !raw.trim()) return null;
  try {
    const text = readFileSync(raw, "utf8");
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx <= 0) continue;
      const key = trimmed.slice(0, idx).trim();
      let val = trimmed.slice(idx + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = val;
    }
  } catch (err) {
    console.error(`[brain-console] env file unreadable: ${String(err.message).slice(0, 160)}`);
  }
  return null;
}

loadEnvFile();

const PORT = Number(process.env.SCP_BRAIN_CONSOLE_PORT || "8090");
const HOST = process.env.SCP_BRAIN_CONSOLE_HOST || "127.0.0.1";
const HTTP_TIMEOUT_MS = Number(process.env.SCP_BRAIN_HTTP_TIMEOUT_MS || "5000");
const POLL_MS = Number(process.env.SCP_BRAIN_POLL_MS || "5000");
const SNAPSHOT_TTL_MS = Number(process.env.SCP_BRAIN_SNAPSHOT_TTL_MS || "1000");
const SERVICE_PROBE_TTL_MS = Number(process.env.SCP_BRAIN_SERVICE_TTL_MS || "30000");

const DATA_DIR = resolve(process.env.SCP_DATA_DIR || join(REPO_ROOT, "data"));
const SKILLS_DIR = resolve(process.env.SCP_BRAIN_SKILLS_DIR || join(REPO_ROOT, ".agents", "skills"));

const CONFIG = {
  dataDir: DATA_DIR,
  skillsDir: SKILLS_DIR,
  backendUrl: (process.env.SCP_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, ""),
  loopSchedulerUrl: (process.env.LOOP_SCHEDULER_URL || "http://127.0.0.1:3030").replace(/\/+$/, ""),
  agentDesktopUrl: (process.env.AGENT_DESKTOP_URL || "http://127.0.0.1:6080").replace(/\/+$/, ""),
  httpTimeoutMs: HTTP_TIMEOUT_MS,
  // Optional operator credential so protected backend surfaces can be
  // observed instead of merely seen refusing the request. The value is
  // never echoed back in a response or written to a log.
  adminToken: (process.env.SCP_BRAIN_ADMIN_TOKEN || process.env.SCP_ADMIN_KEY || "").trim(),
};

// ─── Snapshot cache ─────────────────────────────────────────────────────────

let cachedSnapshot = null;
let cachedAt = 0;
let inFlight = null;
let cachedServices = null;
let cachedServicesAt = 0;
let servicesInFlight = null;

/**
 * Service probes run on their own, slower cadence.
 *
 * The backend is a busy process with its own autonomous cycles. Polling it
 * every refresh cycle adds load to the very system being observed and can
 * provoke transient connection failures that look like real outages.
 */
async function getServices(force = false) {
  const now = Date.now();
  if (!force && cachedServices && now - cachedServicesAt < SERVICE_PROBE_TTL_MS) {
    return cachedServices;
  }
  if (servicesInFlight) return servicesInFlight;
  servicesInFlight = projectServices(CONFIG)
    .then((services) => {
      cachedServices = services;
      cachedServicesAt = Date.now();
      return services;
    })
    .finally(() => {
      servicesInFlight = null;
    });
  return servicesInFlight;
}

async function getSnapshot(force = false) {
  const now = Date.now();
  if (!force && cachedSnapshot && now - cachedAt < SNAPSHOT_TTL_MS) {
    return cachedSnapshot;
  }
  if (inFlight) return inFlight;
  inFlight = buildBrainSnapshot(CONFIG, { services: getServices })
    .then((snapshot) => {
      cachedSnapshot = snapshot;
      cachedAt = Date.now();
      return snapshot;
    })
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

// ─── HTTP helpers ───────────────────────────────────────────────────────────

const CORS_ALLOW_ORIGINS = (process.env.CORS_ALLOWED_ORIGINS || "http://127.0.0.1:3000,http://localhost:3000")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);

function corsHeaders(req) {
  const origin = req.headers.origin;
  if (origin && CORS_ALLOW_ORIGINS.includes(origin)) {
    return {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
  }
  return {};
}

function jsonResponse(body, status = 200, req) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(req),
    },
  });
}

// ─── Routes ─────────────────────────────────────────────────────────────────

async function handleRequest(req) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const path = url.pathname;
  const method = (req.method || "GET").toUpperCase();

  if (method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }
  if (method !== "GET" && method !== "HEAD") {
    return jsonResponse({ error: "method not allowed", path, method }, 405, req);
  }

  if (path === "/" || path === "/index.html") {
    return new Response(renderBrainPage(POLL_MS), {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        ...corsHeaders(req),
      },
    });
  }

  if (path === "/healthz") {
    return jsonResponse({ status: "ok", service: "scp-brain-console", version: "1.0.0" }, 200, req);
  }

  if (path === "/ready" || path === "/readiness") {
    const dataDirPresent = existsSync(CONFIG.dataDir);
    let backendOk = false;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), CONFIG.httpTimeoutMs);
      const response = await fetch(`${CONFIG.backendUrl}/health`, { signal: controller.signal });
      clearTimeout(timer);
      backendOk = response.status < 500;
    } catch {
      backendOk = false;
    }
    const ready = dataDirPresent && backendOk;
    return jsonResponse(
      {
        status: ready ? "ready" : "degraded",
        service: "scp-brain-console",
        checks: {
          data_dir: dataDirPresent ? "ok" : "missing",
          backend: backendOk ? "ok" : "unreachable",
        },
        note: "The console projects reality. It stays available when the backend is down so the failure is observable.",
      },
      ready ? 200 : 503,
      req,
    );
  }

  if (path === "/api/brain") {
    const snapshot = await getSnapshot();
    return jsonResponse(snapshot, 200, req);
  }

  if (path === "/api/storage") {
    const dbs = discoverDatabases(CONFIG.dataDir);
    return jsonResponse(
      {
        dataDir: CONFIG.dataDir,
        databases: dbs.map((dbPath) => inspectDatabase(dbPath, CONFIG.dataDir)),
      },
      200,
      req,
    );
  }

  if (path === "/api/kernel") {
    const dbs = discoverDatabases(CONFIG.dataDir).filter((dbPath) => !isBackupPath(dbPath));
    const kernels = dbs
      .map((dbPath) => projectKernel(dbPath, CONFIG.dataDir))
      .filter((k) => k.observable || (k.error && k.error !== "not a Task Kernel database"));
    return jsonResponse({ dataDir: CONFIG.dataDir, kernels }, 200, req);
  }

  if (path === "/api/journal") {
    const dbs = discoverDatabases(CONFIG.dataDir).filter((dbPath) => !isBackupPath(dbPath));
    const journals = dbs
      .map((dbPath) => projectJournal(dbPath, CONFIG.dataDir))
      .filter((j) => j.observable || (j.error && j.error !== "no events table"));
    return jsonResponse({ dataDir: CONFIG.dataDir, journals }, 200, req);
  }

  if (path === "/api/services") {
    const services = await getServices(true);
    return jsonResponse({ services }, 200, req);
  }

  if (path === "/api/loops") {
    return jsonResponse({ ledgers: projectLedgers(CONFIG.dataDir) }, 200, req);
  }

  if (path === "/api/skills") {
    return jsonResponse({ skills: projectSkills(CONFIG.skillsDir) }, 200, req);
  }

  if (path === "/api/audit") {
    return jsonResponse({ runtimeAudit: projectRuntimeAudit(CONFIG.dataDir) }, 200, req);
  }

  if (path === "/api/stream") {
    const stream = new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder();
        const send = (event, data) => {
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        };
        let closed = false;
        const tick = async () => {
          if (closed) return;
          try {
            send("brain", await getSnapshot(true));
          } catch (err) {
            send("error", { message: String(err.message).slice(0, 200) });
          }
        };
        await tick();
        const interval = setInterval(tick, Math.max(1000, POLL_MS));
        const shutdown = () => {
          closed = true;
          clearInterval(interval);
          try {
            controller.close();
          } catch {
            /* already closed */
          }
        };
        req.signal?.addEventListener("abort", shutdown);
      },
    });
    return new Response(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        Connection: "keep-alive",
        ...corsHeaders(req),
      },
    });
  }

  return jsonResponse({ error: "not found", path, method }, 404, req);
}

// ─── Server ─────────────────────────────────────────────────────────────────

const server = createServer(async (nodeReq, nodeRes) => {
  try {
    const hostHeader = nodeReq.headers.host || `${HOST}:${PORT}`;
    const url = `http://${hostHeader}${nodeReq.url}`;
    const headers = new Headers();
    for (const [key, value] of Object.entries(nodeReq.headers)) {
      if (value === undefined) continue;
      if (Array.isArray(value)) value.forEach((item) => headers.append(key, item));
      else headers.set(key, value);
    }
    const req = new Request(url, { method: nodeReq.method, headers });
    const res = await handleRequest(req);
    nodeRes.statusCode = res.status;
    res.headers.forEach((value, key) => nodeRes.setHeader(key, value));
    const buffer = Buffer.from(await res.arrayBuffer());
    nodeRes.end(buffer);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[brain-console] handler error: ${message.slice(0, 200)}`);
    if (!nodeRes.headersSent) {
      nodeRes.statusCode = 500;
      nodeRes.setHeader("Content-Type", "application/json");
      nodeRes.end(JSON.stringify({ error: "internal", message: message.slice(0, 200) }));
    }
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[brain-console] listening on http://${HOST}:${PORT} (read-only projection)`);
  console.log(`[brain-console] data dir : ${CONFIG.dataDir}`);
  console.log(`[brain-console] backend  : ${CONFIG.backendUrl}`);
});

const shutdown = () => {
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1000).unref();
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
