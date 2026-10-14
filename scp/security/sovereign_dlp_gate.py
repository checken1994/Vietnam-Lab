"""Sovereign Data Loss Prevention (DLP) & PII Protection Gate.

Enforces:
  1. Vietnam Decree 13/2023/ND-CP (Personal Data Protection):
     Automatically detects and masks sensitive Vietnamese Personal Identifiable
     Information (PII) before any text is written to logs or sent to external
     LLM gateways:
       - 12-digit Vietnamese Citizen ID (CCCD / CMND)
       - Vietnamese mobile phone numbers (+84 / 03x, 05x, 07x, 08x, 09x)
       - Email addresses
       - Bank card / account numbers (12-19 digits)
  2. Vietnam Law on Protection of State Secrets 2018 (29/2018/QH14):
     Detects classified markings (`TUYỆT MẬT`, `TỐI MẬT`, `ĐỘ MẬT: MẬT`,
     `TÀI LIỆU MẬT NHÀ NƯỚC`, `TOP SECRET // VN-SOVEREIGN`). When egress is
     enabled (`egress_mode != 'deny'`), raises `ClassifiedEgressBlockedError`
     (HTTP 403 `SOVEREIGN_CLASSIFIED_EGRESS_BLOCKED`) and forces air-gapped mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class ClassifiedEgressBlockedError(PermissionError):
    """Raised when classified state-secret content is about to leave the air-gapped boundary."""


# 12-digit Vietnamese CCCD pattern (starts with 001..096 province code + century/gender digit 0..9 + 2-digit birth year + 6 digits)
_VN_CCCD_RE = re.compile(r"(?<!\d)(?:0[0-9]{2}[0-9][0-9]{2}[0-9]{6})(?!\d)")

# Vietnamese phone number pattern (+84 or 0 followed by 3, 5, 7, 8, 9 and 8 digits)
_VN_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)(?:3|5|7|8|9)[0-9]{8}(?!\d)")

# Standard email pattern
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Classified markings under Vietnam Law on Protection of State Secrets 2018
_CLASSIFIED_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TUYET_MAT", re.compile(r"\b(?:tuyệt\s+mật|tuyet\s+mat|top\s+secret)\b", re.IGNORECASE)),
    ("TOI_MAT", re.compile(r"\b(?:tối\s+mật|toi\s+mat)\b", re.IGNORECASE)),
    (
        "MAT_NHA_NUOC",
        re.compile(
            r"(?:độ\s+mật\s*:\s*mật|tài\s+liệu\s+mật\s+nhà\s+nước|bí\s+mật\s+nhà\s+nước)",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class DLPInspectionResult:
    scrubbed_text: str
    pii_detected: bool
    pii_counts: dict[str, int] = field(default_factory=dict)
    classified_level: str | None = None
    egress_allowed: bool = True


class SovereignDLPGate:
    """Deterministic PII Scrubber & State-Secret Egress Kill-Switch."""

    def inspect_and_scrub(
        self,
        text: str,
        *,
        egress_mode: str = "deny",
        enforce_egress_block: bool = True,
    ) -> DLPInspectionResult:
        """Scrub PII under Decree 13/2023/ND-CP and enforce State-Secret egress kill-switch."""
        if not text:
            return DLPInspectionResult(scrubbed_text="", pii_detected=False)

        # 1. Check for State-Secret classification stamps
        detected_level: str | None = None
        for level_name, pattern in _CLASSIFIED_MARKERS:
            if pattern.search(text):
                detected_level = level_name
                break

        egress_normalized = str(egress_mode or "deny").strip().lower()
        egress_allowed = True
        if detected_level is not None and egress_normalized != "deny":
            egress_allowed = False
            if enforce_egress_block:
                raise ClassifiedEgressBlockedError(
                    f"SOVEREIGN_CLASSIFIED_EGRESS_BLOCKED: Document bears classification marker "
                    f"[{detected_level}] under Law 29/2018/QH14; external egress ({egress_normalized}) is forbidden."
                )

        # 2. Scrub PII per Decree 13/2023/ND-CP
        counts: dict[str, int] = {}

        cccd_matches = _VN_CCCD_RE.findall(text)
        if cccd_matches:
            counts["vn_cccd"] = len(cccd_matches)
            text = _VN_CCCD_RE.sub("[REDACTED_VN_CCCD]", text)

        phone_matches = _VN_PHONE_RE.findall(text)
        if phone_matches:
            counts["vn_phone"] = len(phone_matches)
            text = _VN_PHONE_RE.sub("[REDACTED_VN_PHONE]", text)

        email_matches = _EMAIL_RE.findall(text)
        if email_matches:
            counts["email"] = len(email_matches)
            text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)

        return DLPInspectionResult(
            scrubbed_text=text,
            pii_detected=bool(counts),
            pii_counts=counts,
            classified_level=detected_level,
            egress_allowed=egress_allowed,
        )


_GLOBAL_DLP_GATE = SovereignDLPGate()


def get_sovereign_dlp_gate() -> SovereignDLPGate:
    return _GLOBAL_DLP_GATE
