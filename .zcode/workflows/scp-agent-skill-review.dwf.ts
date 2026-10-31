/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-skill-review (pilot chassis
  v1): review bộ skill pack .agents/skills/ theo 6 chiều C1-C6 + 4 mức bằng
  chứng A-D — index nhất quán (recount filesystem vs bảng AGENTS.md vs
  manifest), mô tả trigger được, bằng chứng, calibration — rồi đối soát viên
  recount + spot-check 2 skill và ghi sổ verdicts.log. Chỉ-đọc, không docker."
whenToUse: Cần audit bộ 16 SCP skill (index nhất quán, chất lượng SKILL.md, mức
  bằng chứng A-D, trusted baseline) — on-demand, chỉ-đọc, nhanh.
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
const SKILL_PATH = ".agents/skills/scp-skill-review/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); khong claim maturity tu presence (FA-07).";

interface SkillRow { name: string; c1to6: string; evidenceLevel: string; notes: string; }
interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface SkillAudit {
  /** Recount filesystem vs AGENTS.md table vs manifest (16?) — lệch từng cặp. */
  index: string;
  /** Bảng từng skill: C1-C6 + mức bằng chứng. */
  rows: SkillRow[];
  /** Skill nào đáng tin làm chuẩn sửa SCP (theo tiêu chí skill-review). */
  trustedBaseline: string;
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
log("Chassis + skill scp-skill-review đã nạp @ " + SHORT);

phase("Review skill pack theo C1-C6 + A-D");
const auditor = await agent("Thẩm định skill pack", {
  system: "Bạn là thẩm định viên skill pack: đếm trước kết luận (count là inventory hint, không phải evidence); mô tả trong SKILL.md phải thực dùng được (trigger được); bằng chứng A-D chỉ khi có evidence thật, không thì UNPROVEN. Không sửa file nào."
}).ask<SkillAudit>(
  BINDING + "\n\n# SKILL scp-skill-review (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — review toàn bộ pack .agents/skills/ @ D:/scp (HEAD " + HEAD + ") CHỈ-ĐỌC:\n1. INDEX: ls -d .agents/skills/*/SKILL.md | wc -l (recount) vs bảng §1 .agents/AGENTS.md vs spec manifest (normative_scp_skills) — từng cặp khớp/lệch; skill nào có trong dir mà thiếu bảng hoặc ngược lại.\n2. ROWS: đọc từng SKILL.md — chấm 6 chiều C1-C6 theo tiêu chí trong skill-review (mô tả + trigger + bằng chứng + calibration +...), gắn mức bằng chứng A (tài liệu) / B (tool enforce) / C (reality evidence) / D (recovery evidence) — KHÔNG đạt thì ghi UNPROVEN, không phóng đạt.\n3. TRUSTED BASELINE: skill nào đủ tin làm chuẩn sửa chính SCP (tiêu chí của skill-review) — kèm lý do.\n4. Verdict tổng: pack nhất quán không, mục cần sửa (index drift, mô tả sai, skill trùng scp-dna 2 bản local-vs-repo?).\n5. Trả đúng case: index, rows (đủ mọi skill), trustedBaseline, findings, verdict, commands."
);

phase("Đối soát viên độc lập recount + ghi sổ");
const checker = await agent("Đối soát viên skill pack", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự đếm/tự đọc lại. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO THẨM ĐỊNH (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (chỉ-đọc):\n1. Tự recount ls -d .agents/skills/*/SKILL.md | wc -l → khớp index khai?\n2. Đọc 2 SKILL.md bất kỳ trong rows → nhận xét C-level khớp nội dung thật không?\n3. Claim 'lệch index' → tự đối chiếu bảng AGENTS.md §1 → đúng lệch không?\n\n# DỌN DẸP: chỉ-đọc — 'không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-skill-review | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

const tableRows = auditor.rows.map((r) => "| " + r.name + " | " + r.c1to6 + " | " + r.evidenceLevel + " | " + r.notes + " |").join("\n");
await artifact.markdown("bao-cao-skill-review", [
  "# Skill-pack review — chassis pilot @ " + SHORT,
  "",
  "## Index (recount vs bảng vs manifest)",
  auditor.index,
  "",
  "## Bảng skill",
  "| Skill | C1-C6 | Bằng chứng | Ghi chú |",
  "|---|---|---|---|",
  tableRows,
  "",
  "## Trusted baseline",
  auditor.trustedBaseline,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Skill-pack review (chassis pilot)", description: "C1-C6 + A-D toàn pack @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Skill review @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker recount: " + checker.overall],
  notCovered: ["Kiểm tra cài đặt skill trên surface nào đó (NOT_INSTALLED_ON_SURFACE mặc định)"],
};
return result;