/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-web-orchestration-safety
  (pilot chassis v1): audit browser/CDP/DOM/anti-honeypot — sandbox browser, CDP
  port bind + token guard, egress cho CDP, honeypot patterns trong code +
  config; live browser probe là OPTIONAL (nếu môi trường có browser tool thì
  probe, không thì ghi UNPROVEN) — rồi đối soát viên re-grep guard chính và ghi
  sổ verdicts.log. Chỉ-đọc repo."
whenToUse: Cần audit mặt browser/CDP/DOM/anti-honeypot của SCP — on-demand,
  chỉ-đọc, nhanh; live probe tự động bỏ qua nếu môi trường không có browser
  tool.
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const SKILL_PATH = ".agents/skills/scp-web-orchestration-safety/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09) - static-only gan HYPOTHESIS.";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface WebAudit {
  /** Browser sandbox + capability token guard: code file:line + test. */
  sandbox: string;
  /** CDP bridge: port bind (127.0.0.1?), auth/token, cross-container bridge, asyncio bridge. */
  cdp: string;
  /** Egress của luồng web: fetch qua safe_urlopen? SSRF allowlist? DOM parse loop guard? */
  egress: string;
  /** Anti-honeypot: loop guard, timeout, trap patterns trong code. */
  honeypot: string;
  /** Live probe kết quả hoặc UNPROVEN kèm lý do. */
  liveProbe: string;
  findings: Finding[];
  verdict: string;
  commands: string[];
}
interface CheckerResult { claims: string[]; fabrication: string; cleanup: string; ledgerLine: string; overall: string; }
interface WorkflowReport { conclusion: string; verified: string[]; notCovered: string[]; }

phase("Nạp chassis + skill theo đúng HEAD (fail-closed FA-10)");
const head = await world.run("git", ["rev-parse", "HEAD"]);
const LIVE_HEAD = head.stdout.trim();
if (head.exitCode !== 0 || !/^[0-9a-f]{40}$/i.test(LIVE_HEAD)) {
  return { conclusion: "DỪNG fail-closed: không đọc được git HEAD hợp lệ (exit=" + head.exitCode + ", stdout='" + LIVE_HEAD + "') (FA-10).", verified: ["git rev-parse HEAD"], notCovered: ["toàn bộ nhiệm vụ — dừng trước khi bắt đầu"] };
}
if (REQ_HEAD !== "" && REQ_HEAD !== "auto" && REQ_HEAD !== "HEAD" && REQ_HEAD !== LIVE_HEAD) {
  return { conclusion: "DỪNG fail-closed: git HEAD hiện hành = " + LIVE_HEAD + " khác args.head_sha = " + REQ_HEAD + " (FA-10).", verified: ["git rev-parse HEAD"], notCovered: ["toàn bộ nhiệm vụ — dừng trước khi bắt đầu"] };
}
const HEAD = LIVE_HEAD;
const SHORT = HEAD.slice(0, 8);
const chassis = await files.read(CHASSIS_PATH);
const skill = await files.read(SKILL_PATH);
log("Chassis + skill scp-web-orchestration-safety đã nạp @ " + SHORT);

phase("Audit browser/CDP/anti-honeypot");
const auditor = await agent("Kiểm toán web orchestration", {
  system: "Bạn là kiểm toán viên web-orchestration-safety: chính là code-đọc + config, live probe chỉ khi công cụ browser có sẵn trong môi trường và an toàn (KHÔNG vào web thật ngoài allowlist local). Static-only nghiêm trọng gắn HYPOTHESIS (FA-09). Không sửa file repo."
}).ask<WebAudit>(
  BINDING + "\n\n# SKILL scp-web-orchestration-safety (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — audit browser/CDP SCP @ D:/scp (HEAD " + HEAD + ") — CHỈ-ĐỌC repo:\n1. SANDBOX: browser sandbox + capability token guard (B38 lineage) — file:line, test pin; profile isolation; workspace-scoped state.\n2. CDP: asyncio CDP bridge (B85), port 9222 bind loopback hay 0.0.0.0?, cross-container bridge auth (B83), token/HMAC guard trước accept — evidence code + compose.\n3. EGRESS web: fetch nào đi qua safe_urlopen/SSRF allowlist, cái nào không; Deep Scraper + AutoWebIngestor staging filter (prompt-injection filter) còn nguyên không.\n4. ANTI-HONEYPOT: loop/timeout guards, trap detection patterns theo skill checklist — có/thiếu cái gì.\n5. LIVE PROBE: nếu môi trường có công cụ browser automation → probe 1 trang local an toàn (127.0.0.1) chứng minh guard chạy; KHÔNG có → ghi 'UNPROVEN — không có browser tool trong môi trường' (không giảm verdict vì static có thể đủ một phần).\n6. Verdict: mức an toàn của mặt web (STRONG/MEDIUM/WEAK trong scope static + live nếu có), thiếu gì.\n7. Trả đúng case: sandbox, cdp, egress, honeypot, liveProbe, findings, verdict, commands."
);

phase("Đối soát viên độc lập re-grep + ghi sổ");
const checker = await agent("Đối soát viên web", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự grep/đọc lại. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (chỉ-đọc):\n1. Re-grep 2 guard chính mà báo cáo khai (VD: CDP bind, capability token) bằng lệnh riêng của bạn → khớp tồn tại/không tồn tại?\n2. Spot-check 1 file:line → khớp nội dung?\n3. Claim 'thiếu X' → tự tìm X ở chỗ khác 1 lượt trước khi công nhận (tránh missing-piece sai).\n\n# DỌN DẸP: chỉ-đọc — ghi 'không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-web-orchestration-safety | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-web-audit", [
  "# Web-orchestration audit — chassis pilot @ " + SHORT,
  "",
  "## Sandbox + capability guard",
  auditor.sandbox,
  "",
  "## CDP bridge",
  auditor.cdp,
  "",
  "## Egress web",
  auditor.egress,
  "",
  "## Anti-honeypot",
  auditor.honeypot,
  "",
  "## Live probe",
  auditor.liveProbe,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Web-orchestration audit (chassis pilot)", description: "Browser/CDP/honeypot @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Web-orchestration @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker re-grep: " + checker.overall],
  notCovered: ["Live browser honeypot test (cần harness riêng + target an toàn)"],
};
return result;