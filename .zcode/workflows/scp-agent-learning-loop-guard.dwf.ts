/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-learning-loop-guard (pilot
  chassis v1): audit vòng học/knowledge warehouse/autofix chống
  knowledge-poisoning và catastrophic-forgetting — manifest checksum catalog,
  quarantine staging, KB rows (SQLite read-only), sổ fix autofix + policy gate,
  allowlist ingestion — rồi đối soát viên re-hash/re-count và ghi sổ
  verdicts.log. Chỉ-đọc toàn bộ, không gọi mạng, không boot."
whenToUse: Cần audit vòng học/knowledge warehouse/autofix của SCP chống
  knowledge-poisoning + catastrophic-forgetting — on-demand, chỉ-đọc tuyệt đối,
  không mạng không boot. Nhanh (~vài phút), an toàn chạy bất cứ lúc nào.
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
const SKILL_PATH = ".agents/skills/scp-learning-loop-guard/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface LoopAudit {
  /** Knowledge store: manifest checksum verify, shards, staging quarantine labels. */
  knowledgeStore: string;
  /** KB WAL rows (read-only): counts theo attribute, freshness cửa sổ. */
  knowledgeBase: string;
  /** AutoFix: sổ fix, policy gate, subsystem reviewer, mutation/rollback evidence. */
  autofix: string;
  /** Ingestion: allowlist domains, egress grants, quarantine flow của AutoWebIngestor. */
  ingestion: string;
  findings: Finding[];
  /** Đánh giá rủi ro poisoning/forgetting: LOW/MEDIUM/HIGH + scope. */
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
log("Chassis + skill scp-learning-loop-guard đã nạp @ " + SHORT);

phase("Audit vòng học + kho tri thức + autofix");
const auditor = await agent("Kiểm toán learning loop", {
  system: "Bạn là kiểm toán viên learning-loop-guard: đọc-only tuyệt đối — KHÔNG ghi vào KB/knowledge/staging, KHÔNG gọi mạng, KHÔNG boot service. Chỉ tin số tự đếm/hash tự tính. Không bịa; không chắc ghi UNKNOWN."
}).ask<LoopAudit>(
  BINDING + "\n\n# SKILL scp-learning-loop-guard (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — audit vòng học SCP @ D:/scp (HEAD " + HEAD + ") CHỈ-ĐỌC (không docker, không mạng; SQLite mở mode=ro nếu cần):\n1. KNOWLEDGE STORE: tools/validate_knowledge_catalog.py (chạy readonly nếu tool cho phép; nếu tool ghi file thì KHÔNG chạy, thay bằng tự verify SHA-256 manifest vs shards bằng lệnh hash) → checksum khớp hết không; liệt kê shards + số records; staging/legacy_unverified còn cách ly đúng không (không lẫn vào catalog).\n2. KNOWLEDGE BASE: sqlite data/hands WAL mode=ro (nếu tồn tại) → đếm rows theo attribute (news_fact...), freshness mới nhất; nếu file không tồn tại → ghi UNPROVEN kèm lý do, không tạo file mới.\n3. AUTOFIX: sổ fix/hands ledger (nếu có), policy manifest, subsystem_reviewer gate, shadow snapshot/rollback mechanism còn nguyên không (đọc code + config); benchmark wp0 fixtures.\n4. INGESTION: TRUSTED_INGESTION_DOMAINS + egress grants (đọc code/config — không probe mạng), quarantine flow của AutoWebIngestor (staging → cross-verify ≥2 nguồn → HMAC receipt → promote) còn nguyên chuỗi không; kill-switch SCP_AUTO_WEB_INGEST.\n5. ĐÁNH GIÁ: rủi ro knowledge-poisoning (nguồn không kiểm chứng lọt vào CanonicalFTS?) + catastrophic-forgetting (cơ chế negative memory/epistemic?) → LOW/MEDIUM/HIGH kèm bằng chứng.\n6. Trả đúng case: knowledgeStore, knowledgeBase, autofix, ingestion, findings, verdict, commands."
);

phase("Đối soát viên độc lập re-hash + ghi sổ");
const checker = await agent("Đối soát viên learning loop", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự tay hash/đếm lại. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (chỉ-đọc, không mạng):\n1. Tự hash 2 shard catalog bất kỳ (sha256sum) so manifest → khớp khai?\n2. Tự đếm 1 số KB/staging mà báo cáo khai (ls | wc -l / sqlite ro) → khớp?\n3. Spot-check 1 code ref autofix/quarantine (file:line) → khớp?\n4. Xác nhận maker KHÔNG ghi gì vào data/ (git status data/ không bẩn)?\n\n# DỌN DẸP: chỉ-đọc — ghi 'không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-learning-loop-guard | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-learning-audit", [
  "# Learning-loop audit — chassis pilot @ " + SHORT,
  "",
  "## Knowledge store",
  auditor.knowledgeStore,
  "",
  "## Knowledge base",
  auditor.knowledgeBase,
  "",
  "## AutoFix",
  auditor.autofix,
  "",
  "## Ingestion",
  auditor.ingestion,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Learning-loop audit (chassis pilot)", description: "KB + autofix + ingestion chống poisoning @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Learning-loop @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker re-hash/re-count: " + checker.overall],
  notCovered: ["Live ingestion cycle (cần egress + owner duyệt) chưa probe", "Chi tiết findings xem artifact"],
};
return result;