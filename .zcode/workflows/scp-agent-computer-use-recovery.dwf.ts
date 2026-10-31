/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-computer-use-recovery (pilot
  chassis v1): phục hồi task bị ngắt KHÔNG LẶP SIDE EFFECT — reconcile trước
  (inspect filesystem/docker/log để xác định side effect đã xảy ra chưa),
  verdict RECONCILED / UNKNOWN / HUMAN_REVIEW, KHÔNG BAO GIỜ tự retry side
  effect uncertain — rồi đối soát viên re-check state probes và ghi sổ
  verdicts.log."
whenToUse: Task GUI/browser/computer-use bị ngắt giữa chừng, nghi side effect
  đã/xảy ra — reconcile trước khi resume, KHÔNG tự retry uncertain action.
  Truyền bắt buộc args.task_context. Chỉ-đọc.
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
  task_context:
    type: string
    description: Mô tả task GUI/browser bị ngắt giữa chừng + side effect nghi ngờ
      (VD 'đã bấm submit form X rồi browser chết' hoặc 'workflow đã gửi
      tool-call Y nhưng không thấy response').
    required: true
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const CONTEXT = String(args.task_context ?? "");
const SKILL_PATH = ".agents/skills/scp-computer-use-recovery/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); KHONG BAO GIO retry mot side effect ngoai chua chac chan - reconcile truoc (GA A4).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface RecoveryAudit {
  /** Side effect nghi ngờ: liệt kê từng cái + state probe cho từng cái. */
  sideEffects: string;
  /** Probe đã chạy: lệnh + kết quả nguyên văn. */
  probes: string;
  /** Kết luận từng side effect: ĐÃ-XẢY-RA / CHƯA-XẢY-RA / KHÔNG-XÁC-ĐỊNH-ĐƯỢC. */
  reconciliation: string;
  /** Bước tiếp theo an toàn: resume từ đâu, phải làm gì trước khi resume, cái gì phải human. */
  resumePlan: string;
  findings: Finding[];
  /** RECONCILED / UNKNOWN / HUMAN_REVIEW + scope. */
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
log("Chassis + skill scp-computer-use-recovery đã nạp @ " + SHORT);

phase("Reconcile side effect trước khi resume");
const auditor = await agent("Điều phối viên recovery", {
  system: "Bạn là điều phối viên recovery: RECONCILE TRƯỚC — không bao giờ retry uncertain external side effect; không chắc = UNKNOWN/HUMAN_REVIEW (GA A4). Chỉ inspect (read-only), KHÔNG thực hiện lại action gốc, KHÔNG sửa file repo. Không bịa; không chắc ghi UNKNOWN."
}).ask<RecoveryAudit>(
  BINDING + "\n\n# SKILL scp-computer-use-recovery (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# TASK BỊ NGẮT (args.task_context):\n" + CONTEXT + "\n\n# NHIỆM VỤ — reconcile theo skill @ D:/scp (HEAD " + HEAD + "):\n1. LIỆT KÊ side effect nghi ngờ từ context (ghi/vùi dữ liệu, gửi message, đổi state container/file...).\n2. PROBE từng side effect bằng inspect chỉ-đọc có thể làm được trong môi trường này (docker ps/logs/inspect, ls/mtime, đọc log, git status, sqlite ro nếu liên quan) — ghi lệnh + kết quả nguyên văn; side effect cần GUI/browser state mà không probe được → KHÔNG-XÁC-ĐỊNH-ĐƯỢC, không đoán.\n3. RECONCILIATION: kết luận từng cái (ĐÃ-XẢY-RA / CHƯA-XẢY-RA / KHÔNG-XÁC-ĐỊNH-ĐƯỢC) kèm evidence.\n4. RESUME PLAN: resume được không, từ bước nào, điều kiện tiên quyết; side effect chưa chắc chắn → bắt buộc HUMAN_REVIEW hoặc cơ chế idempotent-check trước retry; KHÔNG tự retry bất cứ thứ gì.\n5. Trả đúng case: sideEffects, probes, reconciliation, resumePlan, findings, verdict (RECONCILED/UNKNOWN/HUMAN_REVIEW + scope), commands."
);

phase("Đối soát viên độc lập re-check + ghi sổ");
const checker = await agent("Đối soát viên recovery", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự chạy lại state probe then chốt. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# TASK GỐC:\n" + CONTEXT + "\n\n# BÁO CÁO RECOVERY (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN:\n1. Chạy lại 1-2 probe chính mà báo cáo khai → kết quả khớp reconciliation?\n2. Side effect nào maker kết luận ĐÃ-XẢY-RA mà probe của bạn không thấy dấu vết → mâu thuẫn ghi vào fabrication.\n3. Resume plan có tự retry side effect uncertain không (có = NO-GO)?\n\n# DỌN DẸP: chỉ-đọc — 'không có tài nguyên docker của mình'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-computer-use-recovery | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-recovery", [
  "# Computer-use recovery — chassis pilot @ " + SHORT,
  "",
  "Task: " + CONTEXT.slice(0, 300),
  "",
  "## Side effects nghi ngờ + probe",
  auditor.sideEffects,
  "",
  "## Reconciliation",
  auditor.reconciliation,
  "",
  "## Resume plan (an toàn)",
  auditor.resumePlan,
  "",
  "## Verdict",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Recovery reconcile (chassis pilot)", description: "Side-effect reconcile @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Recovery @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker re-run probes: " + checker.overall],
  notCovered: ["GUI/browser state không probe được từ môi trường này"],
};
return result;