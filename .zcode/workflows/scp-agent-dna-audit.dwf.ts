/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-dna (pilot chassis v1): thẩm
  định một claim/báo cáo/kế hoạch theo 29 nguyên lý SCP DNA — chuỗi Tại sao, bản
  đồ bằng chứng + lineage, mảnh ghép thiếu, nhãn UNKNOWN, đề xuất hành động nhỏ
  có rollback — rồi đối soát viên độc lập đối chiếu lại đối tượng và ghi sổ
  verdicts.log. Chỉ-đọc, không đụng docker."
whenToUse: Cần thẩm định một claim/báo cáo/kế hoạch/biến đổi bất kỳ theo chuẩn
  SCP DNA (lineage bằng chứng, missing piece, fail-closed) — on-demand, chỉ-đọc,
  nhanh. Truyền bắt buộc args.target.
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
  target:
    type: string
    description: "Đối tượng thẩm định: một claim/báo cáo/kế hoạch/biến đổi — trích
      dẫn nguyên văn HOẶC path file trong workspace."
    required: true
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const TARGET = String(args.target ?? "");
const SKILL_PATH = ".agents/skills/scp-dna/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that, static-only gan HYPOTHESIS (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface DnaAudit {
  /** Phần thân báo cáo markdown theo cấu trúc skill: Problem / Why chain / Evidence map / Missing pieces / Proposed action / Open questions. */
  report: string;
  findings: Finding[];
  /** ĐẠT / KHÔNG ĐẠT / UNKNOWN + 2 câu lý do + scope. */
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
log("Chassis + skill scp-dna đã nạp @ " + SHORT);

phase("Thẩm định đối tượng theo 29 nguyên lý DNA");
const auditor = await agent("Thẩm định viên DNA", {
  system: "Bạn là thẩm định viên SCP DNA: phân tích sâu, không nhảy cóng vào giải pháp, tách quan sát khỏi suy đoán, kết thúc bằng câu hỏi mở thay vì PASS tuyệt đối."
}).ask<DnaAudit>(
  BINDING + "\n\n# SKILL scp-dna (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# ĐỐI TƯỢNG THẨM ĐỊNH (args.target):\n" + TARGET + "\n\n# NHIỆM VỤ @ D:/scp (HEAD " + HEAD + ") — thực thi đúng cấu trúc Response Structure của skill đã nạp (Problem statement / Why chain 3-5 tầng / Evidence map kèm lineage + shared-origin risk / Missing pieces / Proposed action nhỏ + rollback + reality-test / Open questions). Nếu đối tượng là path file trong workspace thì đọc file đó; KHÔNG sửa file nào; không boot gì; mọi phát hiện gắn nhãn OBSERVED/SUPPORTED_INFERENCE/UNPROVEN/HYPOTHESIS. Verdict chỉ là ĐẠT/KHÔNG ĐẠT/UNKNOWN trong scope, không tuyên bố tuyệt đối (DNA #22/#24). Trả đúng case: report (markdown đầy đủ), findings, verdict, commands (lệnh đã chạy để đọc đối tượng, nếu có)."
);

phase("Đối soát viên độc lập đối chiếu + ghi sổ");
const checker = await agent("Đối soát viên DNA", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự đọc lại đối tượng gốc và đối chiếu các claim chính của báo cáo. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung bất đồng."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# ĐỐI TƯỢNG GỐC (args.target):\n" + TARGET + "\n\n# BÁO CÁO CỦA THẨM ĐỊNH VIÊN (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN:\n1. Đọc lại đối tượng gốc (trích dẫn hoặc path) → các Evidence map claim có khớp với nguồn thật không (spot-check 2-3 claim chính bằng lệnh đọc thật)?\n2. Missing pieces nêu ra có đúng là 'chưa có bằng chứng' không (tự kiểm 1 cái — nếu tìm thấy bằng chứng ngược → SAI)?\n3. Verdict có vượt scope (tuyên bố tuyệt đối không được hỗ trợ) không?\n\n# DỌN DẸP: bản thể này chỉ-đọc, không có tài nguyên docker — ghi 'không có'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-dna-audit | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt 1 câu>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-dna-audit", [
  "# DNA audit — chassis pilot @ " + SHORT,
  "",
  "Đối tượng: " + TARGET.slice(0, 200),
  "",
  auditor.report,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "DNA audit (chassis pilot)", description: "Thẩm định theo 29 nguyên lý @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "DNA audit @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker đối chiếu đối tượng gốc: " + checker.overall],
  notCovered: ["Các open questions còn lại trong report"],
};
return result;