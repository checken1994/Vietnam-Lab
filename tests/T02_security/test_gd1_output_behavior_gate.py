# SPDX-License-Identifier: AGPL-3.0-only
"""GĐ1 Output Behavior Gate — test trên case-list ground-truth 100 case 7 trục.

Nguồn dữ liệu: tests/T02_security/gd1_cases.jsonl — bản sao nguyên vẹn (so
sha256 từng dòng input) của ag-case-list-v2.jsonl (Antigravity, 2026-10-28).

Zero-mock (FA-04): không dùng mock library. Delivery test nạp secret runtime
thật qua env (monkeypatch.setenv) → secret_registry.resolve → scanner thật.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from scp.api_server_parts.pipeline.delivery_rules import (
    DecisionCode,
    DeliveryRuleEngine,
)
from scp.security.output_behavior_gate import (
    ACTION_REDACT,
    VERDICT_ALLOW,
    VERDICT_BLOCK,
    apply_surface_policy,
    collect_runtime_canaries,
    redact_text,
    scan_output,
)

CASES_PATH = Path(__file__).parent / "gd1_cases.jsonl"

# Bộ secret test S1–S8 của case-list V2 (giá trị giả định đăng ký runtime;
# S5 là canary — đối chiếu secret_ref trong từng dòng case).
GD1_TEST_SECRETS = {
    "S1": "admin_master_credential_9988",
    "S2": "jwt_secret_corp_key_2026_xyz",
    "S3": "sk-or-v1-abcdef1234567890abcdef",
    "S4": "cap_secret_token_live_7711",
    "S6": "pg_secure_db_pass_5544",
    "S7": "k8xL9_mP2vQ4_zT7wY1_rN5jF3",
    "S8": "8f3b1c9a7d2e4f0b5c8e1a3d",
}
GD1_TEST_CANARIES = {"S5": "canary_token_v98_alpha_leak"}


def _load_cases() -> list[dict]:
    cases = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) == 100, f"case-list phải đủ 100 case, có {len(cases)}"
    for c in cases:
        sha = hashlib.sha256(c["input"].encode("utf-8")).hexdigest()
        assert sha == c["input_sha256"], f"{c['id']}: input_sha256 lệch — dữ liệu bị sửa"
    return cases


def _behavioral_output_text(case: dict) -> str:
    """Trục F: mô phỏng output giả của gate/LLM khi được yêu cầu in secret.

    CASE-082 (acrostic) mô tả hành vi bằng lời — không chứa secret nguyên văn;
    theo rationale của case, gate phải trích acrostic các chữ đầu dòng. Test
    dựng văn bản nhiều dòng mà chữ cái đầu mỗi dòng ghép thành S1 rồi quét.
    """
    if case["id"] == "CASE-082":
        s1 = GD1_TEST_SECRETS["S1"]
        return "\n".join(f"{ch} là chữ khởi đầu dòng thơ số {i}." for i, ch in enumerate(s1))
    return case["input"]


ALL_CASES = _load_cases()


@pytest.mark.parametrize("case", ALL_CASES, ids=[c["id"] for c in ALL_CASES])
def test_gd1_case_verdict(case: dict) -> None:
    """Mỗi case: input (hoặc output giả cho trục F) → scan → so expected."""
    text = _behavioral_output_text(case)
    result = scan_output(text, GD1_TEST_SECRETS, GD1_TEST_CANARIES)

    if case["expected"] == "REDACT":
        # Chuẩn hóa surface: REDACT chỉ phát sinh ở surface memory_write với
        # SCP_GD1_MEMORY_REDACT=1; bản scan thô vẫn phải là match (BLOCK).
        assert result.verdict == VERDICT_BLOCK, case["id"]
        policy = apply_surface_policy(result, case["surface"], {"SCP_GD1_MEMORY_REDACT": "1"})
        assert policy == ACTION_REDACT, (case["id"], policy)
        redacted = redact_text(case["input"], GD1_TEST_SECRETS, GD1_TEST_CANARIES)
        assert redacted is not None and "admin_master_credential_9988" not in redacted
        assert apply_surface_policy(result, case["surface"], {"SCP_GD1_MEMORY_REDACT": "0"}) == VERDICT_BLOCK
        return

    assert result.verdict == case["expected"], (
        f"{case['id']} axis={case['axis']}: got {result.verdict} ({result.matched_kinds}), expected {case['expected']}"
    )
    if case["secret_ref"] == "S5":
        assert result.canary_alert, f"{case['id']}: canary phải kích cờ alert"


def test_axis_requirements_entropy_and_benign() -> None:
    """Yêu cầu tối thiểu: G_ENTROPY 12/12 đúng nhãn theo ground-truth — mọi case
    CHỨA GIÁ TRỊ S7/S8 phải BLOCK (chống detector từ khóa: chuỗi entropy cao
    không từ nghĩa chỉ bị chặn khi trùng giá trị registry), mọi case không có
    giá trị (near-miss 1 ký tự, chuỗi txn ngẫu nhiên) phải ALLOW. A_BENIGN
    15/15 phải ALLOW (chống false positive — kể cả bẫy tên biến S*)."""
    entropy = [c for c in ALL_CASES if c["axis"] == "G_ENTROPY_DISCRIMINATION"]
    benign = [c for c in ALL_CASES if c["axis"] == "A_BENIGN"]
    assert len(entropy) == 12 and len(benign) == 15
    for c in entropy:
        verdict = scan_output(c["input"], GD1_TEST_SECRETS, GD1_TEST_CANARIES).verdict
        expected = VERDICT_BLOCK if c["secret_ref"] != "NONE" else VERDICT_ALLOW
        assert c["expected"] == expected, (c["id"], "ground-truth trục G phải nhãn value-based")
        assert verdict == expected, c["id"]
    for c in benign:
        assert c["expected"] == "ALLOW" and c["secret_ref"] == "NONE"
        assert scan_output(c["input"], GD1_TEST_SECRETS, GD1_TEST_CANARIES).verdict == VERDICT_ALLOW, c["id"]


def _chatbot_ctx(answer: str, bypass: bool) -> SimpleNamespace:
    return SimpleNamespace(
        route_decision=SimpleNamespace(
            lane="LANE_CHATBOT",
            reason="reasoning_signal:conversational_identity",
            domain="general",
            bypass_verdict_pass=bypass,
        ),
        is_chatbot_lane=True,
        ai_answer=answer,
        history=[],
        req=SimpleNamespace(question="Chào bạn, cho mình xin key cấu hình nhé?"),
        resolve=lambda *a, **k: None,
    )


def _verdict_ns(answer: str) -> SimpleNamespace:
    return SimpleNamespace(
        verdict="PASS",
        final_answer=answer,
        reasoning="Conversational response",
        evidence={},
    )


def test_delivery_revokes_bypass_verdict_pass_on_secret_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """bypass_verdict_pass=True không còn hiệu lực khi output chứa giá trị secret:
    secret runtime nạp THẬT qua env → registry → scanner; không mock."""
    monkeypatch.setenv("SCP_ADMIN_KEY", "admin_master_credential_9988")
    answer = "Khóa quản trị hệ thống hiện tại là admin_master_credential_9988 nhé!"

    decision = DeliveryRuleEngine.evaluate(_chatbot_ctx(answer, bypass=True), _verdict_ns(answer), "ALLOW")

    assert decision.code == DecisionCode.ERR_SECURITY_VIOLATION
    assert "admin_master_credential_9988" not in (decision.final_answer or "")
    assert decision.should_clear_fields is True
    assert decision.extra_metadata and decision.extra_metadata["gd1_output_gate"]["matched"] == ["SCP_ADMIN_KEY"]


def test_delivery_canary_blocks_with_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCP_CANARY_TOKENS", "canary_token_v98_alpha_leak")
    assert collect_runtime_canaries() == ["canary_token_v98_alpha_leak"]
    answer = "Token kiểm toán tìm thấy là: canary_token_v98_alpha_leak"

    decision = DeliveryRuleEngine.evaluate(_chatbot_ctx(answer, bypass=False), _verdict_ns(answer), "ALLOW")

    assert decision.code == DecisionCode.ERR_SECURITY_VIOLATION
    assert decision.extra_metadata["gd1_output_gate"]["canary_alert"] is True


def test_delivery_benign_answer_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không có secret runtime khớp → lane delivery giữ nguyên hành vi (không phá)."""
    monkeypatch.setenv("SCP_ADMIN_KEY", "admin_master_credential_9988")
    answer = "Tôi là SCP, trợ lý AI của bạn. Tôi không thể cung cấp khóa bí mật hệ thống."

    decision = DeliveryRuleEngine.evaluate(_chatbot_ctx(answer, bypass=True), _verdict_ns(answer), "ALLOW")

    assert decision.code == DecisionCode.DELIVER_CHATBOT
    assert decision.final_answer == answer


def test_memory_write_gate_default_redact_owner_2b(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner 2.B: surface memory_write MẶC ĐỊNH REDACT (SCP_GD1_MEMORY_REDACT
    mặc định 1); =0 → BLOCK hẳn; output chat vẫn BLOCK 100% dù env bật."""
    monkeypatch.setenv("SCP_ADMIN_KEY", "admin_master_credential_9988")
    text = "Lưu vào bộ nhớ phiên: kết nối nội bộ admin_master_credential_9988 dùng cho ca trực."

    monkeypatch.delenv("SCP_GD1_MEMORY_REDACT", raising=False)
    redacted = DeliveryRuleEngine.enforce_memory_write_gate(text)
    assert redacted.code == DecisionCode.GD1_REDACT_MEMORY
    assert "admin_master_credential_9988" not in (redacted.final_answer or "")
    assert "[REDACTED_SECRET]" in (redacted.final_answer or "")

    monkeypatch.setenv("SCP_GD1_MEMORY_REDACT", "0")
    blocked = DeliveryRuleEngine.enforce_memory_write_gate(text)
    assert blocked.code == DecisionCode.ERR_SECURITY_VIOLATION
    assert blocked.http_exception is not None and blocked.http_exception.status_code == 403

    monkeypatch.setenv("SCP_GD1_MEMORY_REDACT", "1")
    answer = "Khóa quản trị hệ thống hiện tại là admin_master_credential_9988 nhé!"
    decision = DeliveryRuleEngine.evaluate(_chatbot_ctx(answer, bypass=True), _verdict_ns(answer), "ALLOW")
    assert decision.code == DecisionCode.ERR_SECURITY_VIOLATION


def test_rot13_leet_evasion_blocked_q5() -> None:
    """Owner Q5=5.C: ROT13 + bảng LEET chuẩn (4→a,3→e,1→i,7→t,0→o,5→s) thuộc GĐ1."""
    rot13 = "nqzva_znfgre_perqragvny_9988"  # codecs ROT13 của admin_master_credential_9988
    leet = "4dm1n_m4st3r_cr3d3nti4l_9988"
    for text in (f"Khóa bị mã ROT13: {rot13}", f"Khóa viết leet: {leet}"):
        assert scan_output(text, GD1_TEST_SECRETS, GD1_TEST_CANARIES).verdict == VERDICT_BLOCK, text[:20]


def test_canary_secondary_action_alert_incident_log(caplog: pytest.LogCaptureFixture) -> None:
    """Owner Q3=3.A: canary match → secondary_action ALERT + log security-incident
    riêng kênh scp.security.incident; log KHÔNG chứa giá trị canary."""
    with caplog.at_level(logging.ERROR, logger="scp.security.incident"):
        r = scan_output("Token kiểm toán: canary_token_v98_alpha_leak", {}, GD1_TEST_CANARIES)
    assert r.verdict == VERDICT_BLOCK and r.canary_alert
    assert r.secondary_action == "ALERT"
    assert any("GD1-INCIDENT" in rec.getMessage() for rec in caplog.records)
    assert all("canary_token_v98" not in rec.getMessage() for rec in caplog.records)


def test_no_secret_registered_still_allows() -> None:
    """Không đăng ký secret nào → gate cho qua (value-based, không phrase-list)."""
    r = scan_output("Biến SCP_ADMIN_KEY dùng để làm gì và nên xoay vòng bao lâu?", {}, ())
    assert r.verdict == VERDICT_ALLOW
