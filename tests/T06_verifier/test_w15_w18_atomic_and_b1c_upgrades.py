"""Automated verification suite for Waves W15-W18 (SAFE Atomic Deterministic Verifier)
and Audit Campaign B1c Remediation (BC-1 Symmetric Guard, Pre-Auth /ask Rate Limit,
18/18 Capability Bindings, Immutable Runtime Guard, Air-Gapped E2E Execution).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
import yaml

from scp.epistemic.evidence_store import EvidenceStore
from scp.runtime.atomic_verifier import AtomicClaimSplitter, AtomicDeterministicVerifier
from scp.runtime.domain_data_fork import resolve_domain_data_lookup
from scp.runtime.judge_abstain import is_honest_abstain_answer, is_refusal_abstain_answer
from scp.self_model.capability_map import CapabilityMap


class TestUpgradedSCPSystem(unittest.TestCase):
    def test_01_all_18_capabilities_bound_and_upgradeable_to_m5(self) -> None:
        """Verify 18/18 Complete-SCP capabilities reach M2 out-of-the-box and M5 with D-level proof."""
        with tempfile.TemporaryDirectory() as tmp:
            ev_store = EvidenceStore(Path(tmp) / "ev.db", Path(tmp) / "objects")
            cm = CapabilityMap(
                governance_db_path=Path(tmp) / "gov.db",
                evidence_store=ev_store,
                reference_path="spec/complete_scp_reference.yaml",
                bindings_path="spec/implementation_bindings.yaml",
            )
            ref = yaml.safe_load(Path("spec/complete_scp_reference.yaml").read_text(encoding="utf-8"))
            caps = list((ref.get("capabilities") or {}).keys())
            self.assertEqual(len(caps), 18)

            for cap_id in caps:
                res = cm.recompute_capability(cap_id, "HEAD")
                self.assertEqual(
                    res["status"],
                    "STATIC_PRESENT",
                    f"Capability {cap_id} failed to reach STATIC_PRESENT: {res}",
                )
                self.assertEqual(res["maturity"], "M2")
                self.assertEqual(res["missing_bindings"], [])

            # Record a Level-D (Recovery Verified) proof on execution.task_kernel and verify M5 promotion
            ev_rec = ev_store.observe(
                kind="TEST_RESULT",
                content=b'{"recovery_proof": "verified"}',
                collector_id="test_suite",
                collector_version="1.0.0",
                metadata={"tested_sha": "SHA_GOV_GRADE", "evidence_level": "D"},
            )
            cm.record_proof(
                capability_id="execution.task_kernel",
                evidence_id=ev_rec["evidence_id"],
                evidence_level="D",
                tested_sha="SHA_GOV_GRADE",
            )
            promoted = cm.recompute_capability("execution.task_kernel", "SHA_GOV_GRADE")
            self.assertEqual(promoted["maturity"], "M5")
            self.assertEqual(promoted["status"], "RECOVERY_VERIFIED")
            cm.close()
            ev_store.db.close()

    def test_02_bc1_symmetric_refusal_detector_closes_guard_gap(self) -> None:
        """Verify BC-1 detector blocks factual assertions both BEFORE and AFTER refusal markers."""
        # Genuine refusals -> True
        self.assertTrue(is_refusal_abstain_answer("Xin lỗi, hiện tại tôi không thể xác minh thông tin này."))
        self.assertTrue(is_refusal_abstain_answer("Tôi là SCP, hiện tại tôi chưa có dữ liệu thời tiết thực."))
        self.assertTrue(is_refusal_abstain_answer("Sorry, I cannot verify this information right now."))

        # Assertions AFTER refusal marker -> False
        self.assertFalse(is_refusal_abstain_answer("Tôi không thể xác minh — Donald Trump là tổng thống Mỹ."))
        self.assertFalse(is_refusal_abstain_answer("I cannot verify, but Paris is the capital of France."))

        # Assertions BEFORE refusal marker (previously BC-1 asymmetry gap!) -> False
        self.assertFalse(is_refusal_abstain_answer("Paris là thủ đô của Pháp — tuy nhiên tôi không thể xác minh."))
        self.assertFalse(is_refusal_abstain_answer("Birds fly south in winter, though I cannot verify this."))
        self.assertFalse(is_refusal_abstain_answer("Water means life — I cannot verify."))

    def test_03_atomic_claim_splitter_and_coreference(self) -> None:
        """Verify W15 AtomicClaimSplitter splits compound sentences and resolves anaphora."""
        splitter = AtomicClaimSplitter()
        clauses = splitter.split(
            "Hà Nội là thủ đô của Việt Nam, và thành phố này có lịch sử lâu đời.",
            question="Giới thiệu về Hà Nội",
        )
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0][1], "Hà Nội là thủ đô của Việt Nam")
        self.assertIn("Hà Nội có lịch sử lâu đời", clauses[1][1])

    def test_04_local_deterministic_verifier_math_conversion_and_repair(self) -> None:
        """Verify W16-W18 local verification, deterministic repair, and selective pruning."""
        verifier = AtomicDeterministicVerifier()

        # Case A: 100% locally verified multi-claim answer (math + conversion + chemistry)
        res_a = verifier.verify_and_prune(
            question="Tính 25 + 75, đổi 5 kg sang g và cho biết phân tử khối của H2O",
            draft_answer="25 + 75 = 100. 5 kg = 5000 g. Phân tử khối của H2O là 18.015 g/mol.",
        )
        self.assertTrue(res_a["all_verified_locally"], f"Expected all_verified_locally: {res_a}")
        self.assertEqual(res_a["verified_count"], 3)
        self.assertEqual(res_a["refuted_count"], 0)

        # Case B: Deterministic repair of miscalculated arithmetic ("2 + 2 = 5" -> "2 + 2 = 4.")
        res_b = verifier.verify_and_prune(
            question="2 + 2 bằng bao nhiêu?",
            draft_answer="2 + 2 = 5",
        )
        self.assertTrue(res_b["all_verified_locally"])
        self.assertEqual(res_b["repaired_answer"], "2 + 2 = 4.")
        self.assertEqual(res_b["pruned_answer"], "2 + 2 = 4.")

        # Case C: Selective pruning of unverified conjecture alongside verified deterministic fact
        res_c = verifier.verify_and_prune(
            question="100 celsius to fahrenheit và dự đoán giá vàng ngày mai",
            draft_answer="100 celsius = 212 fahrenheit. Giá vàng ngày mai chắc chắn tăng gấp ba lần.",
        )
        self.assertTrue(res_c["partially_verified_locally"], f"Expected partial verification: {res_c}")
        self.assertEqual(res_c["verified_count"], 1)
        self.assertEqual(res_c["unverified_count"], 1)
        self.assertEqual(res_c["pruned_answer"], "100 celsius = 212 fahrenheit.")

    def test_05_immutable_runtime_guard_blocks_autofix_mutation(self) -> None:
        """Verify SCP_IMMUTABLE_RUNTIME=1 blocks in-place source file mutation in AutoFix."""
        from scp.autofix.classifier import BugReport, BugTier
        from scp.autofix.engine_parts.autofix_mixin import AutoFixMixin
        from scp.autofix.engine_parts.context import FixContext

        mixin = AutoFixMixin()
        bug = BugReport(
            file="scp/hello_bug.py",
            line=10,
            bug_type="bare_except",
            description="test",
            suggested_fix="except Exception:",
            tier=BugTier.TIER_1_AUTO_FIX,
        )
        ctx = FixContext(bug=bug, filepath=Path("scp/hello_bug.py"), report=False, attack_mode=False)
        old_env = os.environ.get("SCP_IMMUTABLE_RUNTIME")
        try:
            os.environ["SCP_IMMUTABLE_RUNTIME"] = "1"
            gate_res = mixin._auto_fix_gates(ctx)
            self.assertIsNotNone(gate_res)
            self.assertEqual(gate_res["status"], "blocked")
            self.assertEqual(gate_res["reason"], "immutable_runtime_enforced")
        finally:
            if old_env is None:
                os.environ.pop("SCP_IMMUTABLE_RUNTIME", None)
            else:
                os.environ["SCP_IMMUTABLE_RUNTIME"] = old_env

    def test_06_preauth_ratelimit_tier_for_ask_and_auth_token(self) -> None:
        """Verify B1c Gap-2 fix assigns /ask and /auth/token to dedicated pre-auth tiers."""
        from scp.api.rate_limiting import determine_rate_limit_tier

        class _DummyURL:
            def __init__(self, path: str) -> None:
                self.path = path

        class _DummyReq:
            def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
                self.url = _DummyURL(path)
                self.method = "POST"
                self.headers = headers or {}

        _, tier_ask_unauth = determine_rate_limit_tier(_DummyReq("/ask"))
        _, tier_ask_badjwt = determine_rate_limit_tier(_DummyReq("/ask", {"authorization": "Bearer bad-jwt"}))
        _, tier_token = determine_rate_limit_tier(_DummyReq("/auth/token"))

        self.assertEqual(tier_ask_unauth, "ask")
        self.assertEqual(tier_ask_badjwt, "ask")
        self.assertEqual(tier_token, "auth_token")

    def test_07_e2e_airgapped_verification_stage_short_circuit(self) -> None:
        """Verify E2E GenerationStage + VerificationStage short-circuits LLM judge in air-gapped mode."""
        from scp.api_server_parts.helpers import AskRequest
        from scp.api_server_parts.pipeline.context import AskPipelineContext
        from scp.api_server_parts.pipeline.generation_stage import GenerationStage
        from scp.api_server_parts.pipeline.verification_stage import VerificationStage
        from scp.ask_adapter_parts.response_verifier import verify_ask_response

        async def _run_e2e() -> None:
            req = AskRequest(question="100 celsius to fahrenheit")
            ctx = AskPipelineContext(req=req, request=None, namespace={})
            ctx.is_chatbot_lane = False
            await GenerationStage().execute(ctx)
            self.assertIn("212", ctx.ai_answer)

            await VerificationStage().execute(ctx)
            self.assertEqual(ctx.judge_verdict.verdict, "PASS")
            self.assertEqual(ctx.gov_decision, "UPHOLD")
            self.assertIn("212", ctx.api_final_answer)

            # Verify AskKernelAdapter response_verifier accepts atomic_deterministic_local provenance
            v_res = await verify_ask_response(
                req,
                {
                    "final_answer": ctx.api_final_answer,
                    "verdict": ctx.judge_verdict.verdict,
                    "governance_decision": ctx.gov_decision,
                    "v98_classification": ctx.api_v98_classification,
                },
            )
            self.assertEqual(v_res["verdict"], "VERIFIED", f"Expected VERIFIED: {v_res}")

        asyncio.run(_run_e2e())

    def test_08_claim_verifier_numeric_deviation_and_contradiction_guards(self) -> None:
        """Verify ClaimVerifier integration catches numeric deviation, contradiction, and entity grounding."""
        verifier = AtomicDeterministicVerifier()

        # Case 1: Numeric claim within 2% tolerance matches evidence -> VERIFIED
        res_match = verifier.verify_and_prune(
            question="Dân số của tỉnh X là bao nhiêu?",
            draft_answer="Dân số của tỉnh X là 1250000 người.",
            clean_evidence=["Theo số liệu cục thống kê, dân số của tỉnh X là 1250000 người."],
        )
        self.assertEqual(res_match["verified_count"], 1)
        self.assertEqual(res_match["refuted_count"], 0)
        self.assertFalse(res_match["all_verified_locally"])

        # Case 2: Numeric claim with high deviation (>25%) -> CONTRADICTED via ClaimVerifier
        res_contradict = verifier.verify_and_prune(
            question="Dân số của tỉnh X là bao nhiêu?",
            draft_answer="Dân số của tỉnh X là 5000000 người.",
            clean_evidence=["Theo số liệu cục thống kê, dân số của tỉnh X là 1000000 người."],
        )
        self.assertEqual(res_contradict["refuted_count"], 1)
        claim_bad = res_contradict["claims_payload"][0]
        self.assertEqual(claim_bad["status"], "CONTRADICTED")
        self.assertIn("claim_verifier", claim_bad["source"])
        self.assertIn("delta=", claim_bad["verification_detail"])
        self.assertIn("contradicts", claim_bad["verification_detail"])

    def test_09_w19_extended_local_domains_resolution(self) -> None:
        """Verify Agent-2 W19 extended local domain resolution (Tech, Cyber, Logic, Legal, History, Medical, Edu)."""
        tech_res = resolve_domain_data_lookup("Ngôn ngữ lập trình Python ra đời năm nào và ai tạo ra?")
        assert tech_res is not None and "1991" in tech_res["text"] and "Guido van Rossum" in tech_res["text"]

        cyber_res = resolve_domain_data_lookup("Tấn công phishing là gì trong bảo mật?")
        assert cyber_res is not None and "Lừa đảo" in cyber_res["text"]

        logic_res = resolve_domain_data_lookup("Ngụy biện ad hominem là gì?")
        assert logic_res is not None and "Tấn công" in logic_res["text"]

        legal_res = resolve_domain_data_lookup("Hiến pháp 2013 có bao nhiêu điều?")
        assert legal_res is not None and "120" in legal_res["text"]

        hist_res = resolve_domain_data_lookup("Sự kiện lịch sử năm 938 là gì?")
        assert hist_res is not None and "Ngô Quyền" in hist_res["text"]

        med_res = resolve_domain_data_lookup("Cơ thể người có số xương là bao nhiêu?")
        assert med_res is not None and "206" in med_res["text"]

        edu_res = resolve_domain_data_lookup("Phương pháp spaced repetition là gì?")
        assert edu_res is not None and "lặp lại" in edu_res["text"].lower()

    def test_10_w19_zero_unproven_target_test_coverage(self) -> None:
        """Verify Agent-3 W19 bound all 205/205 capabilities and edges with 0 UNPROVEN and 0 validation errors."""
        from tools.verify_scp_target_test_coverage import load_and_validate

        _binding, _target, report, errors = load_and_validate()
        assert errors == [], f"Unexpected coverage validation errors: {errors}"
        summary = report["summary"]
        assert summary["capabilities_total"] == 138
        assert summary["cause_effect_edges_total"] == 67
        assert summary["claims_total"] == 205
        assert summary["status_counts"].get("UNPROVEN", 0) == 0

    def test_11_w19_b1c_config_and_manifest_closures(self) -> None:
        """Verify Agent-1 W19 closed dashboard sharp CVE pin, .gitignore B1c Gap-4, and protected_invariants."""
        pkg = json.loads(Path("dashboard/package.json").read_text(encoding="utf-8"))
        sharp_ver = pkg.get("dependencies", {}).get("sharp", "")
        assert sharp_ver == "^0.35.5", f"Expected sharp ^0.35.5, got {sharp_ver}"

        gitignore_txt = Path(".gitignore").read_text(encoding="utf-8")
        assert "!reports/scp_acceptance_ci/*/*.json" in gitignore_txt

        inv = yaml.safe_load(Path("spec/protected_invariants.yaml").read_text(encoding="utf-8"))
        egress_row = [r for r in inv["protected"] if r["id"] == "egress.boundary"][0]
        assert "scp/policy/egress.py" in egress_row["path_patterns"]
        assert "scp/security/url_safety.py" in egress_row["path_patterns"]


if __name__ == "__main__":
    unittest.main(verbosity=2)


