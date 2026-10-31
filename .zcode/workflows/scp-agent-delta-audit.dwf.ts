/* zcode-workflow
description: "Bản thể agent độc lập cho skill scp-delta-audit (pilot chassis
  v1): chứng minh/bác bỏ một giả thuyết gap kiến trúc/bảo mật bằng bằng chứng —
  probe-before-patch, phân tích nhân quả, anti-placebo mutation, bắt buộc script
  repro văng lỗi thật (FA-09) — rồi đối soát viên tái hiện probe chính và ghi sổ
  verdicts.log. Chỉ-đọc đối với repo, mutation chỉ chạy trong môi trường tạm."
whenToUse: "Cần chứng minh/bác bỏ một giả thuyết gap kiến trúc/bảo mật bằng bằng
  chứng sống (FA-09: không static-only) — on-demand, chỉ-đọc repo, mutation
  trong bản sao tạm. Truyền bắt buộc args.hypothesis."
args:
  head_sha:
    type: string
    description: Commit HEAD đầy đủ để neo phiên (để trống hoặc "auto" sẽ tự động
      detect git rev-parse HEAD hiện hành; nếu truyền SHA cụ thể mà lệch git HEAD
      sẽ DỪNG fail-closed theo FA-10).
    required: false
    default: ""
  hypothesis:
    type: string
    description: Giả thuyết gap/lỗ hổng cần chứng minh bằng bằng chứng (1-3 câu, kèm
      vị trí nếu có).
    required: true
*/
const REQ_HEAD = String(args.head_sha ?? "").trim().replace(/^auto$/i, "auto").replace(/^head$/i, "HEAD").replace(/^[0-9a-f]{40}$/i, (m) => m.toLowerCase());
const HYP = String(args.hypothesis ?? "");
const SKILL_PATH = ".agents/skills/scp-delta-audit/SKILL.md";
const CHASSIS_PATH = ".zcode/workflows/chassis/agent-chassis.md";
const BINDING = "MANDATORY BINDING: You are strictly bound by Fail-Closed and Evidence-First principles (SCP DNA #1-#29). You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Khong bia output (FA-08); BAT BUOC FA-09: khong duoc claim lo hong chi bang static AST - phai viet va chay script repro doc lap, script khong vang loi thi gia thuyet phai bi loai bo.";

interface Finding { severity: "high" | "medium" | "low"; what: string; evidence: string; label: string; }
interface DeltaAudit {
  /** Phần thân báo cáo markdown: causal chain + probe results + mutation results. */
  report: string;
  findings: Finding[];
  /** PROVEN / NOT_PROVEN / UNPROVEN + 2 câu bằng chứng + scope. */
  verdict: string;
  /** Script repro đã chạy + output nguyên văn (hoặc lý do không chạy được). */
  repro: string;
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
log("Chassis + skill scp-delta-audit đã nạp @ " + SHORT);

phase("Probe giả thuyết + anti-placebo mutation");
const auditor = await agent("Điều tra viên delta", {
  system: "Bạn là điều tra viên delta-audit: probe TRƯỚC khi kết luận, nhân quả trước bổ hoại, mutation phải chứng minh cổng kiểm soát thật sự bắt được phá hoại. FA-09 là luật sắt: không repro thật thì không được claim lỗ hổng."
}).ask<DeltaAudit>(
  BINDING + "\n\n# SKILL scp-delta-audit (nạp RUNTIME từ workspace — nguồn duy nhất):\n" + skill + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# GIẢ THUYẾT CẦN THẨM ĐỊNH (args.hypothesis):\n" + HYP + "\n\n# NHIỆM VỤ @ D:/scp (HEAD " + HEAD + ") — thực thi đúng Evidence-First loop của skill: (1) xác định assumption + evidence còn thiếu; (2) probe-before-patch: viết script probe ĐỘC LẬP trong Temp ngoài repo, chạy thật, ghi output nguyên văn; (3) vẽ causal chain Trigger → Local Failure → System Impact; (4) nếu giả thuyết nói về 1 cổng kiểm soát: mutation test — phá cổng trong BẢN SAO tạm (không đụng repo sống) chứng minh test/tripwire bắt được (anti-placebo); (5) phân loại verdict PROVEN (repro văng lỗi thật) / NOT_PROVEN (probe không tái hiện) / UNPROVEN (môi trường không cho chạy). CẤM sửa file trong D:/scp; mutation chỉ trong bản sao tạm; KHÔNG tự vá (probe-before-patch — vá là việc của lane khác). Script tạm ghi rõ path để checker tái hiện. Trả đúng case: report, findings (nhãn bắt buộc), verdict, repro, commands."
);

phase("Đối soát viên độc lập tái hiện probe + ghi sổ");
const checker = await agent("Đối soát viên delta", {
  system: "Bạn là đối soát viên độc lập — không tin lời khai: tự chạy lại script repro/probe chính để xác nhận kết quả. Không bịa; không chắc ghi UNKNOWN; bất đồng → UNKNOWN + ghi nội dung."
}).ask<CheckerResult>(
  BINDING + "\n\n# CHASSIS (6 luật):\n" + chassis + "\n\n# GIẢ THUYẾT GỐC:\n" + HYP + "\n\n# BÁO CÁO ĐIỀU TRA VIÊN (lời khai — phải tái hiện mới tin):\n" + JSON.stringify(auditor) + "\n\n# TÁI HIỆN:\n1. Tìm script repro/probe mà báo cáo khai (path trong Temp) → tồn tại không, CHẠY LẠI đúng lệnh → output khớp claim (văng lỗi/không văng)?\n2. Spot-check 1-2 evidence code refs (file:line) trong báo cáo bằng đọc thật → khớp?\n3. Nếu verdict PROVEN mà không có script chạy được → fabrication.\n\n# DỌN DẸP: chỉ-đọc repo — script tạm của maker GIỮ NGUYÊN (để truy vết); ghi 'script tạm giữ tại path, không có tài nguyên docker'.\n\n# GHI SỔ (luật chassis 6): date '+%Y-%m-%d %H:%M'; mkdir -p .zcode/workflows/ledger; append 1 dòng vào .zcode/workflows/ledger/verdicts.log: '<time> | scp-agent-delta-audit | " + SHORT + " | <GO|NO-GO|UNKNOWN> | <tóm tắt>'. Trả ledgerLine.\n\nTrả đúng case: claims, fabrication, cleanup, ledgerLine, overall."
);

await artifact.markdown("bao-cao-delta-audit", [
  "# Delta audit — chassis pilot @ " + SHORT,
  "",
  "Giả thuyết: " + HYP.slice(0, 300),
  "",
  auditor.report,
  "",
  "## Repro (FA-09)",
  auditor.repro,
  "",
  "## Verdict kiểm toán viên",
  auditor.verdict,
  "",
  "## Đối soát độc lập",
  ...checker.claims.map((c) => "- " + c),
  "- Fabrication: " + checker.fabrication,
  "- Sổ: " + checker.ledgerLine,
  "- **Overall: " + checker.overall + "**",
].join("\n"), { title: "Delta audit (chassis pilot)", description: "Probe + mutation + FA-09 repro @ " + SHORT, primary: true });

const result: WorkflowReport = {
  conclusion: "Delta audit @ " + SHORT + ": " + auditor.verdict + " Đối soát: " + checker.overall + ".",
  verified: ["checker re-run repro: " + checker.overall],
  notCovered: ["Việc vá (nếu PROVEN) thuộc lane fix khác — bản thể này chỉ chứng minh"],
};
return result;