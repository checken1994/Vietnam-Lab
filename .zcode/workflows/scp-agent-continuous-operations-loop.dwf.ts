/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-continuous-operations-loop
  (pilot chassis v1): thiết kế hoặc thẩm định vòng vận hành liên tục theo
  pipeline discover → qualify → authorize → execute → independently verify →
  persist → reconcile/escalate — bắt buộc durable state, budget/kill-switch,
  maker/checker split, reconciliation side effect chưa biết — rồi đối soát viên
  rà 5 lỗi phổ biến (thiếu reconcile, retry vô hạn, state bẩn, thiếu
  kill-switch, self-certify) và ghi sổ verdicts.log. Chỉ-thiết-kế/đọc, không
  chạy loop thật."
whenToUse: Cần THIẾT KẾ hoặc THẨM ĐỊNH một vòng vận hành liên tục/định kỳ
  (discover→verify→persist→reconcile, budget, kill-switch, maker/checker split)
  trước khi triển khai automation thật — on-demand, chỉ thiết kế không chạy
  loop. Truyền bắt buộc args.loop_description.
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
  loop_description:
    type: string
    description: Vòng lặp/vận hành định kỳ cần THIẾT KẾ hoặc THẨM ĐỊNH — mô tả mục
      tiêu, nhịp chạy, đầu vào/đầu ra, side effect (nếu đã có config hiện hành
      thì trích dẫn/path).
    required: true
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const LOOP = String(args.loop_description ?? "");
const SKILL_PATH = ".agents/skills/scp-continuous-operations-loop/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); thiet ke khong duoc che giau side effect hoac tu-chap-thuan nguy hiem.";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface LoopDesign {
  /** Thiết kế/thẩm định dạng markdown: pipeline từng stage + durable state + budget/kill-switch + maker/checker split + reconcile flow. */
  design: string;
  findings: Finding[];
  /** SẴN-CHẠY-CÓ-ĐIỀU-KIỆN / CẦN-SỬA / TỪ-CHỐI + điều kiện. */
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
log("Chassis + skill scp-continuous-operations-loop đã nạp @ " + SHORT);

phase("Thiết kế/thẩm định vòng lặp theo pipeline 7 stage");
const auditor = await agent("Kỹ sư vòng vận hành", {
  system: "Bạn là kỹ sư continuous-operations-loop: mọi vòng lặp phải có durable state, budget/kill-switch, maker/checker tách rời, và reconciliation cho side effect chưa biết. Chỉ THIẾT KẾ/THẨM ĐỊNH — KHÔNG triển khai loop thật, KHÔNG ghi file repo. Không bịa; thiếu gì ghi thiếu."
}).ask<LoopDesign>(
  BINDING + "\n\n# SKILL scp-continuous-operations-loop (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# VÒNG LẶP (args.loop_description):\n" + LOOP + "\n\n# NHIỆM VỤ @ D:/scp (HEAD " + HEAD + "):\n1. Nếu mô tả là loop ĐÃ CÓ (trích config/path trong workspace) → THẨM ĐỊNH theo checklist skill: đọc config thật nếu path cho, đối chiếu pipeline 7 stage.\n2. Nếu là loop MỚI → THIẾT KẾ đầy đủ: discover → qualify → authorize (approval gate rõ ai duyệt gì) → execute → independently verify (checker khác lineage maker) → persist (durable state ghi ở đâu, format gì) → reconcile/escalate (side effect uncertain xử lý thế nào, không blind-retry).\n3. BẮT BUỘC mỗi thiết kế có: budget cap + retry cap + kill-switch (cách tắt ngay cả khi loop chết giật), maker/checker split, journal/ledger append-only, escalation tới human khi nào.\n4. Phát hiện loop mô tả có tự-chấp-thuận nguy hiểm / thiếu reconcile / retry vô hạn → findings severity high.\n5. Verdict: SẴN-CHẠY-CÓ-ĐIỀU-KIỆN (liệt kê điều kiện) / CẦN-SỬA / TỪ-CHỐI + lý do.\n6. Trả đúng case: design, findings, verdict, commands (lệnh đọc config nếu có)."
);

phase("Đối soát viên độc lập rà 5 lỗi phổ biến + ghi sổ");
const checker = await agent("Đối soát viên loop", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: rà thiết kế chống 5 lỗi phổ biến của loop (thiếu reconciliation, retry vô hạn, state không durable, thiếu kill-switch, self-certify maker=checker). Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# VÒNG LẶP GỐC:\n" + LOOP + "\n\n# THIẾT KẾ/THẨM ĐỊNH (lời khai — phải rà mới tin):\n" + JSON.stringify(auditor) + "\n\n# RÀ 5 LỖI PHỔ BIẾN (từng cái: có/thiếu + evidence trong design):\n1. Thiếu reconciliation cho side effect uncertain?\n2. Retry vô hạn / không cap?\n3. State không durable (RAM-only)?\n4. Không có kill-switch/budget?\n5. Maker tự verify chính mình (self-certify)?\n6. Nếu là thẩm định loop có sẵn: spot-check 1 config thật mà design trích dẫn → khớp?\n\n# DỌN DẸP: chỉ thiết kế/đọc — 'không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-continuous-operations-loop | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-loop-design", [
  "# Continuous-operations loop — chassis pilot @ " + SHORT,
  "",
  "Loop: " + LOOP.slice(0, 300),
  "",
  auditor.design,
  "",
  "## Verdict",
  auditor.verdict,
  "",
  "## Đối soát độc lập (5 lỗi phổ biến)",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Loop design/audit (chassis pilot)", description: "Pipeline 7 stage + kill-switch @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Loop @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker rà 5 lỗi: " + checker.overall],
  notCovered: ["Triển khai loop thật (thuộc CronCreate/automation lane riêng khi owner duyệt)"],
};
return result;