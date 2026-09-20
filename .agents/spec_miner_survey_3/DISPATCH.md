## 2026-09-20T07:17:02Z
MANDATORY BINDING: You are strictly bound by Zero-Trust and Fail-Closed principles. You MUST adhere to FA-01 through FA-13. You are FORBIDDEN from self-granting authority or simulating PASS results. Any code modifications must explicitly enforce boundaries at the Database/Hardware level, not via RAM/Variables.

You are a Specification Miner investigating the SCP codebase for Milestone Planning.
Your working directory is: c:\Users\check\Downloads\scp\.agents\spec_miner_survey_3\
Authoritative user request: c:\Users\check\Downloads\scp\.agents\ORIGINAL_REQUEST.md (read this file first).

Relevant skills: View and follow c:\Users\check\Downloads\scp\.agents\skills\scp-dna\SKILL.md.

TASK:
Investigate the specifications, guarded invariants, meta-audit rules, and test architecture in SCP to support Requirement R3 and Acceptance Criteria:
"R3. Strict Compliance with SCP Invariants: Mọi mã nguồn sửa đổi bắt buộc tuân thủ bộ nguyên lý Zero-Trust và các luật FA-01 đến FA-13 của dự án SCP (đọc trong .agents/AGENTS.md). Tuyệt đối không được "hack" xanh các bài test bằng cách xóa (skip/xfail) hoặc làm suy yếu assertion. Cần đảm bảo Meta-Audit T00 không bắt lỗi regression.
Acceptance Criteria:
- [State Machine Verification]: Có ít nhất 1 bài test tích hợp (Integration Test) chạy tự động bằng mã nguồn chứng minh được: Một task đi từ đầu đến cuối vòng đời (lifecycle) thành công mà không hề bị chặn lại ở trạng thái HUMAN_REVIEW.
- [System Integrity]: Toàn bộ bộ test cốt lõi liên quan (chạy qua pytest tests/) phải PASS 100%, chứng minh không phá vỡ tính đúng đắn của các tính năng song song khác.
- Lệnh chạy python tools/t00_meta_audit.py báo cáo "0 regressions" (không có vi phạm FA rules)."

Focus Areas:
1. Check tools/t00_meta_audit.py, tools/install_git_hooks.py, and .github/workflows/scp_guardrails.yml. What does t00_meta_audit.py check? What AST patterns, forbidden actions, or regressions does it detect?
2. Check spec/protected_invariants.yaml, spec/complete_scp_reference.yaml, and spec/scp_future_target_manifest.yaml (if present) for protected invariants regarding state machine, human review, and capability tokens.
3. Check the test suite structure: How are tests organized (tests/T00_meta, tests/T01_boot, tests/T02_contract, tests/T03_capability, tests/T04_kernel, etc.)? How is the test runner invoked?
4. Formulate the precise acceptance requirements and regression prevention guidelines for this project.

OUTPUT:
Write your comprehensive findings to c:\Users\check\Downloads\scp\.agents\spec_miner_survey_3\handoff.md.
Then send a message back to the orchestrator with a concise summary and link to your report.
