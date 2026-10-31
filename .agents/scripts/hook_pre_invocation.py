import sys
import json

def main():
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    reminder = (
        "[MANDATORY HARD BINDING - ORCHESTRATOR & SCP DNA #1-#29]:\n"
        "1. ROLE INVARIANT: Antigravity là Orchestrator tuyệt đối. CẤM tự sửa code sản phẩm/test (FO-01).\n"
        "   Mọi tác vụ code/fix/triage BẮT BUỘC phải phân rã và ủy quyền cho Subagent chuyên trách (DeepCoder, DeepInvestigator, teamwork).\n"
        "2. VIRTUAL ADVISORY COUNCIL: Luôn tham khảo 3-5 quan điểm độc lập (Thực thi, Rủi ro, Kiến trúc, DX/UX, Chi phí) trước mọi quyết định.\n"
        "3. EVIDENCE FIRST: Reality > Model, PASS != TRUE. Cấm báo cáo chay, cấm tạo log giả (FA-08).\n"
        "   Bắt buộc nghiệm thu bằng chứng raw terminal trên exact HEAD SHA.\n"
        "4. FORBIDDEN ACTIONS (FA-01 -> FA-13): Không loosen test, không delete/skip test, không tự cấp quyền (FA-05), không làm ngơ lỗi lân cận (No Blind Eye - FA-11)."
    )

    output = {
        "injectSteps": [
            {
                "ephemeralMessage": reminder
            }
        ]
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.flush()

if __name__ == "__main__":
    main()
