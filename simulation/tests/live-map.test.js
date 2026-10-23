/**
 * live-map.test.js — Kiểm chứng lớp ánh xạ dữ liệu runtime (DNA #4 / FA-04).
 *
 * Khoá chính: KHÔNG BAO GIỜ biến "không biết" thành "đang tốt", và không bao
 * giờ sinh số liệu giả khi payload rỗng/hỏng (anti-placebo).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const map = require(path.join(__dirname, "..", "scp-office", "js", "live-map.js"));
const { ROOMS } = require(path.join(__dirname, "..", "scp-office", "js", "data.js"));

const HEALTHY = {
  scp: "online",
  overall: true,
  fastapi: { ok: true, latencyMs: 12, status: 200 },
  loopScheduler: { ok: true, latencyMs: 8, status: 200 },
  agentDesktop: { ok: true, latencyMs: 30, status: 200 },
  llmBridge: { ok: true, latencyMs: 0, status: 200 },
  checkedAt: "2026-10-22T10:00:00.000Z",
};

test("payload rỗng → mọi thứ unknown, không phải ok (anti-placebo)", () => {
  const d = map.deriveRuntimeState({}, ROOMS);
  assert.equal(d.mode, "MOCK");
  assert.equal(d.overall.state, "unknown");
  assert.equal(d.ports.live, null, "không quan sát được thì ports phải null, không phải 0");
  assert.equal(d.needsReview.count, null);
  for (const room of ROOMS) {
    assert.equal(d.rooms[room.id].state, "unknown", `phòng ${room.id} phải unknown`);
  }
});

test("payload hỏng (null) không gây crash", () => {
  const d = map.deriveRuntimeState(null, ROOMS);
  assert.equal(d.mode, "MOCK");
  assert.equal(d.overall.state, "unknown");
});

test("health đầy đủ → overall ok và ports live = 4 (dashboard + 3 dịch vụ được probe)", () => {
  const d = map.deriveRuntimeState({ health: HEALTHY, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.mode, "LIVE");
  assert.equal(d.isLive, true);
  assert.equal(d.overall.state, "ok");
  // Cổng :8081 (llmBridge) đã bị xoá khỏi kiến trúc → KHÔNG tính là cổng.
  assert.equal(d.ports.live, 4);
  assert.equal(d.ports.total, 4);
});

test("mục llmBridge TỔNG HỢP (direct) không được coi là quan sát được", () => {
  // Route /api/scp/health trả ok:true + latencyMs:0 + hint "Direct API-First
  // Gateway" — đây là HẰNG SỐ của kiến trúc Zero-Bun, không phải quan sát.
  const d = map.deriveRuntimeState({ health: HEALTHY, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.gateway_room.state, "unknown");
  assert.match(d.rooms.gateway_room.detail, /không còn cổng :8081/i);
});

test("llmBridge thật (có latency, không phải direct) vẫn được quan sát", () => {
  const health = structuredClone(HEALTHY);
  health.llmBridge = { ok: true, latencyMs: 7, status: 200, hint: "probe thật" };
  const d = map.deriveRuntimeState({ health, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.ports.live, 4, "vẫn chỉ 4 cổng thật (llmBridge không phải cổng)");
});

test("một dịch vụ lỗi → phòng tương ứng fail, overall không còn ok", () => {
  const health = structuredClone(HEALTHY);
  health.fastapi = { ok: false, latencyMs: 5000, status: 500 };
  const d = map.deriveRuntimeState({ health, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.reception.state, "fail");
  assert.notEqual(d.overall.state, "ok");
  assert.equal(d.ports.live, 3, "dashboard + 2 dịch vụ còn lại");
});

test("thiếu trường ok trong health → phòng đó unknown, không fail-open", () => {
  const health = structuredClone(HEALTHY);
  delete health.fastapi.ok;
  const d = map.deriveRuntimeState({ health, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.reception.state, "unknown");
});

test("một tín hiệu OK + một tín hiệu UNKNOWN → phòng vẫn OK (đã quan sát được)", () => {
  // health.loopScheduler.ok = true nhưng activity chưa có chu kỳ nào →
  // phòng loop đã quan sát được dịch vụ đang sống ⇒ OK, không phải UNKNOWN.
  const health = structuredClone(HEALTHY);
  const activity = {
    summary: { totalRunsRecorded: 0, latestRunStatus: "chưa có", latestFindingsCount: 0, latestFixesCount: 0 },
    runs: [],
    events: [],
  };
  const d = map.deriveRuntimeState({ health, activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.loop_sandbox.state, "ok");
  // Vòng release vẫn UNKNOWN vì KHÔNG có tín hiệu dương nào.
  assert.equal(d.rooms.release_ci.state, "unknown");
});

test("một tín hiệu FAIL thắng mọi tín hiệu OK khác của cùng phòng", () => {
  const health = structuredClone(HEALTHY);
  health.loopScheduler = { ok: false, latencyMs: 5000, status: 500 };
  const d = map.deriveRuntimeState({ health, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.loop_sandbox.state, "fail");
});

test("hàng đợi duyệt: đọc được permissions thì lấy count; không đọc được thì null (KHÔNG phải 0)", () => {
  const withPerm = map.deriveRuntimeState(
    { permissions: { pending: [], count: 3 }, upstream: { reachable: true } },
    ROOMS
  );
  assert.equal(withPerm.pendingApproval, 3);

  const withoutPerm = map.deriveRuntimeState({ upstream: { reachable: true } }, ROOMS);
  assert.equal(
    withoutPerm.pendingApproval,
    null,
    "không đọc được hàng đợi thì phải null, KHÔNG phải 0"
  );
});

test("HEALTHY gốc không bị test khác làm bẩn (deep clone)", () => {
  assert.equal(HEALTHY.fastapi.ok, true, "HEALTHY phải nguyên vẹn sau các test khác");
  assert.equal(HEALTHY.overall, true);
  assert.equal(HEALTHY.loopScheduler.ok, true);
  assert.equal(HEALTHY.agentDesktop.ok, true);
  assert.equal(HEALTHY.llmBridge.ok, true);
});

test("upstream không reachable → mode STALE, ports null, không kết luận down", () => {
  const d = map.deriveRuntimeState(
    { health: HEALTHY, upstream: { reachable: false, error: "ECONNREFUSED" } },
    ROOMS
  );
  assert.equal(d.mode, "STALE");
  assert.equal(d.ports.live, null);
  assert.equal(d.overall.state, "unknown");
});

test("activity sinh ra chi tiết cho phòng release_ci", () => {
  const activity = {
    summary: { latestRunStatus: "ok", latestFindingsCount: 2, latestFixesCount: 1, totalRunsRecorded: 7 },
    events: [
      { id: "1", timestamp: "2026-10-22T10:00:00.000Z", type: "loop", service: "loop-scheduler", status: "error", message: "boom" },
      { id: "2", timestamp: "2026-10-22T09:59:00.000Z", type: "log", service: "scheduler", status: "warning", message: "warn" },
    ],
  };
  const d = map.deriveRuntimeState({ activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.release_ci.state, "warn");
  assert.equal(d.needsReview.count, 2, "1 lỗi + 1 cảnh báo");
  assert.equal(d.counters.latestRunStatus, "ok");
});

test("phòng không có tín hiệu runtime → unknown kèm lý do", () => {
  const d = map.deriveRuntimeState({ health: HEALTHY, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.cap_mcp_room.state, "unknown");
  assert.match(d.rooms.cap_mcp_room.detail, /chưa có tín hiệu/i);
});

test("CHƯA TỪNG có chu kỳ loop → release_ci KHÔNG được gắn OK (anti-placebo)", () => {
  // Dashboard thật trả về đúng payload này khi loop chưa từng chạy:
  // totalRunsRecorded = 0, latestRunStatus = "chưa có", counts = 0.
  const activity = {
    summary: {
      totalRunsRecorded: 0,
      latestRunStatus: "chưa có",
      latestFindingsCount: 0,
      latestFixesCount: 0,
    },
    runs: [],
    events: [],
  };
  const d = map.deriveRuntimeState({ activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(
    d.rooms.release_ci.state,
    "unknown",
    "0 phát hiện khi chưa từng chạy KHÔNG được hiển thị là LIVE-OK"
  );
  assert.equal(d.rooms.loop_sandbox.state, "unknown", '"chưa có" không phải lỗi');
  assert.equal(d.rooms.kernel_room.state, "unknown");
});

test("có chu kỳ loop thật + 0 phát hiện → mới được phép là OK", () => {
  const activity = {
    summary: { totalRunsRecorded: 7, latestRunStatus: "ok", latestFindingsCount: 0, latestFixesCount: 0 },
    runs: [],
    events: [],
  };
  const d = map.deriveRuntimeState({ activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.release_ci.state, "ok");
});

test("trạng thái chu kỳ lạ (không phải lỗi) → UNKNOWN chứ không phải FAIL", () => {
  const activity = {
    summary: { totalRunsRecorded: 3, latestRunStatus: "đang chờ duyệt", latestFindingsCount: 0, latestFixesCount: 0 },
    runs: [],
    events: [],
  };
  const d = map.deriveRuntimeState({ activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.loop_sandbox.state, "unknown");
});

test("trạng thái chu kỳ lỗi đã biết → FAIL", () => {
  const activity = {
    summary: { totalRunsRecorded: 3, latestRunStatus: "error", latestFindingsCount: 1, latestFixesCount: 0 },
    runs: [],
    events: [],
  };
  const d = map.deriveRuntimeState({ activity, upstream: { reachable: true } }, ROOMS);
  assert.equal(d.rooms.loop_sandbox.state, "fail");
});

test("metadata engine/audit không được hiện khi dữ liệu đã STALE", () => {
  const d = map.deriveRuntimeState(
    {
      status: { engine: { release: "GA B66", v4Loc: 4333, v4LocLive: true }, audit: { auditRound: 42 } },
      upstream: { reachable: false },
    },
    ROOMS
  );
  assert.equal(d.mode, "STALE");
  assert.equal(d.counters.engineRelease, null, "số liệu cũ không được hiện như hiện hành");
  assert.equal(d.counters.auditRound, null);
  assert.equal(d.counters.v4Loc, null);
});

test("metrics không có → cpu/memory null thay vì 0", () => {
  const d = map.deriveRuntimeState({ upstream: { reachable: true } }, ROOMS);
  assert.equal(d.counters.cpuPercent, null);
  assert.equal(d.counters.memoryPercent, null);
});

test("readPath không văng lỗi với đường dẫn không tồn tại", () => {
  assert.equal(map.readPath({ a: { b: 1 } }, "a.b"), 1);
  assert.equal(map.readPath({ a: { b: 1 } }, "a.c"), null);
  assert.equal(map.readPath(null, "a"), null);
});
