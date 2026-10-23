#!/usr/bin/env node
/**
 * serve.js — Máy chủ tĩnh cho bản đồ mô phỏng + proxy chỉ đọc tới dashboard SCP.
 *
 * Dùng:
 *   node server/serve.js                 # http://127.0.0.1:5177
 *   node server/serve.js --port 5200
 *   SCP_DASHBOARD_URL=http://127.0.0.1:3000 node server/serve.js
 *   SCP_DASHBOARD_PROXY_SECRET=<secret> node server/serve.js
 *
 * Không phụ thuộc thư viện ngoài (chỉ Node built-ins).
 */
"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { URL } = require("node:url");

const {
  isAllowedLiveRoute,
  resolveUpstreamBase,
  isSafeBase,
  fetchUpstream,
  fetchSnapshot,
  UPSTREAM_ROUTES,
} = require("./live-proxy.js");

const ROOT = path.resolve(__dirname, "..");
const STATIC_ROOT = path.join(ROOT, "scp-office");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".map": "application/json; charset=utf-8",
};

function log(...args) {
  const ts = new Date().toISOString();
  process.stdout.write(`[${ts}] ${args.join(" ")}\n`);
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(body);
}

/**
 * Chặn path traversal: đường dẫn đã resolve phải nằm trong STATIC_ROOT.
 *
 * [FIX · 2026-10-22 · DoS] `decodeURIComponent` NÉM `URIError` với input hỏng
 * (`/%ZZ`, `/%E0%A4%A`). Trước đây lỗi này nổi ra khỏi handler 'request' →
 * uncaught exception → tiến trình chết, tức là 1 request không xác thực làm
 * sập server (đã reproduce bằng raw socket). Nay bắt lỗi tại biên và trả 403.
 */
function resolveStaticPath(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath.split("?")[0]);
  } catch (e) {
    return null; // URL không giải mã được → từ chối, KHÔNG làm chết tiến trình
  }
  if (decoded.includes("\0")) return null;
  const rel = decoded === "/" ? "index.html" : decoded.replace(/^\/+/, "");
  const abs = path.resolve(STATIC_ROOT, rel);
  if (abs !== STATIC_ROOT && !abs.startsWith(STATIC_ROOT + path.sep)) return null;
  return abs;
}

function serveStatic(req, res, urlPath) {
  const abs = resolveStaticPath(urlPath);
  if (!abs) {
    sendJson(res, 403, { error: "đường dẫn không hợp lệ hoặc nằm ngoài thư mục phục vụ" });
    return;
  }
  fs.stat(abs, (err, stat) => {
    if (err || !stat.isFile()) {
      sendJson(res, 404, { error: "không tìm thấy tài nguyên" });
      return;
    }
    const type = MIME[path.extname(abs).toLowerCase()] || "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": type,
      "Content-Length": stat.size,
      "Cache-Control": "no-cache",
      "X-Content-Type-Options": "nosniff",
    });
    // HEAD chỉ trả header, không trả body (đúng HTTP spec).
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    const stream = fs.createReadStream(abs);
    stream.on("error", () => {
      if (!res.headersSent) sendJson(res, 500, { error: "lỗi đọc tài nguyên" });
      else res.end();
    });
    stream.pipe(res);
  });
}

/**
 * Origin được phép đọc dữ liệu runtime qua CORS.
 *
 * Cố ý KHÔNG dùng "*": dữ liệu /api/scp/* là thông tin nội bộ (topology, log),
 * và dashboard chủ động chặn nó bằng middleware. Nếu proxy này trả ACAO="*" thì
 * bất kỳ website nào người dùng mở cũng có thể đọc được — tự mở lại đúng cái
 * rủi ro mà repo đã gia cố. Vì vậy chỉ origin dashboard trên máy này được phép.
 */
const DEFAULT_ALLOWED_ORIGINS = [
  "http://127.0.0.1:3000",
  "http://localhost:3000",
];

function allowedOrigins() {
  const raw = (process.env.SCP_SIMULATION_ALLOWED_ORIGINS || "").trim();
  if (!raw) return DEFAULT_ALLOWED_ORIGINS;
  return raw
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean);
}

/** Gắn header CORS nếu origin nằm trong allowlist; luôn kèm Vary: Origin. */
function applyCors(req, res) {
  const origin = req.headers.origin;
  res.setHeader("Vary", "Origin");
  if (!origin) return null;
  if (!allowedOrigins().includes(origin)) return null;
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Methods", "GET, HEAD");
  res.setHeader("Access-Control-Max-Age", "600");
  return origin;
}

/**
 * Cache phía proxy cho snapshot upstream.
 *
 * [FIX · 2026-10-22 · Rate limit] Backend SCP áp `tier=public limit=30/phút`
 * cho /health. Proxy poll 4 endpoint mỗi 5 giây ⇒ 48 probe/phút ⇒ backend trả
 * 429 ⇒ dashboard báo nhầm "FastAPI down" trong khi dịch vụ vẫn sống.
 * Nay proxy chỉ gọi upstream tối đa 1 lần mỗi SCP_LIVE_CACHE_MS (mặc định 30s)
 * và phục vụ bản cache cho các lần poll giữa; payload cache luôn kèm
 * `upstream.cached=true` + `upstream.ageMs` để UI hiển thị đúng tuổi dữ liệu.
 * Chỉ cache khi upstream thực sự trả lời (reachable=true) → lỗi không bị kẹt.
 */
const CACHE_TTL_MS = Number(process.env.SCP_LIVE_CACHE_MS || 30000);
let snapshotCache = null; // { payload, fetchedAtMs }

async function getSnapshotWithCache(base, opts) {
  const now = Date.now();
  if (snapshotCache && now - snapshotCache.fetchedAtMs < CACHE_TTL_MS) {
    const ageMs = now - snapshotCache.fetchedAtMs;
    return Object.assign({}, snapshotCache.payload, {
      upstream: Object.assign({}, snapshotCache.payload.upstream, { cached: true, ageMs }),
    });
  }
  const payload = await fetchSnapshot(base, opts);
  if (payload.upstream && payload.upstream.reachable) {
    snapshotCache = { payload, fetchedAtMs: Date.now() };
  } else {
    snapshotCache = null; // lỗi không được cache → lần poll sau thử lại ngay
  }
  return payload;
}

/**
 * Xử lý /api/live/* — CHỈ ĐỌC.
 * GET /api/live/snapshot         → gom cả 4 endpoint
 * GET /api/live/{status|health|activity|metrics} → passthrough
 */
async function handleLive(req, res, url) {
  const corsOrigin = applyCors(req, res);

  if (req.method === "OPTIONS") {
    // Preflight: chỉ trả 204 khi origin được phép, ngược lại 403.
    if (!corsOrigin) {
      sendJson(res, 403, { error: "origin không được phép đọc kênh runtime" });
      log(`LIVE OPTIONS ${url.pathname} -> 403 (origin bị từ chối)`);
      return;
    }
    res.writeHead(204);
    res.end();
    return;
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD", "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: "kênh dữ liệu runtime chỉ cho phép đọc (GET)" }));
    log(`LIVE ${req.method} ${url.pathname} -> 405 (write bị chặn)`);
    return;
  }

  const name = (url.pathname.replace(/^\/api\/live\/?/, "") || "snapshot").split("/")[0];
  if (!isAllowedLiveRoute(name)) {
    sendJson(res, 404, { error: `route live không được phép: ${name}` });
    log(`LIVE GET ${url.pathname} -> 404 (route không trong allowlist)`);
    return;
  }

  const base = resolveUpstreamBase(process.env);
  if (!isSafeBase(base)) {
    sendJson(res, 500, { error: "SCP_DASHBOARD_URL không hợp lệ" });
    log(`LIVE GET ${url.pathname} -> 500 (upstream base không hợp lệ)`);
    return;
  }

  const secret = (process.env.SCP_DASHBOARD_PROXY_SECRET || "").trim();
  const opts = { secret: secret || undefined, timeoutMs: 4000 };

  if (name === "snapshot") {
    const snapshot = await getSnapshotWithCache(base, opts);
    const reachable = snapshot.upstream.reachable;
    sendJson(res, 200, snapshot);
    log(
      `LIVE GET ${url.pathname} -> 200 (upstream ${base} reachable=${reachable}` +
        `${snapshot.upstream.cached ? ` cached age=${snapshot.upstream.ageMs}ms` : ""})`
    );
    return;
  }

  const result = await fetchUpstream(base, name, opts);
  if (result.body === null) {
    sendJson(res, 502, {
      error: result.error || "upstream không trả về JSON",
      route: UPSTREAM_ROUTES[name],
      upstream: base,
      httpStatus: result.status,
    });
    log(`LIVE GET ${url.pathname} -> 502 (${result.error})`);
    return;
  }
  sendJson(res, 200, result.body);
  log(`LIVE GET ${url.pathname} -> 200 (${UPSTREAM_ROUTES[name]})`);
}

function createServer(options) {
  const opts = options || {};
  const server = http.createServer((req, res) => {
    // Lưới an toàn: handler không bao giờ được để ném exception ra ngoài
    // (một lỗi lạ phải trả lỗi HTTP, không được làm chết tiến trình).
    try {
      handleRequest(req, res);
    } catch (err) {
      log(`LỖI KHÔNG MONG ĐỢI: ${err && err.message}`);
      if (!res.headersSent) {
        sendJson(res, 500, { error: "lỗi nội bộ" });
      } else {
        res.end();
      }
    }
  });

  server.on("clientError", (err, socket) => {
    if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
  });

  server.on("error", (err) => {
    log(`LỖI SERVER: ${err && err.message}`);
  });

  return server;
}

function handleRequest(req, res) {
  let url;
  try {
    url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
  } catch (e) {
    sendJson(res, 400, { error: "URL không hợp lệ" });
    return;
  }

  if (url.pathname.startsWith("/api/live")) {
    handleLive(req, res, url).catch((err) => {
      sendJson(res, 500, { error: "lỗi nội bộ khi đọc runtime", detail: String(err && err.message) });
      log(`LIVE GET ${url.pathname} -> 500 (${err && err.message})`);
    });
    return;
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD" });
    res.end();
    return;
  }

  serveStatic(req, res, url.pathname);
}

function start(port, host) {
  const server = createServer();
  server.listen(port, host, () => {
    const base = resolveUpstreamBase(process.env);
    log(`Bản đồ mô phỏng SCP: http://${host}:${port}/`);
    log(`Proxy chỉ đọc  : ${base}/api/scp/{status,health,activity,metrics}`);
    log(
      process.env.SCP_DASHBOARD_PROXY_SECRET
        ? "Chế độ proxy-secret: dùng SCP_DASHBOARD_PROXY_SECRET từ env."
        : "Không có SCP_DASHBOARD_PROXY_SECRET → dùng nhánh dev-mode (loopback). Dashboard phải chạy SCP_DEV_MODE=1, nếu không sẽ fail-closed 503 và trang hiển thị MOCK."
    );
  });
  return server;
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const portArg = args.indexOf("--port");
  const port = Number(portArg >= 0 ? args[portArg + 1] : process.env.PORT || 5177);
  const host = process.env.HOST || "127.0.0.1";
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    process.stderr.write("Cổng không hợp lệ. Dùng: node server/serve.js --port <1-65535>\n");
    process.exit(2);
  }
  start(port, host);
}

module.exports = { createServer, start, resolveStaticPath, STATIC_ROOT, ROOT };
