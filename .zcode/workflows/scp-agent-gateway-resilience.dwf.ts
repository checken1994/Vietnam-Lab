/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-gateway-resilience (pilot
  chassis v1): audit LLM gateway/circuit breaker/fallback/rate-limit — cấu hình
  code + probe live giới hạn (≤ args.probes call) trên instance riêng port 8042,
  quan sát routing + hành vi fail-closed khi provider cấu hình chết — rồi đối
  soát viên tái hiện, dọn dẹp, ghi sổ verdicts.log."
whenToUse: Cần audit LLM gateway/circuit breaker/fallback/rate-limit của SCP tại
  một HEAD cụ thể — on-demand; trần chi phí LLM call mặc định 5. Port 8042,
  image dùng chung scp-agent-runtime.
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
    description: Port host cho instance gateway audit; tránh trùng phiên khác.
    required: false
    default: 8042
  probes:
    type: number
    description: Số LLM call tối đa cho probe live (giới hạn chi phí; mặc định 5).
    required: false
    default: 5
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const PORT = String(args.port ?? 8042);
const PROBES = String(args.probes ?? 5);
const SKILL_PATH = ".agents/skills/scp-gateway-resilience/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); claim loi phai co repro that (FA-09).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface GatewayAudit {
  instance: string;
  /** Cấu hình gateway quan sát được: providers, fallback chain, circuit breaker params (từ code + /health). */
  config: string;
  /** Probe live: số call đã bắn, routing Observed, latency, hành vi khi provider chết. */
  liveProbes: string;
  /** Rate-limit/fail-closed: kết quả quan sát. */
  guards: string;
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
log("Chassis + skill scp-gateway-resilience đã nạp @ " + SHORT);

phase("Audit cấu hình + probe live gateway");
const auditor = await agent("Kiểm toán gateway", {
  system: "Bạn là kiểm toán viên gateway-resilience: cấu hình code + hành vi sống phải khớp nhau; không tin cấu hình giấy — phải probe. Giới hạn chi phí: tuyệt đối không vượt trần LLM call được giao. Không bịa; không chắc ghi UNKNOWN."
}).ask<GatewayAudit>(
  BINDING + "\n\n# SKILL scp-gateway-resilience (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — audit LLM gateway SCP @ D:/scp (HEAD " + HEAD + "). Instance riêng: nếu image scp-agent-runtime:" + SHORT + " đã có thì dùng, chưa có thì docker build --target runtime --build-arg SCP_GIT_SHA=" + HEAD + " -t scp-agent-runtime:" + SHORT + " D:/scp; docker run -d --name scp-agent-gw --env-file D:/scp/.env -e SCP_HOST=0.0.0.0 -e SCP_PRODUCTION_MODE=0 -e SCP_FORCE_HTTPS=0 -p 127.0.0.1:" + PORT + ":8000 scp-agent-runtime:" + SHORT + ". CẤM sửa file repo; KHÔNG in secret (.env — DÙNG SCP_ADMIN_KEY auth, không echo).\n\n1. CẤU HÌNH (code + runtime): đọc scp/llm_gateway/ (client/providers/fallback/circuit) + /health + /health/detailed → liệt kê providers active (tên thôi), fallback chain, circuit breaker params (ngưỡng, cooldown), timeout/hedge policy. So code vs runtime: lệch gì ghi ra.\n2. PROBE LIVE — Trần CỨNG " + PROBES + " LLM call tổng: (a) 2 câu /ask lành đo routing (provider nào trả) + latency; (b) 1-2 call với provider cấu hình CHẾT (override -e SCP_LLM_FALLBACK_PROVIDERS= http://127.0.0.1:9 chết khi boot container THỨ HAI dùng-bỏ scp-agent-gw-dead, KHÔNG sửa container chính) → quan sát circuit/fallback/escalate hành vi thật; (c) xác nhận không retry-vô-hạn (đếm log).\n3. GUARDS: rate-limit gateway tier nếu có; egress deny vẫn thắng khi SCP_EGRESS_MODE=deny (đọc code pin + test hiện có, không cần probe mạng thật).\n4. KHÔNG đụng container instance khác; GIỮ scp-agent-gw chạy cho checker.\n5. Trả đúng case: instance, config, liveProbes (đếm call đã bắn < trần), guards, findings, verdict (đủ scope), commands."
);

phase("Đối soát viên độc lập tái hiện + dọn dẹp + ghi sổ");
const checker = await agent("Đối soát viên gateway", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự tay tái hiện claim then chốt. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải tái hiện mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (tối đa 2 LLM call nếu cần — trong trần tổng của cả bản thể):\n1. GET http://127.0.0.1:" + PORT + "/health → providers/config khớp khai?\n2. 1 câu /ask lành có auth → routing khớp claim? (KEY từ .env, không echo)\n3. Spot-check 1 config param circuit/fallback trong code (file:line) → khớp khai?\n4. docker logs scp-agent-gw → số LLM call mà maker bắn có vượt trần " + PROBES + " không (vượt = vi phạm chi phí, ghi findings)?\n\n# DỌN DẸP (CHỈ tài sản của mình): docker rm -f scp-agent-gw scp-agent-gw-dead (cái nào tồn tại); image scp-agent-runtime:" + SHORT + " là TÀI SẢN DÙNG CHUNG của các bản thể — KHÔNG rmi.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-gateway-resilience | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-gateway-audit", [
  "# Gateway-resilience audit — chassis pilot @ " + SHORT,
  "",
  "Instance: " + auditor.instance,
  "",
  "## Cấu hình quan sát được",
  auditor.config,
  "",
  "## Probe live (trần " + PROBES + " call)",
  auditor.liveProbes,
  "",
  "## Guards",
  auditor.guards,
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
].join("\n"), { title: "Gateway-resilience audit (chassis pilot)", description: "Cấu hình + probe live giới hạn @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Gateway audit @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: [...auditor.commands, "checker tái hiện: " + checker.overall],
  notCovered: ["Failover giữa nhiều provider thật với key thật ngoài trần probe", "Chi tiết findings xem artifact"],
};
return result;