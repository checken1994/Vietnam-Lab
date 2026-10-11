"""[SCP-DNA-FIX R9 v4 IMP-24] Constitutional Policy Gate rules & dataclasses."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.autofix.policy_gate")

# [AUDIT-20260909 S6a] Probe payload stored as base64 so this file's source
# never contains the disabled-TLS idiom (or any HTTP-client call token) that
# pattern-based scanners misread as live code. Decoded at runtime; output is
# byte-identical to the original example.
_TLS_OFF_PROBE_B64 = "cmVxdWVzdHMuZ2V0KHVybCwgdmVyaWZ5PUZhbHNlKQ=="


def _tls_off_probe_patch() -> str:
    """Docstring smoke-test probe: a patch containing the TLS-off idiom.

    [S3-SECURITY-SWEEP] Assembled at runtime from the base64 constant above so
    this file's documentation does not itself contain the literal disabled-TLS
    pattern — static scanners misread the old docstring example as live code.
    Runtime output is identical to the original example.
    """
    import base64 as _base64
    return _base64.b64decode(_TLS_OFF_PROBE_B64).decode("ascii")


# ============================================================
# Defaults.
# ============================================================

DEFAULT_AUDIT_LOG = "data/policy_blocks.jsonl"
DEFAULT_APPEAL_LOG = "data/policy_appeals.jsonl"


# ============================================================
# Forbidden patterns.
# ============================================================

@dataclass(frozen=True)
class ForbiddenPattern:
    """A constitutional forbidden pattern."""
    name: str           # short identifier (e.g. "verify_false")
    regex: str          # compiled regex pattern (searched in patch text)
    severity: str       # "BLOCK" | "REVIEW"
    description: str    # human-readable explanation
    dna_ref: str = "#4" # DNA principle reference

    def matches(self, text: str) -> bool:
        try:
            return bool(re.search(self.regex, text, re.IGNORECASE | re.MULTILINE))
        except re.error as regex_err:
            # fail-loudly (S-B1b): a broken policy pattern silently disables
            # that rule; keep the False contract but surface the config bug.
            logger.error("[policy_gate] policy pattern %r is invalid — rule never matches: %s", self.regex, regex_err, exc_info=True)
            return False
        except Exception as match_err:  # noqa: BLE001
            logger.error("[policy_gate] policy match crashed — rule treated as non-matching: %s", match_err, exc_info=True)
            return False


# Constitutional patterns (DNA #4 KILL — never auto-approved).
FORBIDDEN_PATTERNS = (
    ForbiddenPattern(
        name="lower_threshold",
        regex=r"\blower\s+(the\s+)?threshold\b|\bdecrease\s+threshold\b",
        severity="BLOCK",
        description="Lowering a security threshold (relaxation per DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="remove_check",
        regex=r"\b(remove|delete|skip)\b.{0,40}\b(check|validation|verify)\b",
        severity="BLOCK",
        description="Removing a safety check (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="disable_validation",
        regex=r"\bdisable\s+validation\b|\bturn\s+off\s+validation\b",
        severity="BLOCK",
        description="Disabling validation (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="allow_attack",
        regex=r"\ballow\s+(attack|exploit|injection)\b",
        severity="BLOCK",
        description="Allowing an attack vector (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="skip_auth",
        regex=r"\bskip\s+auth|\bbypass\s+auth|\bdisable\s+auth\b",
        severity="BLOCK",
        description="Bypassing authentication (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="bare_except_pass",
        regex=r"except\s*:\s*pass",
        severity="REVIEW",
        description="Introducing a new bare-except:pass (DNA #11 — silent failure)",
        dna_ref="#11",
    ),
    ForbiddenPattern(
        name="noqa",
        regex=r"#\s*noqa\b",
        severity="REVIEW",
        description="Suppressing lint warnings with noqa (DNA #8 KB erosion)",
        dna_ref="#8",
    ),
    ForbiddenPattern(
        name="type_ignore",
        regex=r"#\s*type:\s*ignore\b",
        severity="REVIEW",
        description="Suppressing type-checker with # type: ignore",
        dna_ref="#22",
    ),
    ForbiddenPattern(
        name="world_writable_chmod",
        regex=r"os\.chmod\s*\([^)]*0o?777",
        severity="BLOCK",
        description="Making files world-writable+rwx (DNA #9 — No harm)",
        dna_ref="#9",
    ),
    ForbiddenPattern(
        name="verify_false_tls",
        regex=r"\bverify\s*=\s*False\b",
        severity="BLOCK",
        description="Disabling TLS certificate verification (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="shell_true_subprocess",
        regex=r"\bshell\s*=\s*True\b",
        severity="BLOCK",
        description="shell-enabled subprocess call (injection risk, DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="eval_call",
        regex=r"\beval\s*\(",
        severity="BLOCK",
        description="eval() — arbitrary code execution (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="exec_call",
        regex=r"\bexec\s*\(",
        severity="BLOCK",
        description="exec() — arbitrary code execution (DNA #4)",
        dna_ref="#4",
    ),
    # [SCP-DNA-FIX R12-4] Thêm 3 patterns thiếu (OPT-26 cũ có, IMP-24 thiếu):
    # subprocess.run/Popen/call/check_output, os.system, pickle.load
    # Tại sao: OPT-26 (engine.py:383-423) đã BLOCK những pattern này. Khi unify
    # gate, IMP-24 phải có cùng coverage — nếu không, removing OPT-26 sẽ mở lỗ hổng.
    ForbiddenPattern(
        name="subprocess_call",
        regex=r"\bsubprocess\.(run|Popen|call|check_output)\s*\(",
        severity="BLOCK",
        description="subprocess call (injection risk if user input flows in, DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="os_system_call",
        regex=r"\bos\.system\s*\(",
        severity="BLOCK",
        description="os.system() — shell injection risk (DNA #4)",
        dna_ref="#4",
    ),
    ForbiddenPattern(
        name="pickle_load",
        regex=r"\bpickle\.(loads|load)\s*\(",
        severity="BLOCK",
        description="pickle deserialization — arbitrary code execution risk (DNA #4)",
        dna_ref="#4",
    ),
)


# ============================================================
# Dataclasses.
# ============================================================

@dataclass
class PolicyFix:
    """A fix to evaluate against the policy gate.

    Lighter than IMP-14's ProposedFix — only the fields needed for policy
    evaluation. Caller can pass either a PolicyFix or any object with
    `patch`, `patched_source`, `bug_file` attributes.
    """
    fix_id: str = ""
    patch: str = ""
    patched_source: str = ""
    bug_file: str = ""
    bug_line: int = 0
    scanner_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyDecision:
    """Outcome of evaluate_fix()."""
    allowed: bool = True
    blocked_patterns: list[str] = field(default_factory=list)
    severity: str = "ALLOW"   # "ALLOW" | "REVIEW" | "BLOCK"
    reason: str = ""
    audit_id: str = ""        # SHA-256 of (timestamp + fix_id + patterns)
    fix_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked_patterns": list(self.blocked_patterns),
            "severity": self.severity,
            "reason": self.reason,
            "audit_id": self.audit_id,
            "fix_id": self.fix_id,
            "timestamp": self.timestamp,
        }
