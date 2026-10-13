# SCP — Compliance Mapping (NIST AI RMF · OWASP Agentic 2026 · OpenAI Agentic Practices · NIST SP 800-53 · ISO/IEC 27001)

> Áp dụng cho: SCP Agent OS 4.0.2 · HEAD `64704ed5` (branch `main`)  
> Mỗi control: SCP subsystem → control family ngành → bằng chứng (file:test/scan seal).  
> Nguyên tắc: chỉ ghi những gì CÓ bằng chứng trong repo; gap ghi tường minh.

---

## C.1 NIST AI RMF 1.0 + SP 800-53

| SCP subsystem | NIST / SP 800-53 control | Bằng chứng (file:test/scan) | Trạng thái |
|---|---|---|---|
| `SCP_EGRESS_MODE` + `enforce_egress_policy` (choke point `url_safety.py`) | SP 800-53 **AC-3 / AC-4** (Access Enforcement / Information Flow Control) | `scp/security/url_safety.py` (`enforce_egress_policy`, `EgressDeniedError`); container proof: deny chặn example.com (M13-evidence `EE-egress-container-reversal.txt`); static gate `tests/T03_capability/test_egress_enforcement.py::test_g_*` | ✅ ENFORCED |
| TaskKernel state machine + OCC + lease fencing | **AC-2 / AC-25** (account/reference monitor — luôn qua PEP, không bypass) | `scp/task_kernel_parts/taskkernel.py`; parity 15/15 SQLite↔PG (`tests/T04_kernel/test_pg_storage_parity.py`); FA-05 test: 0 kernel row khi deny (`test_mcp_server.py::test_hands_execute_without_capability_token_denied_by_bridge_pep`) | ✅ ENFORCED |
| Kill switch + rollback + recovery | **IR-4 / CP-10** (Incident Response / System Recovery) | `scp/pc_control/pc_controller.py` kill switch; chaos: kill connection giữa transaction — instance mới recovery sạch (`test_pg_storage_chaos.py::test_a*`); W2 R3: kill -9 → breaker mở + recovery 21s; `docs/governance/INCIDENT_RESPONSE.md` | ✅ ENFORCED |
| Evidence gates T00 + FA-03 (guardrails) | **CA-7 / AU-6** (Continuous Monitoring / Audit Record Review) | `.github/workflows/` L3 T00 tripwire + guardrails; `tools/t00_meta_audit.py`; CI L3 SUCCESS trên PR | ✅ ENFORCED |
| Self-audit / self-repair (autofix + verified_fix) | **CA-2 / CA-5** (Control Assessment / Remediation) | `scp/autofix/engine.py` + deterministic_worker + sandbox evaluator (`scp/sandbox_evaluator/evaluator.py`, E2E `test_sandbox_evaluator_e2e.py`); rollback fail-closed; `scp/autofix/evidence_replay.py` là mã nguồn thật (**759 LOC**, FA-04 verified, `tests/T07_learning/test_evidence_replay_hardening.py`) | ✅ ENFORCED |
| Postgres durability + event bus | **CP-9** (System Backup) / AU (audit trail) | `scp/kernel_storage_pg.py` + `scp/event_bus_pg.py`; parity 15/15; chaos backup round-trip pg_dump (`test_pg_storage_chaos.py::test_d*`) | ✅ ENFORCED |
| Retention / drift guard | **CM / SI** (Configuration Management) | `scp/governance/` drift_guard semantic AST (`drift_guard.py:153-192`); `docs/governance/DATA_RETENTION.md` | ✅ ENFORCED |

---

## C.2 OWASP Top 10 Agentic Applications (2025/2026)

| Rủi ro OWASP | SCP control | Bằng chứng / Gap | Trạng thái |
|---|---|---|---|
| **LLM01 Prompt Injection** | Red-team suite + jailbreak block; hidden-instruction probing | `scp/security/red_team.py`; benchmark attacks: dan/prompt_injection/encoded/role_play — **attack resistance 100%** (4/4 BLOCKED, seed 42, `bench_w2_seed42.json`) | ✅ đo được |
| **LLM02 Sensitive Disclosure** | PII/secret redaction + traversal guards | sweep S1–S6: 0 raw fetch; `bypass_encrypt.py` fail-closed (A1); gitignore secrets; full-tree secret scan `tools/scan_secrets.py --all` (0 secret) | ✅ ENFORCED |
| **LLM03 Supply Chain** | Lockfile + pin + advisory | `scp/requirements.lock.txt` (**67 package pins**, diff container = rỗng); `tools/audit_dependencies.py` (DEP-01..06, 1.100 SHA256 hashes); SLSA Build L3 provenance & CycloneDX SBOM attestation (`SUP-01`); Trivy scan fail-closed (`exit-code: 1`, `SEC-19`) | ✅ ENFORCED |
| **LLM04 Data/Model Poisoning** | Knowledge quarantine + contradiction authority | `scp/knowledge/` quarantine; `antibody_parts`; RAG validation | ✅ ENFORCED |
| **LLM05 Improper Output Handling** | Sandbox Evaluator + fail-closed verdicts | `scp/sandbox_evaluator/` (E2E 18P); stdout/stderr raw trả về, gate chốt; strict isolation không bwrap fail-closed (`GAP-03`) | ✅ ENFORCED |
| **LLM06 Excessive Agency** | Capability PEP + tier3 approval + token scopes + FA-05 ordering | `test_flow_04` (58P, kernel mutation 0 khi deny); MCP `test_mcp_server.py`; Ma trận phân quyền 6 cấp `docs/governance/ACCESS_CONTROL.md` | ✅ ENFORCED |
| **LLM07 System Prompt Leakage** | Withheld answers khi verify FAIL | `ask_kernel_adapter.py` withheld (`bench_w2_seed42.json`: 13/13 FAIL = withheld) | ✅ ENFORCED |
| **LLM08 Vector/Embedding Weakness** | Evidence-grounded retrieval (RAG) | evidence recall đo được (`bench_w2_seed42.json` D) — pipeline grounded with source verification | ✅ ENFORCED |
| **LLM09 Misinformation** | Hallucination guard + abstain | W2 R1: hallucination **0**/30; FactChecker streaming + abstain threshold (`StreamingFactChecker`) | ✅ ENFORCED |
| **LLM10 Unbounded Consumption** | Backpressure (200 in-flight cap), rate limit 60/min, lockfile | `ask_kernel_adapter.py` backpressure; slowapi; payload ceilings (2MB / 5MB / 10MB M-09); compose resource limits (2 CPU / 1GB RAM / 100 PIDs M-04) | ✅ ENFORCED |

---

## C.3 OpenAI "Practices for Governing Agentic AI" — nguyên tắc → SCP

| Nguyên tắc | SCP implementation | Bằng chứng |
|---|---|---|
| Traceability & audit trail | Journal HMAC hash-chain + Journal Anchor + trace_id | `taskkernel.py` + `lease_manager.py` (GAP-01/02: HMAC-SHA256, Journal Anchor catching table deletion); `evidence_replay.py` (759 LOC) |
| Human oversight / approval gates | Tier-3 approval + escalation UNKNOWN→ESCALATE | `judge.py` governance; Human Confirmation Store (`confirmation_id`); L5 Privileged Access Control |
| Bounded autonomy | Capability levels (L0-L5) + egress mode + backpressure | capability_epoch + PEP; dead-proxy `127.0.0.1:0`; safe_process whitelist |
| Incident response | Kill switch 3 lớp + breaker + recovery | `docs/governance/INCIDENT_RESPONSE.md`; chaos recovery (recovery 21s sau kill -9); Level D recovery proof |

---

## C.4 Cách đọc
- Bảng này là **map** — không phải chứng nhận. Mỗi dòng "✅" = có test/scan evidence trong repo tại SHA nêu trên.
- Số liệu benchmark (`bench_w2_seed42.json` → `bench_after_fix_seed42.json`) accuracy 0% **không còn** do compile-time cost wall — đã bỏ, đổi thành opt-in `SCP_LLM_COST_MODE=free_only` (commit `c45d973`, V18 verify 10/10). Root cause còn lại: container chạy `SCP_EGRESS_MODE=deny` → egress authority (always-on, đúng thiết kế) chặn openrouter.ai → provider không gọi được mạng → `/ask` withhold → accuracy 0. **Security attack resistance vẫn 100%** (PEP/egress/kernel nguyên vẹn).
- Muốn accuracy thật: deployment owner tự `SCP_EGRESS_MODE=allowlist` + allowlist host provider (quyết định security-posture — KHÔNG tự đổi). Đây không còn là "mandate mã hóa cứng" mà là knob cấu hình.

---

## C.5 Bảng Theo Dõi Kế Hoạch Hành Động & Các Cột Mốc (POA&M Tracking Table)

Bảng theo dõi Kế hoạch Hành động và Các Cột mốc (Plan of Action and Milestones - POA&M) theo chuẩn NIST SP 800-53 CA-5 và ISO/IEC 27001:2022 A.5.27:

| POA&M ID | Chuẩn Quy Chiếu | Phân Hệ Liên Quan | Mô Tả Rủi Ro & Biện Pháp Khắc Phục | Trạng Thái | Cột Mốc Hoàn Tất | Bằng Chứng / NodeID Kiểm Chứng |
|---|---|---|---|:---:|---|---|
| **POAM-AC-01** | NIST SP 800-53 AC-3 / ISO 27001 A.8.2 | Task Kernel Journal | Rủi ro giả mạo nhật ký sự kiện khi attacker xóa hoặc sửa DB; Triển khai HMAC-SHA256 có khóa bí mật và Journal Anchor trong `control` table. | **CLOSED** | GA B56 (2026-10-13) | `tests/T03_capability/test_redteam_b56_remediation.py::test_gap01_journal_anchor_detects_events_deletion` |
| **POAM-AC-02** | NIST SP 800-53 AC-6 / ISO 27001 A.8.2 | PC Controller | Lệnh thực thi bị lỗi FileNotFoundError powershell.exe trên môi trường Linux/Docker; Triển khai phân nhánh nền tảng `/bin/sh -c` và `pwsh`. | **CLOSED** | GA B56 (2026-10-13) | `tests/T03_capability/test_redteam_b56_remediation.py::test_pc01_command_dispatcher_platform_branching` |
| **POAM-SC-01** | NIST SP 800-53 SC-7 / ISO 27001 A.8.20 | Sandbox Evaluator | Rủi ro tin tưởng sai container (`/.dockerenv`) bỏ qua cách ly; Cưỡng chế bắt buộc có `bwrap` hoặc capability token trong strict mode. | **CLOSED** | GA B56 (2026-10-13) | `tests/T03_capability/test_redteam_b56_remediation.py::test_gap03_evaluator_strict_isolation_refuses_even_in_container` |
| **POAM-SC-02** | NIST SP 800-53 SC-8 / ISO 27001 A.8.24 | Production Guard & API | Khởi chạy chế độ sản xuất với bind non-loopback thiếu TLS; Cưỡng chế fail-closed bắt buộc `SCP_FORCE_HTTPS=1`. | **CLOSED** | GA B56 (2026-10-13) | `tests/T03_capability/test_redteam_b56_remediation.py::test_e02_production_guard_tls_enforcement_non_loopback` |
| **POAM-RA-01** | NIST SP 800-53 RA-5 / ISO 27001 A.8.8 | CI Container Publish | Trivy scan container image chạy với cờ exit-code 0; Nâng cấp `exit-code: 1` fail-closed và thiết lập `.trivyignore` có căn cứ kỹ thuật. | **CLOSED** | GA B56 (2026-10-13) | `.github/workflows/container-publish.yml` + `.trivyignore` |
| **POAM-SR-01** | NIST SP 800-53 SR-3 / SLSA L3 | Supply Chain & Attestation | Thiếu chứng thực nguồn gốc SLSA Build L3 và ký SBOM; Tích hợp `actions/attest-build-provenance` và `cosign attest` CycloneDX. | **CLOSED** | GA B57 (2026-10-13) | `.github/workflows/container-publish.yml` (SUP-01) |
| **POAM-IR-01** | NIST SP 800-53 IR-4 / ISO 27001 A.5.24 | Governance & Incident Response | Thiếu tài liệu quy trình ứng phó sự cố theo NIST SP 800-61 Rev2; Ban hành quy trình 5 bước tích hợp Kill Switch 3 lớp và Chaos Recovery. | **CLOSED** | GA B57 (2026-10-13) | `docs/governance/INCIDENT_RESPONSE.md` |
| **POAM-MP-01** | NIST SP 800-53 MP-6 / GDPR Art 5 | Governance & Data Retention | Thiếu quy định phân cấp 4 tầng dữ liệu và thời hạn lưu giữ audit logs; Ban hành chính sách tuân thủ GDPR và NĐ 13/2023/NĐ-CP. | **CLOSED** | GA B57 (2026-10-13) | `docs/governance/DATA_RETENTION.md` |
| **POAM-AC-03** | NIST SP 800-53 AC-2 / ISO 27001 A.5.15 | Governance & Access Control | Thiếu đặc tả ma trận phân quyền 6 cấp L0-L5; Ban hành chính sách kiểm soát truy cập và quản lý thu hồi token JTI. | **CLOSED** | GA B57 (2026-10-13) | `docs/governance/ACCESS_CONTROL.md` |
| **POAM-SA-01** | NIST SP 800-53 SA-11 / ISO 27001 A.5.3 | Governance & CODEOWNERS | Thiếu cơ chế Separation of Duties cho các đường dẫn an ninh nhạy cảm; Cập nhật Reviewer độc lập cho `/scp/security/`, `/scp/capabilities/`. | **CLOSED** | GA B57 (2026-10-13) | `.github/CODEOWNERS` (GOV-10) |
| **POAM-PL-01** | NIST SP 800-53 PL-4 / ISO 27001 A.5.1 | Technical Integrity | Ban hành bộ quy tắc ứng xử kỹ thuật, an toàn chuỗi cung ứng và liêm chính kiểm thử chống Placebo Assertions. | **CLOSED** | GA B57 (2026-10-13) | `docs/governance/CODE_OF_CONDUCT.md` (GOV-11) |
