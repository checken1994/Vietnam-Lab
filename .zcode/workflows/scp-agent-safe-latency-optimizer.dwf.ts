/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-safe-latency-optimizer (pilot
  chassis v1): đo latency /ask thật trên instance riêng (p50/p95 thô từng loại
  câu), phân tích pipeline code tìm bottleneck, ĐỀ XUẤT tối ưu KHÔNG BAO GIỜ tắt
  policy/verifier/revocation/egress/recovery — rồi đối soát viên re-run 1 probe
  + rà đề xuất không hạ an toàn, ghi sổ verdicts.log."
whenToUse: Cần đo latency /ask thật + tìm bottleneck + đề xuất tối ưu KHÔNG hạ
  an toàn (policy/verifier/egress giữ nguyên) — on-demand; trần probe ≤ 9 LLM
  call. Port 8044, image dùng chung scp-agent-runtime.
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
    description: Port host cho instance đo latency; tránh trùng phiên khác.
    required: false
    default: 8044
  rounds:
    type: number
    description: Số vòng đo mỗi câu probe (mặc định 3, tối đa 5 — giới hạn chi phí
      LLM call).
    required: false
    default: 3
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const PORT = String(args.port ?? 8044);
const ROUNDS = String(Math.min(Number(args.rounds ?? 3), 5));
const SKILL_PATH = ".agents/skills/scp-safe-latency-optimizer/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface LatencyAudit {
  instance: string;
  /** Số đo thô: từng probe × vòng, ms; p50/p95 tính từ dữ liệu thật. */
  measurements: string;
  /** Bottleneck phân tích từ code pipeline /ask (stage nào ngốn, file:line). */
  bottleneck: string;
  /** Đề xuất tối ưu — MỖI cái phải ghi rõ guard nào KHÔNG bị đụng. */
  proposals: string;
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
log("Chassis + skill scp-safe-latency-optimizer đã nạp @ " + SHORT);

phase("Đo latency + phân tích pipeline");
const auditor = await agent("Kiểm toán latency", {
  system: "Bạn là kiểm toán viên latency an toàn: đo trước đề xuất sau; MỌI đề xuất phải giữ nguyên policy/capability/revocation/egress/verifier/recovery/high-risk approval (luật GA A4). Không sửa file repo; trần LLM call = số probe × vòng được giao, không vượt."
}).ask<LatencyAudit>(
  BINDING + "\n\n# SKILL scp-safe-latency-optimizer (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — đo + phân tích latency /ask SCP @ D:/scp (HEAD " + HEAD + "). Instance riêng: image scp-agent-runtime:" + SHORT + " (build nếu chưa có: docker build --target runtime --build-arg SCP_GIT_SHA=" + HEAD + " -t scp-agent-runtime:" + SHORT + " D:/scp) → docker run -d --name scp-agent-lat --env-file D:/scp/.env -e SCP_HOST=0.0.0.0 -e SCP_PRODUCTION_MODE=0 -e SCP_FORCE_HTTPS=0 -p 127.0.0.1:" + PORT + ":8000 scp-agent-runtime:" + SHORT + ". KHÔNG in secret.\n\n1. ĐO: 3 loại câu × " + ROUNDS + " vòng mỗi loại (tổng ≤ 9 LLM call): nhanh (Xin chào — deterministic path), lookup (Thủ đô của Pháp là gì?), cần LLM (tin tức về paris và pháp). Ghi từng số ms thô + tính p50/p95 từ chính dữ liệu đó (khai báo cách tính). Câu withheld vẫn ghi latency của nó.\n2. PHÂN TÍCH: đọc pipeline /ask (question_router → stages → judge → delivery) — stage nào ngốn ms theo log timing nếu có (docker logs ghi timing?), code file:line của các chờ đợi lớn (LLM call, judge crosscheck, retry/hedge).\n3. ĐỀ XUẤT: tối đa 5, ưu tiên theo ms tiết kiệm được; MỖI đề xuất ghi rõ: không đụng guard nào, rollback thế nào, cần test nào pin; KHÔNG tự sửa; KHÔNG đề xuất tắt/bỏ qua policy/verifier/revocation/egress/recovery/approval (vi phạm = verdict KHÔNG ĐẠT).\n4. GIỮ container cho checker.\n5. Trả đúng case: instance, measurements, bottleneck, proposals, findings, verdict (đủ scope + số liệu nhỏ lưu ý), commands."
);

phase("Đối soát viên độc lập re-run + rà đề xuất + ghi sổ");
const checker = await agent("Đối soát viên latency", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự đo lại + rà an toàn đề xuất. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải tái hiện mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (tối đa 2 LLM call):\n1. Tự đo lại 1 probe nhanh (Xin chào) × 2 lần → ms cùng bậc với khai (chênh >3x → mâu thuẫn)?\n2. Spot-check 1 bottleneck code ref (file:line) → khớp?\n3. RÀ AN TOÀN đề xuất: từng proposal có đụng (tắt/bỏ qua/hạ) policy/verifier/revocation/egress/recovery/approval không? Đụng cái nào → ghi findings severity high.\n4. docker logs scp-agent-lat → số call của maker ≤ trần " + ROUNDS + "×3?\n\n# DỌN DẸP (CHỈ tài sản của mình): docker rm -f scp-agent-lat; image scp-agent-runtime:" + SHORT + " DÙNG CHUNG — KHÔNG rmi.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-safe-latency-optimizer | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-latency-audit", [
  "# Latency audit — chassis pilot @ " + SHORT,
  "",
  "Instance: " + auditor.instance,
  "",
  "## Số đo thô + p50/p95",
  auditor.measurements,
  "",
  "## Bottleneck (code:line)",
  auditor.bottleneck,
  "",
  "## Đề xuất tối ưu (an toàn-giữ-nguyên)",
  auditor.proposals,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Cleanup: " + checker.cleanup,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Latency audit (chassis pilot)", description: "Đo thật + đề xuất không hạ an toàn @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Latency @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: [...auditor.commands, "checker re-run + rà đề xuất: " + checker.overall],
  notCovered: ["Benchmark p95/p99 production-grade (cần load test chuyên)", "Áp dụng tối ưu (thuộc lane fix)"],
};
return result;