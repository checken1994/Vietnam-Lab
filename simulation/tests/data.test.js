/**
 * data.test.js — Hợp đồng dữ liệu bản đồ.
 *
 * Bản đồ có 24 "nhân vật" — nhưng chúng là các THÀNH PHẦN THẬT của repo
 * (tên, skill, vai trò, đường dẫn đều thật). Điều bị cấm là dữ liệu GIẢ:
 * nhiệm vụ bịa, đồng hồ đếm ngược, tiến trình giả, kho tác vụ sinh tự động.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const JS_DIR = path.join(__dirname, "..", "scp-office", "js");
const data = require(path.join(JS_DIR, "data.js"));
const Sim = require(path.join(JS_DIR, "sim.js"));

test("có đúng 12 hệ thống", () => {
  assert.equal(data.ROOMS.length, 12);
});

test("mỗi hệ thống có id/codeName/path/row/col hợp lệ và không trùng", () => {
  const ids = new Set();
  const codeNames = new Set();
  for (const room of data.ROOMS) {
    assert.ok(room.id && typeof room.id === "string", "room.id phải là chuỗi");
    assert.ok(!ids.has(room.id), `room.id trùng: ${room.id}`);
    ids.add(room.id);
    assert.ok(!codeNames.has(room.codeName), `room.codeName trùng: ${room.codeName}`);
    codeNames.add(room.codeName);
    assert.ok(room.row >= 0 && room.row < 3, `row ngoài lưới 3 hàng: ${room.id}`);
    assert.ok(room.col >= 0 && room.col < 4, `col ngoài lưới 4 cột: ${room.id}`);
    assert.ok(room.path && typeof room.path === "string", `thiếu path: ${room.id}`);
    assert.ok(room.liveLabel && typeof room.liveLabel === "string", `thiếu liveLabel: ${room.id}`);
  }
});

test("mỗi hệ thống chiếm đúng một ô của lưới 3×4", () => {
  const cells = new Set();
  for (const room of data.ROOMS) {
    const key = `${room.row}:${room.col}`;
    assert.ok(!cells.has(key), `hai phòng trùng ô ${key}`);
    cells.add(key);
  }
  assert.equal(cells.size, 12);
});

test("có 24 thành phần (nhân vật) và mọi cái đều là thành phần THẬT", () => {
  assert.equal(data.AGENTS.length, 24);
  const ids = new Set();
  const roomIds = new Set(data.ROOMS.map((r) => r.id));
  for (const a of data.AGENTS) {
    assert.ok(!ids.has(a.id), `agent.id trùng: ${a.id}`);
    ids.add(a.id);
    assert.ok(roomIds.has(a.roomId), `agent.roomId không tồn tại: ${a.roomId}`);
    assert.ok(data.ACCOUNTS[a.account], `account không hợp lệ: ${a.account}`);
    // Thông tin phải là thật: tên, skill/module, vai trò, mô tả.
    assert.ok(a.name && a.name.length > 2, `thiếu tên: ${a.id}`);
    assert.ok(a.skill && a.skill.length > 2, `thiếu skill/module: ${a.id}`);
    assert.ok(a.role && a.role.length > 5, `thiếu vai trò: ${a.id}`);
    assert.ok(a.desc && a.desc.length > 20, `thiếu mô tả: ${a.id}`);
    // Từ khoá khớp sự kiện runtime (dùng để biết nhân vật nào đang bận).
    assert.ok(Array.isArray(a.match) && a.match.length > 0, `thiếu từ khoá match: ${a.id}`);
  }
});

test("mọi hệ thống đều có ít nhất một thành phần", () => {
  for (const room of data.ROOMS) {
    const n = data.AGENTS.filter((a) => a.roomId === room.id).length;
    assert.ok(n >= 1, `hệ thống ${room.id} không có thành phần nào`);
  }
});

test("KHÔNG còn trường dữ liệu GIẢ trên nhân vật (task/timer/speedMult/status)", () => {
  const forbidden = ["task", "taskIcon", "timer", "speedMult", "status"];
  for (const a of data.AGENTS) {
    for (const key of forbidden) {
      assert.equal(a[key], undefined, `agent.${key} là dữ liệu giả: ${a.id}`);
    }
  }
  // Không còn kho tác vụ sinh tự động (đó là nguồn của chuyển động giả).
  assert.equal(data.TASK_POOL, undefined, "không được có TASK_POOL (nguồn tác vụ giả)");
  assert.equal(data.PRESET_TASKS, undefined, "không được có PRESET_TASKS");
  assert.equal(data.SCENARIO_CARDS, undefined, "không được có thẻ số liệu kịch bản");
});

test("không có số liệu mô phỏng bịa trong giao diện (76/76, 34%, 0.299ms, 2120 APIs…)", () => {
  const files = [
    path.join(JS_DIR, "data.js"),
    path.join(JS_DIR, "live-map.js"),
    path.join(JS_DIR, "ui.js"),
    path.join(JS_DIR, "app.js"),
    path.join(JS_DIR, "render.js"),
    path.join(JS_DIR, "sim.js"),
    path.join(__dirname, "..", "scp-office", "index.html"),
  ];
  const banned = ["76/76", "18/18", "0.299ms", "2120 API", "34%", "38%", "29 Nguyên tắc", "Gemini"];
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    for (const pattern of banned) {
      assert.ok(!text.includes(pattern), `${path.basename(file)} còn chứa số liệu bịa: "${pattern}"`);
    }
  }
});

test("chuyển động CHỉ xảy ra khi có sự kiện runtime THẬT", () => {
  const sim = Sim.createSimulation({
    rooms: data.ROOMS,
    agents: data.AGENTS,
    serviceRooms: data.SERVICE_ROOMS,
    random: () => 0.5,
  });

  const loopMaster = data.AGENTS.find((a) => a.id === "loop_master");
  const before = { ...sim.agents3d[loopMaster.id] };

  // 1) Sự kiện KHÔNG liên quan ⇒ không ai di chuyển, không ai có bong bóng.
  sim.applyLiveEvents([
    { service: "khong-liên-quan", message: "một dòng log hoàn toàn không liên quan" },
  ]);
  assert.equal(sim.agents3d[loopMaster.id].waypoints.length, 0, "không được đi khi sự kiện không liên quan");
  assert.equal(sim.agents3d[loopMaster.id].bubble, null, "không được có bong bóng khi không có sự kiện liên quan");
  assert.equal(sim.agents3d[loopMaster.id].x, before.x, "vị trí không đổi khi không có sự kiện liên quan");

  // 2) Sự kiện THẬT khớp ⇒ thành phần liên quan đi tới phòng của dịch vụ đó.
  sim.applyLiveEvents([
    { service: "loop-scheduler", message: "[loop-scheduler] Chu kỳ Tự động: ok trong 5019ms" },
  ]);
  const s = sim.agents3d[loopMaster.id];
  assert.ok(s.waypoints.length > 0, "phải có lộ trình khi có sự kiện runtime liên quan");
  assert.ok(s.bubble && s.bubble.includes("Chu kỳ"), "bong bóng phải hiện thông điệp sự kiện THẬT");
  assert.equal(s.busy, true, "phải đánh dấu đang có hoạt động");
});

test("khớp thành phần theo từ khoá: sự kiện desktop chỉ kích hoạt nhân vật desktop", () => {
  const sim = Sim.createSimulation({
    rooms: data.ROOMS,
    agents: data.AGENTS,
    serviceRooms: data.SERVICE_ROOMS,
    random: () => 0.5,
  });
  const matched = sim.matchAgents({ service: "desktop", message: "Chromium crashed" });
  const ids = matched.map((a) => a.id);
  assert.ok(ids.includes("desktop_op"), "desktop_op phải khớp sự kiện desktop");
  assert.ok(!ids.includes("nova_ideas"), "nhân vật không liên quan không được khớp");
});

test("service khớp CHÍNH XÁC thắng — không dùng từ khoá chung", () => {
  const sim = Sim.createSimulation({
    rooms: data.ROOMS,
    agents: data.AGENTS,
    serviceRooms: data.SERVICE_ROOMS,
    random: () => 0.5,
  });
  // Dòng log thật của container desktop chứa "kernel"/"next" trong đường dẫn
  // node_modules — KHÔNG được làm bật TaskKernel Master hay Dashboard Architect.
  const noise = {
    service: "desktop",
    message:
      "once the app is packaged.\\n[2026-09-20T19:58:09.879Z] level=warning " +
      "node:internal/modules/cjs/loader:1405 nextTick next",
  };
  const ids = sim.matchAgents(noise).map((a) => a.id);
  assert.ok(!ids.includes("kernel_k"), "kernel_k không được bật bởi log chứa từ 'kernel'");
  assert.ok(!ids.includes("architect_c"), "architect_c không được bật bởi log chứa từ 'next'");
  assert.ok(ids.includes("desktop_op"), "desktop_op vẫn phải khớp (service desktop)");
});

test("mọi thành phần có service đều khai báo service nằm trong tập service quan sát được", () => {
  const observed = new Set(["loop-scheduler", "scheduler", "desktop"]);
  for (const a of data.AGENTS) {
    if (a.service) {
      assert.ok(observed.has(a.service), `service chưa quan sát được: ${a.service} (${a.id})`);
    }
  }
});

test("không dùng từ khoá quá chung (dễ bắt nhầm trong log)", () => {
  const generic = ["kernel", "next", "health", "audit", "token", "policy", "dom", "gateway", "release", "idea"];
  for (const a of data.AGENTS) {
    for (const kw of a.match || []) {
      assert.ok(
        !generic.includes(kw.toLowerCase()),
        `${a.id} dùng từ khoá quá chung "${kw}" — dễ bắt nhầm trong log`
      );
    }
  }
});

test("mọi hệ thống có tín hiệu runtime khai báo đúng định dạng", () => {
  for (const room of data.ROOMS) {
    assert.ok(Array.isArray(room.liveSignals), `room.liveSignals phải là mảng: ${room.id}`);
    for (const sig of room.liveSignals) {
      assert.match(sig, /^[a-zA-Z._]+$/, `đường dẫn tín hiệu không hợp lệ: ${sig}`);
    }
  }
  // Hệ thống không có tín hiệu phải nêu rõ lý do UNKNOWN.
  for (const room of data.ROOMS.filter((r) => r.liveSignals.length === 0)) {
    assert.match(
      room.liveLabel,
      /chưa có tín hiệu|không còn cổng|không quan sát/i,
      `phải nêu rõ lý do UNKNOWN: ${room.id}`
    );
  }
});
