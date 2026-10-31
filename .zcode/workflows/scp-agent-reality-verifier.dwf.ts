/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-reality-verifier (pilot
  chassis v1): tự nạp skill từ workspace theo đúng HEAD, chạy full test suite
  SCP trong Docker theo convention chuẩn, đếm skip/xfail chống gian lận
  (FA-02/03), chạy T00 meta-audit + reality portable, rồi đối soát viên độc lập
  đếm lại log + tái hiện meta-audit, dọn image và ghi sổ verdicts.log. Chạy lâu
  (~25-40 phút)."
whenToUse: Cần kiểm chứng độ tin cậy bộ test SCP tại một HEAD cụ thể (full suite
  + chống gian lận skip + T00 meta-audit) — on-demand; đặc biệt useful sau khi
  vá để nghiệm thu 'suite xanh' claim trước khi nâng verdict mua. Chạy lâu
  ~25-40 phút, chỉ chạy khi thực sự cần.
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
const SKILL_PATH = ".agents/skills/scp-reality-verifier/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output - bang chung phai la raw stdout/HTTP exchange that (FA-08). Claim lo hong phai kem script repro da chay vang loi that, static-only thi gan nhan HYPOTHESIS (FA-09).";

interface Finding {
  severity: "high" | "medium" | "low";
  what: string;
  evidence: string;
  label: string;
}

interface SuiteAudit {
  /** Runner chuẩn tìm thấy (file + lệnh được tài liệu hóa). */
  runner: string;
  /** Lệnh chính xác đã chạy. */
  command: string;
  /** Exit code pytest. */
  exitCode: number;
  /** Dòng summary: passed/failed/error/skipped + thời gian. */
  summary: string;
  /** Skip tĩnh (grep) vs trong summary + đánh giá no-skip gate. */
  staticSkips: string;
  /** t00_meta_audit exit + summary. */
  metaAudit: string;
  /** verify_scp_test_skill_contract verdict. */
  skillContract: string;
  /** run_reality_tests_portable kết quả. */
  realityPortable: string;
  /** Path log RAW ngoài repo. */
  logPath: string;
  findings: Finding[];
  /** CREDIBLE / CREDIBLE_WITH_CAVEATS / NOT_CREDIBLE + phạm vi. */
  verdict: string;
  commands: string[];
}

interface CheckerResult {
  claims: string[];
  fabrication: string;
  cleanup: string;
  ledgerLine: string;
  overall: string;
}

interface WorkflowReport {
  conclusion: string;
  verified: string[];
  notCovered: string[];
}

phase("Nạp chassis + skill theo đúng HEAD (fail-closed FA-10)");
const head = await world.run("git", ["rev-parse", "HEAD"]);
const LIVE_HEAD = head.stdout.trim();
if (head.exitCode !== 0 || !/^[0-9a-f]{40}$/i.test(LIVE_HEAD)) {
  return {
    conclusion: "DỪNG fail-closed: không đọc được git HEAD hợp lệ (exit=" + head.exitCode + ", stdout='" + LIVE_HEAD + "') (FA-10).",
    verified: ["git rev-parse HEAD"],
    notCovered: ["toàn bộ nhiệm vụ — dừng trước khi bắt đầu"],
  };
}
if (REQ_HEAD !== "" && REQ_HEAD !== "auto" && REQ_HEAD !== "HEAD" && REQ_HEAD !== LIVE_HEAD) {
  return {
    conclusion: "DỪNG fail-closed: git HEAD hiện hành = " + LIVE_HEAD + " khác args.head_sha = " + REQ_HEAD + " (FA-10).",
    verified: ["git rev-parse HEAD"],
    notCovered: ["toàn bộ nhiệm vụ — dừng trước khi bắt đầu"],
  };
}
const HEAD = LIVE_HEAD;
const SHORT = HEAD.slice(0, 8);
const chassis = await files.read(CHASSIS_PATH);
const skill = await files.read(SKILL_PATH);
log("Chassis + skill scp-reality-verifier đã nạp @ " + SHORT);

phase("Chạy full suite + cổng meta-audit (nền, ~25-40 phút)");
const auditor = await agent("Kiểm toán test suite", {
  system: "Bạn là kiểm toán viên test-suite độc lập: các claim chất lượng có THẬT và tái hiện được không. KHÔNG làm xanh test bằng bất kỳ cách nào (FA-02/03); không bịa số liệu; không chắc ghi UNKNOWN."
}).ask<SuiteAudit>(
  BINDING + "\n\n# SKILL scp-reality-verifier (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# NHIỆM VỤ — kiểm chứng độ tin cậy bộ test SCP @ D:/scp (HEAD " + HEAD + "). KHÔNG sửa file repo (log tạm NGOÀI repo ở Temp); KHÔNG đụng container/port của phiên khác (chỉ dùng docker run --rm). Ưu tiên Docker (host python có thể là Store stub).\n\n1. SNAPSHOT: git -C D:/scp rev-parse HEAD phải == " + HEAD + "; git status --short (working tree bẩn phải khai báo — anchor bằng chứng là git HEAD, các file dirty là WIP của session khác).\n2. RUNNER CHUẨN: tìm convention được tài liệu hóa (docker-compose.test.yml / GA.md / .agents/CLEAN_ROOM_PROTOCOL.md / CI .github/workflows) — báo runner + lệnh chuẩn.\n3. FULL SUITE: docker build -t scp-agent-reality:" + SHORT + " D:/scp (ghi BUILD_EXIT; nếu Dockerfile có stage audit dùng nó, lỗi thì --target runtime và ghi rõ). Chạy suite trong container tree-embedded (convention compose.test.yml) VÀ/HOẶC mount ĐỦ repo (convention GA.md) — chạy NỀN (background) rồi poll log, KHÔNG để timeout ~10 phút giết giữa chừng (suite ~20-40 phút; có thể cần 2 conventions nếu 1 gãy collection). Lưu RAW output vào Temp ngoài repo (ghi path vào logPath). Ghi: lệnh chính xác, exit code, passed/failed/error/skipped/deselected, thời gian. KHÔNG ngụy trang subset thành full suite — chạy không hết thì báo nguyên trạng.\n4. CHỐNG GIAN LẬN (FA-02/03): (a) grep -rE '@pytest.mark.(skip|xfail)' D:/scp/tests | wc -l đếm tĩnh + phân loại (literal trong self-test gate vs skip thật) + số skipped trong summary; (b) python tools/t00_meta_audit.py trong container — exit + summary; (c) python tools/verify_scp_test_skill_contract.py — exit + verdict; (d) run_reality_tests_portable.py nếu có — kết quả.\n5. Phân loại failure (nếu có): regression ổn định cả 2 conventions / pollution-dependent (fail full pass riêng) / convention-specific (tree-embedded vs mount) / môi trường (stall I/O...). KHÔNG claim 'suite xanh' nếu exit != 0.\n6. Image KHÔNG xóa — để checker dọn. Container dùng --rm hết.\n7. Trả đúng case: runner, command, exitCode, summary, staticSkips, metaAudit, skillContract, realityPortable, logPath, findings, verdict (CREDIBLE / CREDIBLE_WITH_CAVEATS / NOT_CREDIBLE + scope), commands."
);

phase("Đối soát viên độc lập đếm lại + dọn + ghi sổ");
const checker = await agent("Đối soát viên reality", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự tay tái hiện claim then chốt rồi mới kết luận. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO KIỂM TOÁN VIÊN (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN ĐỘC LẬP (NHANH — KHÔNG re-run full suite):\n1. Đọc log pytest tại logPath trong báo cáo → ĐẾM LẠI passed/failed/error/skipped từ summary khớp con số khai? (kênh: grep/tail log thật)\n2. Chạy lại tools/t00_meta_audit.py trong container scp-agent-reality:" + SHORT + " (mount D:/scp:/app hoặc tree-embedded) → exit khớp khai?\n3. grep đếm skip tĩnh → khớp staticSkips?\n4. docker image inspect scp-agent-reality:" + SHORT + " tồn tại khớp khai?\n5. Nếu logPath không đọc được hoặc số lệch → ghi UNKNOWN/mâu thuẫn vào fabrication.\n\n# DỌN DẸP (CHỈ tài sản của mình): docker rmi scp-agent-reality:" + SHORT + " (báo kết quả).\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-reality-verifier | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-reality-audit", [
  "# Reality-verifier audit — chassis pilot @ " + SHORT,
  "",
  "- Runner: " + auditor.runner,
  "- Lệnh: " + auditor.command,
  "- Kết quả: exit=" + auditor.exitCode + " — " + auditor.summary,
  "- Skip/xfail: " + auditor.staticSkips,
  "- T00 meta-audit: " + auditor.metaAudit,
  "- Skill contract: " + auditor.skillContract,
  "- Reality portable: " + auditor.realityPortable,
  "- Log RAW: " + auditor.logPath,
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
  "",
  "## Phát hiện",
  ...(auditor.findings.length > 0 ? auditor.findings.map((f) => "- [" + f.severity.toUpperCase() + "/" + f.label + "] " + f.what) : ["- Không có."]),
].join("\n"), { title: "Reality-verifier audit (chassis pilot)", description: "Full suite + meta-audit + anti-gaming @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Reality-verifier @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + " — fabrication: " + checker.fabrication + ".",
  verified: [...auditor.commands, "checker đếm lại log + meta-audit: " + checker.overall],
  notCovered: ["Mutation battery (audit_mutants) chưa nằm trong pilot này", "Chi tiết findings xem artifact"],
};
return result;