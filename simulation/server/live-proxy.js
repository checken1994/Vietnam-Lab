/**
 * live-proxy.js — Tầng trung gian chỉ đọc giữa trang mô phỏng và dashboard SCP.
 *
 * HỢP ĐỒNG BẢO MẬT (fail-closed):
 *  1. CHỈ cho phép GET/HEAD. Mọi phương thức ghi bị từ chối (405).
 *  2. KHÔNG phải open proxy: chỉ 4 đường dẫn upstream cố định được phép.
 *     Không nhận URL đích từ phía trình duyệt.
 *  3. Upstream chỉ lấy từ biến môi trường (mặc định http://127.0.0.1:3000).
 *  4. Timeout cứng + giới hạn kích thước phản hồi.
 *  5. Không bao giờ trả lỗi/nội dung upstream cho bên thứ ba khác ngoài cùng máy.
 *
 * Module thuần về logic (không cần DOM) → kiểm thử được bằng node --test.
 */
"use strict";

/** Bảng đường dẫn upstream được phép. KHÔNG thêm mục mới mà không có nhu cầu thật. */
const UPSTREAM_ROUTES = {
  status: "/api/scp/status",
  health: "/api/scp/health",
  activity: "/api/scp/activity",
  metrics: "/api/scp/metrics",
  // Hàng đợi duyệt của con người. Route này của dashboard tự chèn credential
  // admin phía server (injectServiceAuth) nên bản đồ đọc được mà không cần giữ
  // secret trong trình duyệt. Nếu dashboard ở posture "trusted proxy" (không
  // chèn credential) ⇒ backend trả 401 ⇒ bản đồ hiện "không đọc được" (đúng).
  permissions: "/api/scp/permissions",
};

const DEFAULT_TIMEOUT_MS = 4000;
const MAX_BYTES = 4 * 1024 * 1024; // 4 MB

/** Tên route live hợp lệ (kể cả route tổng hợp). */
function isAllowedLiveRoute(name) {
  if (name === "snapshot") return true;
  return Object.prototype.hasOwnProperty.call(UPSTREAM_ROUTES, name);
}

/**
 * Header gửi lên upstream.
 * - Nếu có `x-scp-proxy-secret` → dùng đúng secret của dashboard (chế độ production).
 * - Nếu không có → gửi X-Forwarded-For loopback để khớp nhánh dev-mode
 *   (dashboard chỉ chấp nhận khi SCP_DEV_MODE=1; nếu không, dashboard tự fail-closed 503).
 */
function buildUpstreamHeaders(options) {
  const opts = options || {};
  const headers = { Accept: "application/json", "X-Forwarded-For": "127.0.0.1" };
  if (opts.secret) headers["x-scp-proxy-secret"] = opts.secret;
  return headers;
}

/** Lấy base upstream từ env (không nhận từ request). */
function resolveUpstreamBase(env) {
  const e = env || process.env;
  const raw = (e.SCP_DASHBOARD_URL || "http://127.0.0.1:3000").trim();
  return raw.replace(/\/+$/, "");
}

/** Bảo đảm base là http(s)://host[:port] hợp lệ — chặn scheme lạ (file:, javascript:...). */
function isSafeBase(base) {
  try {
    const url = new URL(base);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch (e) {
    return false;
  }
}

/**
 * Gọi 1 endpoint upstream. Không bao giờ throw — luôn trả về kết quả có cấu trúc
 * để lớp trên biết chính xác "quan sát được" hay "không quan sát được".
 */
async function fetchUpstream(base, routeName, options) {
  const opts = options || {};
  const upstreamPath = UPSTREAM_ROUTES[routeName];
  if (!upstreamPath) {
    return { ok: false, status: 0, body: null, error: `route không được phép: ${routeName}`, elapsedMs: 0 };
  }
  if (!isSafeBase(base)) {
    return { ok: false, status: 0, body: null, error: `upstream base không hợp lệ: ${base}`, elapsedMs: 0 };
  }

  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs || DEFAULT_TIMEOUT_MS);
  try {
    const res = await fetch(`${base}${upstreamPath}`, {
      method: "GET",
      headers: buildUpstreamHeaders(opts),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await readCapped(res);
    let body = null;
    try {
      body = JSON.parse(text);
    } catch (e) {
      body = null;
    }
    return {
      ok: res.ok && body !== null,
      status: res.status,
      body,
      error: body === null ? "phản hồi upstream không phải JSON" : null,
      elapsedMs: Date.now() - started,
    };
  } catch (err) {
    const message = err && err.name === "AbortError" ? "timeout khi đọc upstream" : err && err.message ? err.message : String(err);
    return { ok: false, status: 0, body: null, error: message, elapsedMs: Date.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

/** Đọc body nhưng dừng nếu vượt giới hạn kích thước (chống response khổng lồ). */
async function readCapped(res) {
  if (!res.body || typeof res.text !== "function") return "";
  const reader = res.body.getReader ? res.body.getReader() : null;
  if (!reader) {
    const text = await res.text();
    return text.length > MAX_BYTES ? text.slice(0, MAX_BYTES) : text;
  }
  const chunks = [];
  let total = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > MAX_BYTES) {
      await reader.cancel().catch(() => {});
      break;
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Gom snapshot từ 4 endpoint upstream (song song).
 * Kết quả luôn có cấu trúc ổn định kể cả khi toàn bộ upstream chết.
 */
async function fetchSnapshot(base, options) {
  const opts = options || {};
  const names = Object.keys(UPSTREAM_ROUTES);
  const results = await Promise.all(names.map((name) => fetchUpstream(base, name, opts)));

  const payload = { status: null, health: null, activity: null, metrics: null, permissions: null };
  const endpoints = {};
  let reachable = false;
  let firstError = null;

  names.forEach((name, i) => {
    const r = results[i];
    endpoints[name] = {
      ok: r.ok,
      status: r.status,
      error: r.error,
      elapsedMs: r.elapsedMs,
      path: UPSTREAM_ROUTES[name],
    };
    if (r.body !== null) {
      payload[name] = r.body;
      reachable = true;
    } else if (!firstError) {
      firstError = `${name}: ${r.error}`;
    }
  });

  return {
    fetchedAt: new Date().toISOString(),
    upstream: {
      base,
      reachable,
      error: reachable ? null : firstError || "không đọc được upstream",
      endpoints,
    },
    status: payload.status,
    health: payload.health,
    activity: payload.activity,
    metrics: payload.metrics,
    permissions: payload.permissions,
    errors: reachable ? {} : { upstream: firstError || "không đọc được upstream" },
  };
}

module.exports = {
  UPSTREAM_ROUTES,
  DEFAULT_TIMEOUT_MS,
  isAllowedLiveRoute,
  buildUpstreamHeaders,
  resolveUpstreamBase,
  isSafeBase,
  fetchUpstream,
  fetchSnapshot,
};
