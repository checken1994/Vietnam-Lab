# Contributing to SCP Agent OS

Welcome to the SCP Agent OS repository! We appreciate contributions that strengthen the resilience, security, and empirical reliability of the system.

Before contributing, please read this document carefully. All contributions must adhere to our strict verification and test integrity contracts.

---

## 1. Core Operating Principles (SCP DNA)

SCP is governed by the 29 principles of the **SCP DNA** (see `.agents/skills/scp-dna/SKILL.md`):
1. **Reality > Model (Thực tế > Mô hình):** Code and actual runtime evidence take precedence over theoretical models or AI conjectures.
2. **Fail-Closed by Default:** In the presence of ambiguity or missing authority, the system must fail closed (reject or escalate to human review).
3. **Evidence-First Verification:** `PASS` only means no failure was observed in the stated test scope. Claims must be backed by exact same-SHA execution logs.
4. **Zero Hardcoded Paths & Zero Hardcoded Secrets:** All filesystem paths must resolve dynamically relative to project roots, and secrets must never be committed.

---

## 2. Mandatory Pre-Commit Hooks & Quality Gates

Every contributor (human or AI agent) must set up and pass the project gates before committing:

### 2.1 Install Git Pre-Commit Hook
Run the hook installer to configure the Tier 1 pre-commit check:
```bash
python tools/install_git_hooks.py
```

The installer sets up two hooks: `pre-commit` (T00 + WHY-claims check) and `commit-msg` (WHY-Chain gate).
Every commit message and council done file must carry a `Chuỗi Tại Sao` block - template and rules in
[`docs/governance/WHY_CHAIN.md`](docs/governance/WHY_CHAIN.md); CI re-checks every commit in the range.

### 2.2 Local Verification Workflow
Before submitting any commit or Pull Request, run the required verification suite:
1. **Meta-Audit & Test Integrity Gate (T00):**
   ```bash
   python tools/t00_meta_audit.py
   ```
   *Enforces zero test regressions, no unapproved skips/xfails, and strict test preservation.*
2. **Secret Scanner:**
   ```bash
   python tools/scan_secrets.py
   ```
   *Ensures zero high-entropy API keys, tokens, or credentials exist in the diff.*
3. **Linter & Exception Suppression Audit:**
   ```bash
   ruff check scp/ --select BLE001,S110,S112
   ```
4. **Portable Reality Test Suite:**
   ```bash
   python scripts/run_reality_tests_portable.py
   ```
5. **Bounded System Smoke Test:**
   ```bash
   python tools/run_bounded_system_smoke.py
   ```

### 2.3 Anti-Placebo Gate Pattern (Mandatory Negative Tests)
Mọi cổng kiểm soát (quality gate, security validator, supply-chain tripwire) mới bắt buộc phải đi kèm ít nhất một negative-test chứng minh cổng sẽ fail-closed (từ chối / báo lỗi) khi gặp đầu vào giả mạo, đầu vào không hợp lệ hoặc nỗ lực bypass. Bài test chỉ coi là hợp lệ khi đã chứng minh được nó bắt được lỗi (không tạo placebo assertion).

---

## 3. Forbidden Actions (FA-01 through FA-13)

Violating any of the following rules is an absolute blocker enforced by `tools/t00_meta_audit.py` and GitHub Actions CI:

- **FA-01 (No Loosened Assertions):** Never weaken test assertions, add placebo assertions, or add fallbacks around asserts.
- **FA-02 (No Test Deletion/Skip):** Never delete tests or add `@pytest.mark.skip` / `pytest.skip()` without approved infrastructure contract.
- **FA-03 (No Unverified PASS Claims):** Never claim a fix without terminal output evidence on the exact commit SHA.
- **FA-04 (No Manufactured VERIFIED):** Never return `VERIFIED` from mocks or stubs.
- **FA-05 (No Self-Granted Authority):** Subsystems cannot mint their own capability tokens.
- **FA-06 (No Mutation Before Reconcile):** Reconcile baseline HEAD SHA and branch status before editing code.
- **FA-07 (No Maturity Claim From Presence):** Code presence does not equate to verified maturity.
- **FA-08 (No Forged Provenance):** Never generate simulated execution logs.
- **FA-09 (The Exploit Mandate):** Do not claim a vulnerability or bug without a reproducing terminal script.
- **FA-10 (Cross-Workspace Isolation):** Never assume external clone or workspace code matches without hash diff verification.
- **FA-11 (Mandatory Peripheral Audit):** Review adjacent security logic when modifying files.
- **FA-12 (Empirical Closure):** Verify fixes with physical execution and runtime data inspection.
- **FA-13 (Causal-Driven Tests):** Ensure coverage matrix covers all causal branches of modified logic.

---

## 4. Code & Commit Style

- **Python Version:** Python 3.12+ (required — `numpy==2.5.3` pin has no 3.10/3.11 wheels; the Docker image uses `python:3.12-slim`).
- **Commit Messages:** Follow Conventional Commits format:
  - `fix(...)`: Bug fixes and remediations
  - `feat(...)`: New architectural capabilities
  - `test(...)`: Test additions or hardening
  - `docs(...)`: Documentation and audit ledger updates
  - `refactor(...)`: Code modularization preserving 100% backward compatibility
- **TODO / FIXME Policy (QLT-03):** All TODO and FIXME comments in `scp/` must include a structured issue ID:
  - Valid: `# TODO(SEC-03): ...` or `# FIXME(GAP-01): ...` or `# [SCP-ID] TODO: ...`
  - Unstructured TODOs (e.g., `# TODO: fix this`) will be rejected by the T00 meta-audit tripwire.

Thank you for contributing to the robustness of SCP!
