/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-release-evidence-gate (pilot
  chassis v1): thẩm định release candidate — commit snapshot + tree sạch, image
  build reproducibility (digest), T00 + skill-contract + reality portable, tham
  chiếu full-suite gần nhất từ sổ verdicts (không re-run), service manifest +
  health thật, rollback evidence — rồi đối soát viên verify snapshot + re-run
  gate nhanh, ghi sổ verdicts.log. Verdict chỉ là RELEASE_READY /
  CANDIDATE_NOT_PROVEN / NOT_READY."
whenToUse: Trước khi tuyên bố release/customer-handoff — kiểm candidate theo
  release-evidence-gate (snapshot + build repro + gates + suite evidence từ sổ +
  service + rollback). On-demand; nếu cần suite thật cho SHA chưa có bằng chứng
  → chạy scp-agent-reality-verifier trước. Image riêng scp-agent-release.
args:
  candidate:
    type: string
    description: Nhãn candidate release (VD 'v14.0.0 @ HEAD'); rỗng thì dùng HEAD + ngày.
    required: false
    default: ""
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const RAW_CANDIDATE = String(args.candidate ?? "").trim();
const SKILL_PATH = ".agents/skills/scp-release-evidence-gate/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); khong claim maturity tu code/test presence (FA-07).";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface ReleaseAudit {
  /** Snapshot: HEAD, git status, dirty files khai báo rõ (WIP của session khác?). */
  snapshot: string;
  /** Build reproducibility: BUILD_EXIT, image digest/manifest, stale-vs-HEAD. */
  build: string;
  /** Gates chạy thật: T00, verify_scp_test_skill_contract, reality portable — exit từng cái. */
  gates: string;
  /** Full-suite gần nhất cùng SHA (đọc .zcode/workflows/ledger/verdicts.log — KHÔNG re-run); không có → UNPROVEN. */
  suiteEvidence: string;
  /** Manifest/service thật: /health (boot nếu cần), ports, label revision. */
  service: string;
  /** Rollback: artifact/tag/undo path tồn tại không. */
  rollback: string;
  findings: Finding[];
  /** RELEASE_READY / CANDIDATE_NOT_PROVEN / NOT_READY + thiếu gì liệt kê rõ. */
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
const CANDIDATE = RAW_CANDIDATE !== "" ? RAW_CANDIDATE : "candidate @ " + SHORT;
const chassis = await files.read(CHASSIS_PATH);
const skill = await files.read(SKILL_PATH);
log("Chassis + skill scp-release-evidence-gate đã nạp @ " + SHORT);

phase("Thẩm định release candidate theo gate");
const auditor = await agent("Thẩm định release", {
  system: "Bạn là cổng release-evidence: same-SHA evidence là luật; thiếu bất kỳ mandatory gate nào → CANDIDATE_NOT_PROVEN, không manufacture green (FA-03/04/07). Không sửa file repo; container docker chỉ --rm trừ image check."
}).ask<ReleaseAudit>(
  BINDING + "\n\n# SKILL scp-release-evidence-gate (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# CANDIDATE: " + CANDIDATE + " @ " + HEAD + "\n\n# NHIỆM VỤ — thực thi gate checklist của skill trên D:/scp:\n1. SNAPSHOT: rev-parse HEAD, status --short, tree hash ngắn (git write-tree nếu sạch, hoặc khai dirty files rõ — dirty = WIP session khác thì anchor là HEAD commit không phải tree).\n2. BUILD: docker build --target runtime --build-arg SCP_GIT_SHA=" + HEAD + " -t scp-agent-release:" + SHORT + " D:/scp → BUILD_EXIT + image digest + label revision == HEAD?; build lần 2 cùng lệnh → cùng digest không (reproducibility, ghi nếu cache làm khó đánh giá).\n3. GATES (chạy thật, nhanh): python tools/t00_meta_audit.py trong container (mount ro) → exit; python tools/verify_scp_test_skill_contract.py → exit; run_reality_tests_portable.py nếu chạy được trong container → exit (không chạy được thì UNPROVEN kèm lý do).\n4. SUITE: KHÔNG re-run full suite — đọc .zcode/workflows/ledger/verdicts.log tìm line scp-agent-reality-verifier|buyer-audit cùng SHA này; có → tham chiếu GO/verdict đó; không có → mục suite UNPROVEN (bắt buộc trước RELEASE_READY).\n5. SERVICE: boot nhanh container từ image (--rm, port 127.0.0.1:8045:8000) → /health 200 + label/commit khớp → tắt ngay (docker rm -f tự quản vì --rm). KHÔNG đụng instance phiên khác.\n6. ROLLBACK: tag/commit trước đó định danh được (git tag/HEAD~), image cũ còn lưu? undo path 1 câu.\n7. VERDICT: RELEASE_READY chỉ khi snapshot sạch + build ok + gates exit 0 + suite evidence GO cùng SHA + service healthy + rollback có; thiếu cái nào liệt kê cái đó.\n8. Trả đúng case: snapshot, build, gates, suiteEvidence, service, rollback, findings, verdict, commands."
);

phase("Đối soát viên độc lập verify + ghi sổ");
const checker = await agent("Đối soát viên release", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: verify snapshot + re-run gate nhanh nhất. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# BÁO CÁO THẨM ĐỊNH (lời khai — phải đối chiếu mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN (NHANH — không re-run suite):\n1. git rev-parse HEAD + git status --short → khớp snapshot khai?\n2. docker image inspect scp-agent-release:" + SHORT + " → digest + label revision khớp khai?\n3. Re-run 1 gate nhanh nhất mà maker đã chạy (verify_scp_test_skill_contract trong container) → exit khớp?\n4. Đọc .zcode/workflows/ledger/verdicts.log → dòng suite mà maker tham chiếu có thật không, SHA khớp không?\n5. Verdict RELEASE_READY mà thiếu bất kỳ mục mandatory → NO-GO.\n\n# DỌN DẸP (CHỈ tài sản của mình): docker rmi scp-agent-release:" + SHORT + " — báo kết quả.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-release-evidence-gate | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <verdict ngắn>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-release-gate", [
  "# Release-evidence gate — chassis pilot @ " + SHORT,
  "",
  "Candidate: " + CANDIDATE,
  "",
  "## Snapshot",
  auditor.snapshot,
  "",
  "## Build reproducibility",
  auditor.build,
  "",
  "## Gates (exit từng cái)",
  auditor.gates,
  "",
  "## Suite evidence (tham chiếu sổ, không re-run)",
  auditor.suiteEvidence,
  "",
  "## Service thật",
  auditor.service,
  "",
  "## Rollback",
  auditor.rollback,
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
].join("\n"), { title: "Release-evidence gate (chassis pilot)", description: "Candidate gate @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Release gate @ " + SHORT + " (" + CANDIDATE + "): " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: [...auditor.commands, "checker verify: " + checker.overall],
  notCovered: ["Full suite re-run (dùng scp-agent-reality-verifier riêng khi cần cùng SHA)"],
};
return result;