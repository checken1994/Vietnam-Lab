const fs = require('fs');

function main() {
  let raw = '';
  try {
    raw = fs.readFileSync(0, 'utf-8');
  } catch (e) {
    // Ignore read error
  }

  const reminder = [
    "[MANDATORY HARD BINDING - ORCHESTRATOR & SCP DNA #1-#29]:",
    "1. ROLE INVARIANT: Antigravity là Orchestrator tuyệt đối. CẤM tự sửa code sản phẩm/test (FO-01).",
    "   Mọi tác vụ code/fix/triage BẮT BUỘC phải phân rã và ủy quyền cho Subagent chuyên trách (DeepCoder, DeepInvestigator, teamwork).",
    "2. VIRTUAL ADVISORY COUNCIL: Luôn tham khảo 3-5 quan điểm độc lập (Thực thi, Rủi ro, Kiến trúc, DX/UX, Chi phí) trước mọi quyết định.",
    "3. EVIDENCE FIRST: Reality > Model, PASS != TRUE. Cấm báo cáo chay, cấm tạo log giả (FA-08).",
    "   Bắt buộc nghiệm thu bằng chứng raw terminal trên exact HEAD SHA.",
    "4. FORBIDDEN ACTIONS (FA-01 -> FA-13): Không loosen test, không delete/skip test, không tự cấp quyền (FA-05), không làm ngơ lỗ hổng lân cận (No Blind Eye - FA-11)."
  ].join("\n");

  const output = {
    injectSteps: [
      {
        ephemeralMessage: reminder
      }
    ]
  };

  process.stdout.write(JSON.stringify(output));
}

main();
