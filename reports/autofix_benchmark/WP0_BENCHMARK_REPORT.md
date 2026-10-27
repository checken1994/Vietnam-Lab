# SCP Autofix Evolution Roadmap — WP0 Benchmark Report

- **Timestamp UTC:** 2026-10-27 04:46:39
- **Benchmark Status:** PASS_WITHIN_SCOPE
- **Total Seeded Cases:** 75
- **Seeded Pass Rate:** 100.00% (75/75)

---

## 1. Verified Code Claims (5/5 Invariant Audit)

| Claim | Module & Reference | Status | Evidence Detail |
|---|---|---|---|
| VerifyMixin pytest target cap [:3] & timeout 60s | `scp/autofix/engine_parts/verify_mixin.py` | **VERIFIED** | Found at lines [265, 295]; cap[:3]=True, timeout=60=True |
| Autofix LLM model fallback to openrouter/free | `scp/autofix/llm_fix_parts/_call_openrouter.py` | **VERIFIED** | Default fallback 'openrouter/free' at line [34] |
| Self-admitted 63% rollback rate in 4 key modules | `n/a` | **VERIFIED** | 63% rollback documented in 4/4 expected modules |
| Repro generator creates hardcoded assert False test | `scp/autofix/repro_generator.py` | **VERIFIED** | assert False generated at line [20] |
| Pytest gate compares pre-patch baseline vs post-patch | `scp/autofix/engine_parts/verify_mixin.py` | **VERIFIED** | Compares pre-patch baseline failures/passes to prevent false rejection |

---

## 2. Fix Ledger & Historical Metrics Audit

- **Failure Ledger (`data/failure_ledger.sqlite3`):** 15 failure records logged.
  - Active Immunity Rules: **14**
    - `MAKER_CHECKER_REJECTION [ACTIVE_IMMUNITY]`: 11
    - `WHY_REJECTION [ACTIVE_IMMUNITY]`: 3
    - `WHY_REJECTION [RESOLVED]`: 1
- **Tier 3 Auto-Audit (`data/tier3_auto_audit.jsonl`):** 72 total auto-approvals.
  - Bug types: {"HardcodedSecret": 64, "HardcodedTimeout": 8}
- **Regression Watch (`data/regression_watch.jsonl`):** 63 lifecycle events.
  - Actions: {"register": 28, "regression_detected": 7, "rollback": 7, "cleanup_expired": 21}
  - Historical Rollback Rate: **25.0%**
- **Constitutional Policy Blocks (`data/policy_blocks.jsonl`):** 159 blocked exploit attempts.
  - Patterns matched: {"verify_false_tls": 28, "type_ignore": 14, "pickle_load": 7, "bare_except_pass": 28}

---

## 3. Seeded Flaw Benchmark Tracks

| Track | Total Probes | Passed | Failed | Pass Rate |
|---|---|---|---|---|
| `track_1_deterministic_ast` | 15 | 15 | 0 | 100.0% |
| `track_2_security_policy_gate` | 15 | 15 | 0 | 100.0% |
| `track_3_clean_rollback` | 15 | 15 | 0 | 100.0% |
| `track_4_anti_placebo` | 15 | 15 | 0 | 100.0% |
| `track_5_complex_rescan` | 15 | 15 | 0 | 100.0% |

---

## 4. Architectural Invariants Verified

1. **Fail-Closed Policy Boundary:** Constitutional blocks (e.g. `verify_false_tls`, arbitrary code execution) prevent dangerous candidate patches from ever writing to disk.
2. **Drift Guard Test Integrity:** Invariants forbidding `@pytest.mark.skip` and test deletions trigger immediate `DriftDecision.DENY` (FA-02).
3. **Zero-Corruption Reversibility:** When candidate patches fail AST or targeted verifiers, files are rolled back to 100% bit-exact pre-patch SHA256 hashes.
4. **Anti-Placebo Discrimination:** Trivial, empty, comment-only, and placebo mock returns are systematically rejected.