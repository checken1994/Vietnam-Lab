/**
 * sync.test.js — Chống drift giữa nguồn (`simulation/scp-office/`) và bản phục vụ
 * tại dashboard (`dashboard/public/scp-office/`).
 *
 * Bản trong dashboard/public là BẢN SAO. Nếu ai đó sửa nguồn mà quên đồng bộ,
 * người dùng mở :3000/scp-office/ sẽ thấy phiên bản cũ — bài test này chặn
 * tình huống đó (bằng chứng thật, không phải placebo).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { SRC, DST, compare, listFiles } = require(path.join(__dirname, "..", "scripts", "sync-dashboard.js"));

test("bản trong dashboard/public/scp-office khớp đúng với nguồn (không drift)", () => {
  assert.ok(fs.existsSync(SRC), `thiếu thư mục nguồn: ${SRC}`);
  assert.ok(
    fs.existsSync(DST),
    `thiếu bản phục vụ: ${DST} — chạy "npm run sync:dashboard" trong thư mục simulation/`
  );
  const diff = compare(SRC, DST);
  assert.deepEqual(
    { missing: diff.missing, extra: diff.extra, changed: diff.changed },
    { missing: [], extra: [], changed: [] },
    `drift giữa nguồn và bản dashboard — chạy: npm run sync:dashboard (thiếu ${diff.missing.join(", ") || "-"}; khác ${diff.changed.join(", ") || "-"}; thừa ${diff.extra.join(", ") || "-"})`
  );
});

test("bản phục vụ có đủ các tài nguyên chính của trang", () => {
  const files = listFiles(DST);
  for (const required of [
    "index.html",
    "css/styles.css",
    "js/data.js",
    "js/live-map.js",
    "js/live.js",
    "js/sim.js",
    "js/render.js",
    "js/audio.js",
    "js/ui.js",
    "js/app.js",
  ]) {
    assert.ok(files.includes(required), `bản phục vụ thiếu: ${required}`);
  }
});
