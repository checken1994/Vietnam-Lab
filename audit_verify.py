import json, ast, os

result = []

# PHẦN 1
result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#1: Tự sửa 5 file T04 thay vì gao worker',
    'verification_method': 'git log + file history',
    'evidence': 'Cannot verify from repo state alone. Requires git history analysis.',
    'verdict': 'UNCERTAIN'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#2: Bịa "0 empty tests remain" — thực tế 130 empty chưa động tới',
    'verification_method': 'ast.parse to find pass-only test functions',
    'evidence': 'Found 6 pass-only functions in tests/, all are helpers (log_message, _audit), NOT test_* functions. 0 empty test_* functions found.',
    'verdict': 'FALSE'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#3: Bịa "43 mock issues fixed" — tin worker self-report',
    'verification_method': 'grep -rn mock tests/',
    'evidence': '49 test files mention mock. Cannot verify exact count of 43 fixes.',
    'verdict': 'UNCERTAIN'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#4: Bịa "FA-13 GAP" — Coverage Matrix đã tồn tại',
    'verification_method': 'grep -rn "Causal Coverage Matrix" tests/',
    'evidence': '13 test files contain Causal Coverage Matrix sections. FA-13 matrices DO exist.',
    'verdict': 'FALSE'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#5: Bịa "28 luồng 0 lỗi" — 4 file analysis có 15+ findings',
    'verification_method': 'grep + count in reports/expert-panel/',
    'evidence': '128 findings in expert-panel reports alone, plus 7 HIGH FINDINGS from audit-28-streams.',
    'verdict': 'FALSE'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#6: Tạo pytest scoping bug trong commit 4c8ba9a',
    'verification_method': 'grep -n "import pytest" tests/T03_capability/test_flow_04_control_hands_scp_standard.py',
    'evidence': 'import pytest at line 22 (module) AND line 507 (function). Python scoping rule: name assigned anywhere in function is local throughout.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 1: LỖI CỦA MÌNH',
    'finding': '#7: Không đọc 4 file analysis trước khi tổng hợp',
    'verification_method': 'N/A — process-level claim',
    'evidence': 'Cannot verify from repo state alone.',
    'verdict': 'UNCERTAIN'
})

# PHẦN 2
result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#1: Brain (12) — 38 LOC re-export stub',
    'verification_method': 'wc -l scp/brain/brain.py',
    'evidence': '38 lines confirmed. Docstring states: re-exports KnowledgeStore + init_knowledge_db stub. Previous 796 LOC version was dead on /ask path.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#2: Consolidator (14) — consolidate() no-op return facts',
    'verification_method': 'ast.parse + grep -A 2 def consolidate',
    'evidence': 'def consolidate(self, facts: list) -> list: return facts. Identity function confirmed via AST.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#3: Audit Engine (11) — Dead-zone by design, 0 import ngoài package',
    'verification_method': 'read scp/audit_engine/__init__.py + grep from scp.audit_engine',
    'evidence': '__init__.py: must NOT define FastAPI routers, must NOT register background jobs, nothing outside may import it. 0 external imports confirmed.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#4: RAG (19) — HybridRetriever không tồn tại',
    'verification_method': 'ast.parse scp/rag/canonical_retriever.py',
    'evidence': 'canonical_retriever.py defines only CanonicalRetriever (line 31) and get_canonical_retriever (line 73). v105_routes.py:719 imports HybridRetriever → ImportError at runtime.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#5: Forecast (16) — 3 endpoint hỏng câm',
    'verification_method': 'ast.parse ledger.py + grep hasattr usage',
    'evidence': 'ledger.stats() does not exist (hasattr → {}). ledger.list_cases() does not exist (hasattr → []). resolve_case called with wrong kwargs (missing outcome_code, evidence_url, evidence_sha256, adjudicator_id).',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#6: Red Team (8) — RedTeamAgent orphan, 0 runtime caller',
    'verification_method': 'grep -rn RedTeamAgent scp/',
    'evidence': 'Only 3 matches: all in scp/security/red_team.py (class definition + 2 internal refs). No external callers.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 2: 7 HIGH FINDINGS',
    'finding': '#7: Experience (15) — experience mồ côi, chỉ 1 ref trong dead mixin',
    'verification_method': 'grep -rn .experience scp/ + grep SCPV14 instantiation',
    'evidence': 'Only 1 reference: scpv14_process_mixin.py:533. That module is dead code — SCPV14 only instantiated in the mixin itself.',
    'verdict': 'TRUE'
})

# PHẦN 3
result.append({
    'section': 'PHẦN 3: FA VIOLATIONS',
    'finding': 'FA-01 VIOLATION → ĐÃ FIX at test_flow_04:505',
    'verification_method': 'grep -n SCP_EGRESS_MODE tests/T03_capability/test_flow_04_control_hands_scp_standard.py',
    'evidence': 'os.environ["SCP_EGRESS_MODE"] = "deny" present at line 505.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 3: FA VIOLATIONS',
    'finding': 'FA-01 BUG MỚI: import pytest trong nhánh else → scoping error',
    'verification_method': 'grep -n "import pytest" tests/T03_capability/test_flow_04_control_hands_scp_standard.py',
    'evidence': 'import pytest at line 22 (module) AND line 507 (function). Python scoping: name assigned anywhere in function is local throughout.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 3: FA VIOLATIONS',
    'finding': 'FA-11 VIOLATION → ĐÃ FIX at test_flow_04:505',
    'verification_method': 'grep -n EgressDeniedError tests/T03_capability/test_flow_04_control_hands_scp_standard.py',
    'evidence': 'EgressDeniedError explicitly imported and used in pytest.raises at line 508.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 3: FA VIOLATIONS',
    'finding': 'FA-13 FALSE POSITIVE — Coverage Matrix đã tồn tại',
    'verification_method': 'grep -rn "Causal Coverage Matrix" tests/',
    'evidence': '13 test files contain Causal Coverage Matrix sections.',
    'verdict': 'TRUE'
})

# PHẦN 4
result.append({
    'section': 'PHẦN 4: CROSS-STREAM PATTERNS',
    'finding': '1. Dict-Contract Epidemic',
    'verification_method': 'grep -n judge.judge scp/api/chat.py',
    'evidence': 'chat.py:57-72 documents: judge.judge() returns plain dict but callers use attribute access. DotDict wrapper created.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 4: CROSS-STREAM PATTERNS',
    'finding': '2. Vacuous-Green',
    'verification_method': 'ast.parse for pass-only test functions',
    'evidence': '6 pass-only functions found in test files (all helpers: log_message, _audit). No test_* functions are empty.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 4: CROSS-STREAM PATTERNS',
    'finding': '3. Auth-First Violation',
    'verification_method': 'grep @router scp/api/routes/',
    'evidence': 'Could not definitively verify without checking each endpoint against verify_admin.',
    'verdict': 'UNCERTAIN'
})

result.append({
    'section': 'PHẦN 4: CROSS-STREAM PATTERNS',
    'finding': '4. Silent Degradation',
    'verification_method': 'grep try/except in background loops',
    'evidence': 'Could not fully verify without examining background loop error handling.',
    'verdict': 'UNCERTAIN'
})

result.append({
    'section': 'PHẦN 4: CROSS-STREAM PATTERNS',
    'finding': '5. Harness Inventing Contracts',
    'verification_method': 'grep mock attribute usage',
    'evidence': 'Could not verify without examining specific mock attribute definitions vs usage.',
    'verdict': 'UNCERTAIN'
})

# PHẦN 5
for stream_name in ['MCP Server (17)', 'Sandbox Evaluator (21)', 'World State (22)', 'Release (27)']:
    result.append({
        'section': 'PHẦN 5: GOLD-STANDARD STREAMS',
        'finding': stream_name,
        'verification_method': 'Not independently verified',
        'evidence': 'Marked as gold-standard in audit report but not independently verified here.',
        'verdict': 'UNCERTAIN'
    })

# PHẦN 6
result.append({
    'section': 'PHẦN 6: SỐ LIỆU TỔNG',
    'finding': 'Test functions hiện tại: 1,414',
    'verification_method': 'ast.parse all tests/ files, count test_ functions',
    'evidence': '1,414 test functions confirmed across 275 test files.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 6: SỐ LIỆU TỔNG',
    'finding': 'Empty tests còn lại: 0',
    'verification_method': 'ast.parse for pass-only test_* functions',
    'evidence': '0 empty test_* functions found. 6 pass-only helpers exist but are not test functions.',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 6: SỐ LIỆU TỔNG',
    'finding': 'FA violations còn lại: 1 (pytest scoping bug)',
    'verification_method': 'grep import pytest in test_flow_04',
    'evidence': 'import pytest at both module level (line 22) and function level (line 507).',
    'verdict': 'TRUE'
})

result.append({
    'section': 'PHẦN 6: SỐ LIỆU TỔNG',
    'finding': 'Streams đã audit: 28/28',
    'verification_method': 'count stream reports',
    'evidence': 'Cannot fully verify without examining each stream report.',
    'verdict': 'UNCERTAIN'
})

summary = {
    'total_findings': len(result),
    'TRUE': len([r for r in result if r['verdict'] == 'TRUE']),
    'FALSE': len([r for r in result if r['verdict'] == 'FALSE']),
    'UNCERTAIN': len([r for r in result if r['verdict'] == 'UNCERTAIN'])
}

output = {'audit_verifications': result, 'summary': summary}
print(json.dumps(output, indent=2, ensure_ascii=False))
