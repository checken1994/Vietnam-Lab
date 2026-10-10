# Security Policy — SCP Agent OS

## 1. Supported Versions

We take the security of the SCP Agent OS platform seriously. Security patches and remediations are actively maintained on the current primary release track:

| Version / Branch | Supported | Notes |
|:---|:---:|---|
| `main` (SCP 14.x) | :white_check_mark: | Primary development and production baseline |
| < 14.0.0 | :x: | Legacy releases; users must upgrade to current `main` |

---

## 2. Reporting a Vulnerability & Security Contact

If you discover a security vulnerability, security gap, or potential bypass in SCP:
- **Please DO NOT open a public issue or discuss the vulnerability in public forums.**
- Send a detailed disclosure report directly to the security maintainers:
  - **Email:** `0974061824@gmail.com` (or submit a Private Vulnerability Advisory via GitHub Security Advisories on repository `checken1994/Vietnam-Lab`).
  - **GPG/PGP Encryption:** Available upon request for sensitive reports.

### What to Include in Your Report
To accelerate triage and remediation, please provide:
1. **Description & Impact:** Clear description of the vulnerability, affected components, and potential blast radius.
2. **Exploit Mandate Proof (FA-09 Compliant):** Concrete reproduction script or step-by-step instructions demonstrating actual failure/bypass in a sandbox or local environment.
3. **Environment Details:** Commit SHA, OS version, and relevant configurations.
4. **Remediation Proposal (Optional):** Suggested patch or architectural guard adhering to Fail-Closed principles.

---

## 3. Response Service Level Agreement (SLA)

We commit to the following response timeline:

| Phase | Target SLA | Description |
|---|:---:|---|
| **Initial Acknowledgment** | ≤ 24–48 hours | Confirmation of report receipt and initial classification. |
| **Triage & Validation** | ≤ 5 business days | Technical reproduction and severity assignment. |
| **Critical Fix & Patch** | ≤ 7 calendar days | Emergency remediation for Remote Code Execution (RCE), sandbox breakout, or secret exfiltration. |
| **High Severity Fix** | ≤ 14 calendar days | Remediation for privilege escalation, policy bypass, or authentication flaw. |
| **Medium / Low Fix** | ≤ 30 calendar days | Scheduled remediation through standard release cycle. |
| **Public Advisory** | Coordinated | Release notes and CVE publication after patch verification. |

---

## 4. Responsible Research & Safe Harbor

We fully support responsible security research. We will not take legal action against researchers who:
- Conduct research in good faith and avoid privacy violations, data destruction, or service disruption.
- Test against local instances (`localhost`, loopback) rather than production-facing multi-tenant environments.
- Provide reasonable time for maintainers to patch the vulnerability before public disclosure.
- Respect Fail-Closed security boundaries (SCP DNA #1–#29).

Thank you for helping keep SCP secure and resilient.
