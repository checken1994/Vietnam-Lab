#!/usr/bin/env python3
"""Architectural Deprecation Tombstone for tools/verify_scp_skills_pack.py.

DEPRECATION NOTICE (FA-02 Phase 1.1 Architectural Deprecation):
----------------------------------------------------------------
This markdown prompt and cosmetic frontmatter validation tool is DEPRECATED.
Historical reliance on markdown prompt eval datasets (such as
`.agents/skill-evals/skill_pack_cases.json`) has been decommissioned because
all 16 normative SCP skills are now codified physically into the kernel,
router, policy pipeline, and reality verifiers.

Sole Machine-Readable Authority:
---------------------------------
The sole normative authority for skill bindings and mandatory test-skill
contracts is `tools/verify_scp_test_skill_contract.py`, bound directly to
`.agents/skills/release-gate-skill-dna-bindings.json` and the 29 SCP DNA
principles.

When executed, this tombstone outputs this formal deprecation notice and
verifies the active contract via `tools/verify_scp_test_skill_contract.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPRECATION_NOTICE = """
================================================================================
[ARCHITECTURAL DEPRECATION NOTICE - FA-02 Phase 1.1]
tools/verify_scp_skills_pack.py is formally DEPRECATED.
- Reason: Markdown prompt validation is deprecated; all 16 normative SCP skills
  are codified physically into the kernel, router, policy pipeline, and verifiers.
- Sole Authority: tools/verify_scp_test_skill_contract.py is the machine-readable
  source of truth bound to .agents/skills/release-gate-skill-dna-bindings.json.
================================================================================
""".strip()


def main() -> int:
    print(DEPRECATION_NOTICE)
    try:
        from tools.verify_scp_test_skill_contract import DEFAULT_PROFILE, validate_contract

        evidence = validate_contract(DEFAULT_PROFILE.resolve())
        contract_status = evidence.get("status", "UNKNOWN")
    except Exception as exc:
        contract_status = f"ERROR: {exc}"

    tombstone_report = {
        "status": "DEPRECATED",
        "tombstone": "tools/verify_scp_skills_pack.py",
        "protocol": "FA-02 Phase 1.1 Architectural Deprecation",
        "reason": (
            "Markdown prompt validation is deprecated because all 16 normative "
            "SCP skills are codified physically into the kernel/router/verifier."
        ),
        "sole_authority": "tools/verify_scp_test_skill_contract.py",
        "profile": ".agents/skills/release-gate-skill-dna-bindings.json",
        "normative_skills_count": 16,
        "contract_status": contract_status,
    }
    print(json.dumps(tombstone_report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
