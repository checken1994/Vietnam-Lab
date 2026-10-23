/**
 * proxy.test.js — Kiểm chứng tầng proxy chỉ đọc (fail-closed).
 *
 * Nhánh âm (anti-placebo): chứng minh proxy THỰC SỰ chặn ghi và chặn
 * đường dẫn ngoài allowlist — không chỉ "trông như có kiểm soát".
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const path = require("node:path");

const { createServer } = require(path.join(__dirname, "..", "server", "serve.js"));
const {
  isAllowedLiveRoute,
  isSafeBase,
  buildUpstreamHeaders,
  fetchSnapshot,
  fetchUpstream,
} = require(path.join(__dirname, "..", "server", "live-proxy.js"));

/** Upstream giả: trả payload đúng hình dạng endpoint của dashboard. */
function startFakeUpstream() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader("Content-Type", "application/json");
      if (req.url === "/api/scp/health") {
        res.end(
          JSON.stringify({
            scp: "online",
            overall: true,
            fastapi: { ok: true, latencyMs: 9, status: 200 },
            loopScheduler: { ok: true, latencyMs: 5, status: 200 },
            agentDesktop: { ok: true, latencyMs: 11, status: 200 },
            llmBridge: { ok: true, latencyMs: 0, status: 200 },
          })
        );
        return;
      }
      if (req.url === "/api/scp/status") {
        res.end(JSON.stringify({ scp: "online", engine: { release: "GA B66", v4Loc: 4333, v4LocLive: true } }));
        return;
      }
      if (req.url === "/api/scp/activity") {
        res.end(JSON.stringify({ success: true, summary: { latestRunStatus: "ok", totalRunsRecorded: 3 }, events: [] }));
        return;
      }
      if (req.url === "/api/scp/metrics") {
        res.end(JSON.stringify({ cpuPercent: 12.5, memoryPercent: 44.2 }));
        return;
      }
      if (req.url === "/api/scp/permissions") {
        res.end(
          JSON.stringify({ pending: [], count: 0, checkedAt: "2026-10-22T10:00:00.000Z" })
        );
        return;
      }
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "not found" }));
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, base: `http://127.0.0.1:${port}` });
    });
  });
}

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

test("allowlist route: chỉ 5 route upstream + snapshot", () => {
  assert.equal(isAllowedLiveRoute("status"), true);
  assert.equal(isAllowedLiveRoute("health"), true);
  assert.equal(isAllowedLiveRoute("activity"), true);
  assert.equal(isAllowedLiveRoute("metrics"), true);
  assert.equal(isAllowedLiveRoute("permissions"), true);
  assert.equal(isAllowedLiveRoute("snapshot"), true);
  assert.equal(isAllowedLiveRoute("anything-else"), false);
  assert.equal(isAllowedLiveRoute("../secrets"), false);
});

test("route permissions được phép và trả về hàng đợi duyệt (read-only)", async () => {
  const fake = await startFakeUpstream();
  try {
    const result = await fetchUpstream(fake.base, "permissions", { timeoutMs: 2000 });
    assert.equal(result.ok, true);
    assert.equal(result.body.count, 0);
    assert.deepEqual(result.body.pending, []);
  } finally {
    await close(fake.server);
  }
});

test("base upstream: chỉ chấp nhận http/https", () => {
  assert.equal(isSafeBase("http://127.0.0.1:3000"), true);
  assert.equal(isSafeBase("https://example.internal"), true);
  assert.equal(isSafeBase("file:///etc/passwd"), false);
  assert.equal(isSafeBase("javascript:alert(1)"), false);
  assert.equal(isSafeBase("not a url"), false);
});

test("header upstream: có secret thì gửi secret, không thì không giả mạo", () => {
  const withSecret = buildUpstreamHeaders({ secret: "s3cret" });
  assert.equal(withSecret["x-scp-proxy-secret"], "s3cret");
  const without = buildUpstreamHeaders({});
  assert.equal(without["x-scp-proxy-secret"], undefined);
});

test("snapshot: gom đủ 4 endpoint từ upstream thật (upstream giả trong test)", async () => {
  const fake = await startFakeUpstream();
  try {
    const snap = await fetchSnapshot(fake.base, { timeoutMs: 2000 });
    assert.equal(snap.upstream.reachable, true);
    assert.equal(snap.health.overall, true);
    assert.equal(snap.status.engine.release, "GA B66");
    assert.equal(snap.metrics.cpuPercent, 12.5);
    assert.equal(snap.activity.summary.latestRunStatus, "ok");
  } finally {
    await close(fake.server);
  }
});

test("snapshot: upstream chết → reachable=false, vẫn trả cấu trúc ổn định", async () => {
  const snap = await fetchSnapshot("http://127.0.0.1:1", { timeoutMs: 500 });
  assert.equal(snap.upstream.reachable, false);
  assert.ok(snap.upstream.error, "phải có mô tả lỗi");
  assert.equal(snap.health, null);
  assert.equal(snap.status, null);
});

test("snapshot gom đủ 5 route (kể cả permissions)", async () => {
  const fake = await startFakeUpstream();
  try {
    const snap = await fetchSnapshot(fake.base, { timeoutMs: 2000 });
    assert.ok(snap.permissions, "snapshot phải có khóa permissions");
    assert.equal(snap.permissions.count, 0);
    assert.equal(snap.upstream.endpoints.permissions.ok, true);
  } finally {
    await close(fake.server);
  }
});

test("máy chủ: GET / trả về index.html", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/`);
    assert.equal(res.status, 200);
    assert.match(res.headers.get("content-type"), /text\/html/);
    const body = await res.text();
    assert.match(body, /officeCanvas/);
  } finally {
    await close(server);
  }
});

test("máy chủ: phương thức ghi bị chặn 405 trên /api/live (fail-closed)", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/snapshot`, { method: "POST" });
    assert.equal(res.status, 405);
    const body = await res.json();
    assert.match(body.error, /chỉ cho phép đọc/i);
  } finally {
    await close(server);
  }
});

test("máy chủ: route ngoài allowlist bị 404 (không open proxy)", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/../../etc/passwd`);
    assert.ok(res.status === 404 || res.status === 400, `status thực tế: ${res.status}`);
  } finally {
    await close(server);
  }
});

test("máy chủ: /api/live/snapshot trả dữ liệu từ upstream được cấu hình", async () => {
  const fake = await startFakeUpstream();
  const previous = process.env.SCP_DASHBOARD_URL;
  process.env.SCP_DASHBOARD_URL = fake.base;
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/snapshot`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.upstream.reachable, true);
    assert.equal(body.health.overall, true);
    assert.ok(body.fetchedAt, "phải có mốc thời gian lấy dữ liệu");
  } finally {
    await close(server);
    await close(fake.server);
    if (previous === undefined) delete process.env.SCP_DASHBOARD_URL;
    else process.env.SCP_DASHBOARD_URL = previous;
  }
});

test("máy chủ: path traversal qua static không thoát khỏi thư mục phục vụ", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/%2e%2e%2f%2e%2e%2fAGENTS.md`);
    assert.ok(res.status === 403 || res.status === 404, `status thực tế: ${res.status}`);
    const body = await res.text();
    // Marker lấy từ Vietnam-Lab/AGENTS.md (nằm NGOÀI thư mục phục vụ).
    assert.ok(
      !body.includes("SCP project guidance"),
      "RÒ RỈ nội dung file ngoài thư mục phục vụ qua path traversal"
    );
  } finally {
    await close(server);
  }
});

test("máy chủ: URL giả mã hỏng KHÔNG được làm chết tiến trình (DoS)", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    // Regression: decodeURIComponent('%ZZ') ném URIError → uncaught exception →
    // process exit. Đã reproduce bằng raw socket trước khi sửa.
    const res = await fetch(`http://127.0.0.1:${port}/%ZZ`);
    assert.ok(res.status >= 400 && res.status < 500, `status thực tế: ${res.status}`);
    // Tiến trình phải còn sống: request kế tiếp vẫn phải trả lời.
    const after = await fetch(`http://127.0.0.1:${port}/`);
    assert.equal(after.status, 200, "server phải còn sống sau request URL hỏng");
    assert.ok(!server.listening === false, "server vẫn lắng nghe");
  } finally {
    await close(server);
  }
});

test("proxy: upstream treo (timeout) → snapshot fail-closed, không treo", async () => {
  const hang = http.createServer(() => {
    /* cố ý không trả lời */
  });
  await new Promise((resolve) => hang.listen(0, "127.0.0.1", resolve));
  const { port } = hang.address();
  try {
    const snap = await fetchSnapshot(`http://127.0.0.1:${port}`, { timeoutMs: 400 });
    assert.equal(snap.upstream.reachable, false, "upstream treo phải bị coi là không quan sát được");
    assert.ok(snap.upstream.error, "phải có mô tả lỗi");
    assert.equal(snap.health, null);
  } finally {
    await new Promise((resolve) => hang.close(() => resolve()));
  }
});

test("proxy: phản hồi quá lớn bị cắt, không trả về payload khổng lồ", async () => {
  const big = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ blob: "x".repeat(5 * 1024 * 1024) }));
  });
  await new Promise((resolve) => big.listen(0, "127.0.0.1", resolve));
  const { port } = big.address();
  try {
    const result = await fetchUpstream(`http://127.0.0.1:${port}`, "status", { timeoutMs: 5000 });
    const size = result.body ? JSON.stringify(result.body).length : 0;
    assert.ok(size <= 4 * 1024 * 1024, `payload phải ≤ 4MB, thực tế ${size}`);
    // JSON bị cắt nên không parse được → không được coi là dữ liệu hợp lệ.
    assert.equal(result.ok, false);
  } finally {
    await new Promise((resolve) => big.close(() => resolve()));
  }
});

test("CORS: origin dashboard được phép đọc kênh runtime", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/snapshot`, {
      headers: { Origin: "http://127.0.0.1:3000" },
    });
    assert.equal(res.headers.get("access-control-allow-origin"), "http://127.0.0.1:3000");
    assert.match(res.headers.get("vary") || "", /Origin/i);
  } finally {
    await close(server);
  }
});

test("CORS: origin lạ KHÔNG được cấp quyền đọc (không dùng ACAO='*')", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/snapshot`, {
      headers: { Origin: "https://evil.example.com" },
    });
    assert.equal(
      res.headers.get("access-control-allow-origin"),
      null,
      "origin lạ không được phép đọc dữ liệu runtime"
    );
    assert.notEqual(res.headers.get("access-control-allow-origin"), "*");
  } finally {
    await close(server);
  }
});

test("CORS: preflight từ origin lạ bị từ chối 403", async () => {
  const server = createServer();
  const port = await listen(server);
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/live/snapshot`, {
      method: "OPTIONS",
      headers: { Origin: "https://evil.example.com" },
    });
    assert.equal(res.status, 403);
  } finally {
    await close(server);
  }
});
