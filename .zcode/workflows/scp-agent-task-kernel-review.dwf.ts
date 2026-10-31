/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-task-kernel-review (pilot
  chassis v1): audit Task Kernel — state machine (đếm STATES thật vs docs), OCC
  lock, journal append-only + HMAC receipt, lease/TTL, checkpoint recovery,
  kill-switch, idempotency — bằng code-đọc + import thật trong container + chạy
  subset contract tests T04 — rồi đối soát viên re-run đếm/test và ghi sổ
  verdicts.log. Không boot server."
whenToUse: Cần audit Task Kernel SCP (state machine, OCC, journal HMAC,
  checkpoint/kill-switch) tại một HEAD — on-demand, không boot server, nhanh hơn
  reality-verifier.
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
const SKILL_PATH = ".agents/skills/scp-task-kernel-review/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface KernelAudit {
  /** States: số đếm thật (import/AST) vs docs (17 vs 18?), transitions hợp lệ. */
  states: string;
  /** OCC lock / idempotency: cơ chế + evidence code + test. */
  concurrency: string;
  /** Journal: append-only, key-versioning, HMAC VerifierReceipt, receipt_auditor tool. */
  journal: string;
  /** Lease/TTL, checkpoint/recovery, kill-switch channels. */
  recovery: string;
  /** Kết quả chạy subset contract tests (tối đa ~10 test chọn lọc). */
  testProbe: string;
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
log("Chassis + skill scp-task-kernel-review đã nạp @ " + SHORT);

phase("Audit kernel: code + import thật + subset tests");
const auditor = await agent("Kiểm toán Task Kernel", {
  system: "Bạn là kiểm toán viên Task Kernel: số đếm phải từ import/AST thật không phải từ docs; mọi claim cơ chế phải có file:line + (nếu có thể) test thật. Không boot server; container chỉ dùng --rm. Không bịa; không chắc ghi UNKNOWN."
}).ask<KernelAudit>(
  BINDING + "\n\n# SKILL scp-task-kernel-review (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — audit Task Kernel SCP @ D:/scp (HEAD " + HEAD + "). KHÔNG sửa file repo; KHÔNG boot server; docker chỉ --rm (dùng image scp-agent-runtime:" + SHORT + " nếu đã có, chưa có thì docker build --target runtime --build-arg SCP_GIT_SHA=" + HEAD + " -t scp-agent-runtime:" + SHORT + " D:/scp).\n\n1. STATES: đếm STATES thật bằng python import trong container (python -c 'from scp.task_kernel_parts.definitions import STATES; print(len(STATES))' hoặc AST nếu import cần env) → so với docs ghi 17; liệt kê tên states + transitions bị cấm.\n2. CONCURRENCY: OCC lock trong commit_approval/transition (file:line), idempotency key, stale-lease không được commit (luật GA) — evidence code + test tương ứng.\n3. JOURNAL: append-only, key-versioning, HMAC VerifierReceipt (sign_verifier_receipt), offline receipt_auditor CLI (tools/) — đọc code, xác nhận auditor dùng public keyring only.\n4. RECOVERY: lease TTL/heartbeat, checkpoint cutover một chiều (R5), recovery authority không cấp lại quyền cho stale worker, kill-switch kênh (file/env/kernel/fail-closed) — evidence code:line từng cái.\n5. TEST PROBE: chạy TỐI ĐA 10 test contract chọn lọc trong container (tests/T04_kernel subset — chọn FileTest giao phủ 5 mục trên, ví dụ autonomous_checker_gate_contract + idempotency + journal) → PASS/FAIL từng cái, ghi node id.\n6. Trả đúng case: states, concurrency, journal, recovery, testProbe, findings, verdict (đủ scope: phần chưa probe ghi rõ), commands."
);

phase("Đối soát viên độc lập re-run + ghi sổ");
const checker = await agent("Đối soát viên kernel", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự import/đếm/chạy lại. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải tái hiện mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (container --rm, không boot):\n1. Tự import đếm STATES → số khớp khai? (lệnh của maker hoặc AST grep)\n2. Chạy lại 2/10 test mà maker đã chạy → PASS khớp?\n3. Spot-check 2 code ref (file:line) trong journal/recovery → khớp?\n4. docker image inspect scp-agent-runtime:" + SHORT + " (nếu maker build) tồn tại?\n\n# DỌN DẸP: container đều --rm hết sẵn; image scp-agent-runtime:" + SHORT + " là TÀI SẢN DÙNG CHUNG — KHÔNG rmi. Ghi 'không còn tài nguyên riêng'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-task-kernel-review | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-kernel-audit", [
  "# Task-kernel audit — chassis pilot @ " + SHORT,
  "",
  "## States",
  auditor.states,
  "",
  "## Concurrency (OCC/idempotency)",
  auditor.concurrency,
  "",
  "## Journal + HMAC receipt",
  auditor.journal,
  "",
  "## Recovery + kill-switch",
  auditor.recovery,
  "",
  "## Test probe",
  auditor.testProbe,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Task-kernel audit (chassis pilot)", description: "States + OCC + journal HMAC + recovery @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Task-kernel audit @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: [...auditor.commands, "checker re-run: " + checker.overall],
  notCovered: ["Full 409-test kernel suite (thuộc reality-verifier)", "Chaos/soak tests chưa trong pilot"],
};
return result;