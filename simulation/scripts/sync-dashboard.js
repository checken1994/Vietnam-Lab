#!/usr/bin/env node
/**
 * sync-dashboard.js — Đồng bộ bản đồ mô phỏng sang `dashboard/public/scp-office/`
 * để dashboard Next.js phục vụ nó tại đường dẫn /scp-office/ (tĩnh, không cần build).
 *
 * Nguồn sự thật: `simulation/scp-office/` (thư mục này).
 * Bản trong `dashboard/public/` là BẢN SAO — luôn phải khớp với nguồn.
 * `tests/sync.test.js` kiểm tra việc này để phát hiện drift.
 *
 * Dùng:
 *   node scripts/sync-dashboard.js          # đồng bộ (chỉ ghi file khác biệt)
 *   node scripts/sync-dashboard.js --check  # chỉ kiểm tra, exit 1 nếu lệch
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "scp-office");
const DST = path.join(ROOT, "..", "dashboard", "public", "scp-office");

/** Liệt kê file (đường dẫn tương đối) của một cây thư mục. */
function listFiles(dir, base) {
  const out = [];
  const root = base || dir;
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...listFiles(abs, root));
    } else if (entry.isFile()) {
      out.push(path.relative(root, abs).split(path.sep).join("/"));
    }
  }
  return out;
}

function hashFile(abs) {
  return crypto.createHash("sha256").update(fs.readFileSync(abs)).digest("hex");
}

/**
 * So sánh cây nguồn với cây đích.
 * @returns {{missing: string[], extra: string[], changed: string[]}}
 */
function compare(srcDir, dstDir) {
  const missing = [];
  const extra = [];
  const changed = [];
  const srcFiles = listFiles(srcDir);
  const dstFiles = new Set(listFiles(dstDir));

  for (const rel of srcFiles) {
    const srcAbs = path.join(srcDir, rel);
    const dstAbs = path.join(dstDir, rel);
    if (!fs.existsSync(dstAbs)) {
      missing.push(rel);
    } else if (hashFile(srcAbs) !== hashFile(dstAbs)) {
      changed.push(rel);
    }
  }
  for (const rel of dstFiles) {
    if (!srcFiles.includes(rel)) extra.push(rel);
  }
  return { missing, extra, changed };
}

/** Đồng bộ: ghi lại file khác biệt, xoá file thừa trong bản đích. */
function sync(srcDir, dstDir) {
  const result = compare(srcDir, dstDir);
  const written = [];
  const removed = [];

  fs.mkdirSync(dstDir, { recursive: true });
  for (const rel of [...result.missing, ...result.changed]) {
    const srcAbs = path.join(srcDir, rel);
    const dstAbs = path.join(dstDir, rel);
    fs.mkdirSync(path.dirname(dstAbs), { recursive: true });
    fs.copyFileSync(srcAbs, dstAbs);
    written.push(rel);
  }
  for (const rel of result.extra) {
    const dstAbs = path.join(dstDir, rel);
    if (fs.existsSync(dstAbs)) {
      fs.rmSync(dstAbs);
      removed.push(rel);
    }
  }
  return { written, removed };
}

function main() {
  const checkOnly = process.argv.includes("--check");
  if (!fs.existsSync(SRC)) {
    process.stderr.write(`Không tìm thấy nguồn: ${SRC}\n`);
    process.exit(2);
  }
  const result = compare(SRC, DST);
  const drift = result.missing.length + result.extra.length + result.changed.length;

  if (checkOnly) {
    if (drift === 0) {
      process.stdout.write(`OK — bản dashboard/public/scp-office khớp nguồn (${listFiles(SRC).length} file).\n`);
      process.exit(0);
    }
    process.stderr.write(
      `LỆCH so với nguồn (${drift} file). Chạy: npm run sync:dashboard\n` +
        `  thiếu: ${result.missing.join(", ") || "-"}\n` +
        `  khác:  ${result.changed.join(", ") || "-"}\n` +
        `  thừa:  ${result.extra.join(", ") || "-"}\n`
    );
    process.exit(1);
  }

  const applied = sync(SRC, DST);
  process.stdout.write(
    `Đã đồng bộ ${applied.written.length} file sang dashboard/public/scp-office` +
      (applied.removed.length ? `, xoá ${applied.removed.length} file thừa` : "") +
      ".\n"
  );
  if (applied.written.length) {
    applied.written.forEach((f) => process.stdout.write(`  + ${f}\n`));
  }
}

if (require.main === module) {
  main();
}

module.exports = { SRC, DST, listFiles, hashFile, compare, sync };
