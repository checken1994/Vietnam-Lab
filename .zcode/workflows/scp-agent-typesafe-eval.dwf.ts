/* zcode-workflow
description: "Bản thể agent độc lập cho skill typesafe-agent-eval (pilot chassis
  v1): chạy TypeSafe AI Evaluation API (noul/choice/score) lên một diff/plan
  file qua integrations/typesafe/typesafe_eval.py — fail-closed nếu thiếu
  TYPESAFE_API_KEY — rồi đối soát viên re-run cùng lệnh khớp kết quả và ghi sổ
  verdicts.log. Workflow-only, KHÔNG import vào runtime SCP."
whenToUse: Cần đánh giá typed độc lập (noul/choice/score) cho một diff/plan theo
  quy trình agent (workflow-only) — on-demand; fail-closed nếu thiếu
  TYPESAFE_API_KEY. Truyền bắt buộc args.file_path.
args:
  eval_kind:
    type: string
    description: "Loại đánh giá: choice (risk_level=low|medium|high|critical) | noul
      (is_safe=...) | score."
    required: false
    default: choice
  eval_prompt:
    type: string
    description: Câu hỏi đánh giá cho noul/choice (bọc trong nháy kép khi truyền).
    required: false
    default: Does this change maintain fail-closed boundaries without unintended
      side effects?
  file_path:
    type: string
    description: Path file trong workspace chứa diff/plan cần đánh giá (truyền cho
      typesafe_eval.py --file).
    required: true
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const FILE_PATH = String(args.file_path ?? "");
const RAW_KIND = String(args.eval_kind ?? "").trim();
const KIND = RAW_KIND !== "" ? RAW_KIND : "choice";
const RAW_PROMPT = String(args.eval_prompt ?? "").trim();
const PROMPT = RAW_PROMPT !== "" ? RAW_PROMPT : "Does this change maintain fail-closed boundaries without unintended side effects?";
const SKILL_PATH = ".agents/skills/typesafe-agent-eval/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08): ket qua TypeSafe phai la raw stdout that cua script, khong duoc tu-dien.";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface EvalAudit {
  /** Lệnh chính xác đã chạy + raw stdout/stderr nguyên văn. */
  invocation: string;
  /** File được đánh giá: path, kích thước, loại (diff/plan). */
  target: string;
  /** Đọc kết quả: risk_level / noul / score + has_breaking_side_effects nếu có. */
  outcome: string;
  findings: Finding[];
  /** DÙNG-ĐƯỢC / KHÔNG-CHẠY-ĐƯỢC (lý do: thiếu key/script) + ý nghĩa trong phạm vi. */
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
log("Chassis + skill typesafe-agent-eval đã nạp @ " + SHORT);

phase("Chạy TypeSafe eval lên diff/plan");
const auditor = await agent("Người chạy TypeSafe eval", {
  system: "Bạn là người vận hành TypeSafe eval: kết quả là raw stdout của script thật — KHÔNG tự diễn ra kết quả nếu script fail; fail-closed khi thiếu key (KHÔNG tự bịa verdict). Loại eval workflow-only, không đụng runtime SCP. Không sửa file repo."
}).ask<EvalAudit>(
  BINDING + "\n\n# SKILL typesafe-agent-eval (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# ĐÁNH GIÁ file: " + FILE_PATH + " | kind: " + KIND + " @ D:/scp (HEAD " + HEAD + ")\n\n# NHIỆM VỤ:\n1. Tiền-kiểm: file " + FILE_PATH + " tồn tại trong workspace không (files.read hoặc ls)? integrations/typesafe/typesafe_eval.py tồn tại không? .env có TYPESAFE_API_KEY không (kiểm tra TỒN TẠI bằng grep -c '^TYPESAFE_API_KEY=' D:/scp/.env — KHÔNG in giá trị)?\n2. Thiếu bất kỳ → DỪNG fail-closed: verdict 'KHÔNG-CHẠY-ĐƯỢC' + lý do nguyên văn, không bịa kết quả.\n3. Đủ → chạy đúng lệnh chuẩn của skill (interpreter .venv/Scripts/python.exe nếu host python là Store stub): kind choice → --choice \"risk_level=low|medium|high|critical\" --noul \"" + PROMPT + "\"; kind noul → chỉ --noul; kind score → theo hướng dẫn skill. Ghi raw stdout/stderr NGUYÊN VĂN.\n4. Đọc kết quả: risk_level/noul/score + has_breaking_side_effects nếu có — trích đúng, không thêm ý kiến ngoài output (ý kiến riêng ghi findings).\n5. Trả đúng case: invocation, target, outcome, findings, verdict, commands."
);

phase("Đối soát viên độc lập re-run + ghi sổ");
const checker = await agent("Đối soát viên TypeSafe", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: re-run cùng lệnh khớp kết quả (API có thể không-deterministic nhẹ → chênh nhỏ chấp nhận, chênh verdict hoàn toàn → ghi UNKNOWN). Không bịa; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO (lời khai — phải re-run mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN:\n1. File được đánh giá tồn tại khớp khai?\n2. Re-run ĐÚNG lệnh invocation khai (tối đa 1 lần) → outcome khớp (hoặc chênh nhỏ do API)?\n3. Nếu maker báo KHÔNG-CHẠY-ĐƯỢC: tự kiểm nguyên nhân (key/script tồn tại?) → khớp lý do?\n\n# DỌN DẸP: chỉ-đọc + 1 API call — 'không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-typesafe-eval | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-typesafe-eval", [
  "# TypeSafe eval — chassis pilot @ " + SHORT,
  "",
  "File: " + FILE_PATH + " · Kind: " + KIND,
  "",
  "## Invocation (nguyên văn)",
  auditor.invocation,
  "",
  "## Outcome",
  auditor.outcome,
  "",
  "## Verdict",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "TypeSafe eval (chassis pilot)", description: "noul/choice/score @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "TypeSafe eval @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker re-run: " + checker.overall],
  notCovered: ["TypeSafe là tool quy trình agent — không thay thế reality evidence của SCP runtime"],
};
return result;