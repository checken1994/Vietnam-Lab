/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-startup-troubleshooter (pilot
  chassis v1): chẩn đoán lỗi khởi động SCP — boot thử container riêng port 8043
  (hoặc phân tích symptom đã cho), bắt nguyên nhân fail-closed NGUYÊN VĂN, chẩn
  đoán theo skill (guard config, port mismatch, WAL lock holder, env lệch), đề
  xuất fix KHÔNG tự sửa — rồi đối soát viên tái hiện root-cause probe và ghi sổ
  verdicts.log."
whenToUse: SCP boot lỗi/port lệch/dashboard không nối — chẩn đoán root-cause
  bằng boot thử + đọc guard, ĐỀ XUẤT fix mà không tự sửa. On-demand; truyền
  args.symptom nếu đã có log lỗi (rỗng = tự boot thử). Port 8043, image riêng
  scp-agent-startup.
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
  port:
    type: number
    description: Port host cho container chẩn đoán boot; tránh trùng phiên khác.
    required: false
    default: 8043
  symptom:
    type: string
    description: Triệu chứng lỗi khởi động đã quan sát (nguyên văn log/lỗi) — nếu
      rỗng thì bản thể tự boot thử để bắt lỗi.
    required: false
    default: ""
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const PORT = String(args.port ?? 8043);
const SYMPTOM = String(args.symptom ?? "");
const SKILL_PATH = ".agents/skills/scp-startup-troubleshooter/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface StartupAudit {
  /** Đã thử boot thế nào, kết quả nguyên văn (log lỗi fail-closed). */
  bootAttempt: string;
  /** Root cause theo skill: từng nguyên nhân ứng viên + evidence loại trừ/xác nhận. */
  rootCause: string;
  /** Fix đề xuất từng bước (KHÔNG tự sửa — chỉ đề xuất + cách verify). */
  proposedFix: string;
  findings: Finding[];
  /** ROOT_CAUSE_FOUND / NEEDS_HUMAN / NO_ISSUE_FOUND + scope. */
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
log("Chassis + skill scp-startup-troubleshooter đã nạp @ " + SHORT);

phase("Chẩn đoán khởi động theo skill");
const auditor = await agent("Bác sĩ khởi động", {
  system: "Bạn là chẩn đoán viên startup: lỗi fail-closed là BẰNG CHỨNG không phải kẻ địch — đọc nguyên văn, phân loại theo skill, KHÔNG hack vòng guard. Không sửa file repo; fix chỉ ĐỀ XUẤT kèm cách verify. Không bịa; không chắc ghi NEEDS_HUMAN."
}).ask<StartupAudit>(
  BINDING + "\n\n# SKILL scp-startup-troubleshooter (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — chẩn đoán boot SCP @ D:/scp (HEAD " + HEAD + "). KHÔNG sửa file repo; KHÔNG kill container/đụng port của phiên khác.\n\n# SYMPTOM người dùng cung cấp (nếu rỗng → tự boot thử):\n" + (SYMPTOM === "" ? "(rỗng — tự boot thử)" : SYMPTOM) + "\n\n1. Nếu symptom rỗng: docker build --target runtime --build-arg SCP_GIT_SHA=" + HEAD + " -t scp-agent-startup:" + SHORT + " D:/scp (ghi BUILD_EXIT; fail build → chính nó là root cause layer 1) rồi docker run -d --name scp-agent-startup --env-file D:/scp/.env -e SCP_HOST=0.0.0.0 -e SCP_PRODUCTION_MODE=0 -e SCP_FORCE_HTTPS=0 -p 127.0.0.1:" + PORT + ":8000 scp-agent-startup:" + SHORT + " → poll /health ~60s.\n2. Nếu boot lỗi: chụp docker logs NGUYÊN VĂN; phân loại theo skill (ConfigContractError / production guard / port mismatch / WAL lock holder / env thiếu) — từng nguyên nhân ứng viên kèm evidence loại trừ hoặc xác nhận (file:line của guard đó).\n3. Nếu có symptom từ trước: phân tích symptom + code guard tương ứng; có thể boot tái hiện để chứng minh root cause (FA-09).\n4. Trường hợp đặc biệt: stale WAL lock (data/hands/*.sqlite3-shm/-wal) → CỐ ĐỊNH DANH holder nếu nhận diện được qua lệnh có sẵn, KHÔNG xóa file, KHÔNG restart Docker Desktop — báo NEEDS_HUMAN kèm hướng dẫn.\n5. FIX ĐỀ XUẤT: từng bước + lệnh verify sau fix; KHÔNG tự sửa.\n6. Trả đúng case: bootAttempt (nguyên văn), rootCause, proposedFix, findings, verdict (ROOT_CAUSE_FOUND/NEEDS_HUMAN/NO_ISSUE_FOUND + scope), commands."
);

phase("Đối soát viên độc lập tái hiện root-cause + ghi sổ");
const checker = await agent("Đối soát viên startup", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự tái hiện probe root-cause. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO CHẨN ĐOÁN VIÊN (lời khai — phải tái hiện mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN:\n1. Root cause khẳng định 'guard X từ chối vì điều kiện Y' → đọc guard tại file:line khai → điều kiện đúng vậy không?\n2. Nếu có container scp-agent-startup còn chạy: docker logs → lỗi khớp khai?\n3. Fix đề xuất có KHÔNG hạ chuẩn an toàn nào không (không đề xuất tắt guard/fail-open)?\n\n# DỌN DẸP (CHỈ tài sản của mình): docker rm -f scp-agent-startup; docker rmi scp-agent-startup:" + SHORT + " — báo kết quả.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-startup-troubleshooter | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-startup-audit", [
  "# Startup-troubleshooter — chassis pilot @ " + SHORT,
  "",
  "## Boot attempt (nguyên văn)",
  auditor.bootAttempt,
  "",
  "## Root cause",
  auditor.rootCause,
  "",
  "## Fix đề xuất (chưa áp dụng)",
  auditor.proposedFix,
  "",
  "## Verdict",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Cleanup: " + checker.cleanup,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Startup troubleshooter (chassis pilot)", description: "Chẩn đoán boot fail-closed @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Startup @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: [...auditor.commands, "checker tái hiện root-cause: " + checker.overall],
  notCovered: ["Áp dụng fix (thuộc lane fix khác)"],
};
return result;