/**
 * syntax.test.js — Kiểm tra cú pháp của MỌI file JS giao diện.
 *
 * [FIX · 2026-10-22] Một lần sửa render.js khai báo trùng `const live` trong cùng
 * hàm ⇒ SyntaxError ⇒ toàn bộ file không parse ⇒ TRANG TRẮNG và badge kẹt ở
 * trạng thái khởi tạo, KHÔNG có lỗi nào hiện ra ngoài console. Bộ test cũ không
 * bắt được vì các file này chạy trong trình duyệt, không phải trong Node.
 * Bài test này chạy `node --check` cho từng file để lỗi cú pháp bị chặn trước
 * khi tới trình duyệt.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const JS_DIR = path.join(__dirname, "..", "scp-office", "js");

function jsFiles(dir) {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile() && e.name.endsWith(".js"))
    .map((e) => path.join(dir, e.name));
}

test("mọi file JS giao diện parse được (node --check)", () => {
  const files = jsFiles(JS_DIR);
  assert.ok(files.length >= 6, `cần ít nhất 6 file JS, thấy ${files.length}`);
  for (const file of files) {
    try {
      execFileSync(process.execPath, ["--check", file], { stdio: "pipe" });
    } catch (err) {
      const stderr = err.stderr ? err.stderr.toString() : String(err);
      assert.fail(`SyntaxError trong ${path.basename(file)}:\n${stderr.split("\n").slice(0, 6).join("\n")}`);
    }
  }
});

test("các module dùng chung phải theo mẫu UMD (chạy được cả trong Node lẫn trình duyệt)", () => {
  // app.js là entry point của trình duyệt (IIFE thuần) — không phải module.
  for (const file of jsFiles(JS_DIR).filter((f) => path.basename(f) !== "app.js")) {
    const text = fs.readFileSync(file, "utf8");
    assert.ok(
      text.includes("module.exports") && text.includes("root."),
      `${path.basename(file)} phải dùng mẫu UMD để require() được trong Node`
    );
  }
});

test("mọi id DOM mà JS tham chiếu đều tồn tại trong index.html", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "scp-office", "index.html"), "utf8");
  const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  for (const file of jsFiles(JS_DIR)) {
    const text = fs.readFileSync(file, "utf8");
    const used = new Set([...text.matchAll(/byId\("([^"]+)"\)|getElementById\("([^"]+)"\)/g)].map((m) => m[1] || m[2]));
    for (const id of used) {
      assert.ok(htmlIds.has(id), `${path.basename(file)} tham chiếu #${id} không có trong index.html`);
    }
  }
});

test("mọi file script trong index.html đều tồn tại trong thư mục js/", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "scp-office", "index.html"), "utf8");
  const present = new Set(jsFiles(JS_DIR).map((f) => path.basename(f)));
  for (const m of html.matchAll(/<script src="([^"]+)"/g)) {
    const name = path.basename(m[1]);
    assert.ok(present.has(name), `index.html tham chiếu ${m[1]} nhưng file không tồn tại`);
  }
});
