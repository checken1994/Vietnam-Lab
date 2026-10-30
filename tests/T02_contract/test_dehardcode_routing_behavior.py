"""[DEHARDCODE 2026-10-30] Hành vi sau khi gỡ mẫu câu hỏi viết cứng.

Hợp đồng (đỏ trên main aceb137b, xanh sau fix):
B1  Lời chào / câu hỏi danh tính / smalltalk / cảm ơn / trí nhớ bằng NHIỀU ngôn
    ngữ (câu do test tự viết, không lấy từ bộ eval — leak test kiểm riêng) KHÔNG
    BAO GIỜ thành "hội thoại thuần" (giao không kiểm chứng) khi không có đồng
    thuận 2 lineage ngữ nghĩa; reason không còn tag ``conversational_*``.
B2  Bất định (L0 không quyết, classifier L2 chết/trả rác) ⇒ LANE_FACTUAL.
B3  Đồng thuận kép L1+L2 (lineage giả lập, cơ chế thật: consensus → arbitrate)
    ⇒ CHATBOT ``via == CONSENSUS_VIA`` và chỉ khi đó predicate hội thoại True;
    câu có chữ số ⇒ False; L1 chết ⇒ KHÔNG gọi L2.
B4  Delivery không còn câu trả lời đóng hộp: thiếu bản nháp ⇒ withheld.
B5  10 expert: câu khớp khoá cũ ⇒ không còn đáp án viết cứng (no-evidence).
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

import scp.runtime.question_router as qr
from scp.api_server_parts.pipeline.delivery_rules import DeliveryRuleEngine
from scp.runtime.semantic_router import lineage_embed

#: Hợp đồng arbitration Rule 3 (khai báo ở test để bộ test CHẠY được cả trên main
#: — đo hành vi đỏ thật, không phải lỗi import). Test b3 khoá nó với code.
CONSENSUS_VIA = "arb:chatbot-dual-consensus"

REPO = Path(__file__).resolve().parents[2]

# Câu do test tự viết (đa ngôn ngữ). Không phải câu của bộ realq.
CONVERSATIONAL_PARAPHRASES = (
    ("vi", "xin chào"),
    ("vi", "bạn tên là gì"),
    ("vi", "cảm ơn nhiều nhé"),
    ("vi", "dạo này bạn ổn không"),
    ("en", "hello"),
    ("en", "who are you"),
    ("en", "thanks a lot"),
    ("en", "hey there, nice to see you"),
    ("fr", "Bonjour ! Comment tu t'appelles ?"),
    ("fr", "Merci beaucoup"),
    ("de", "Hallo, wer bist du eigentlich"),
    ("de", "Danke schön"),
    ("es", "Hola, ¿cómo te llamas?"),
    ("es", "muchas gracias"),
    ("pt", "Olá, tudo bem com você"),
    ("it", "Ciao, come stai"),
    ("ru", "Привет, как тебя зовут?"),
    ("ja", "こんにちは、あなたは誰ですか"),
    ("zh", "你好，你叫什么名字"),
    ("ko", "안녕하세요, 이름이 뭐예요"),
    ("ar", "مرحبا، ما اسمك"),
    ("hi", "नमस्ते, आप कौन हैं"),
    ("tr", "Merhaba, adın ne"),
    ("id", "Halo, siapa namamu"),
    ("pl", "Cześć, jak masz na imię"),
    ("nl", "Hoi, hoe gaat het met je"),
    ("sw", "Habari, jina lako nani"),
)

# Câu không có tín hiệu cú pháp nào cho L0 (L0 trả None) — đo nhánh bất định.
UNCERTAIN_NO_L0 = (
    "Habari za asubuhi rafiki yangu",
    "Hyvää huomenta kaikille",
    "Bonjour à tous",
)


class _DeadGateway:
    def chat(self, *a, **k):
        raise RuntimeError("hermetic: no LLM provider")


class _GarbageGateway:
    def chat(self, *a, **k):
        return "¯\\_(ツ)_/¯ banana", "fake"


class _AsyncJsonGateway:
    def __init__(self, label: str, conf: float = 0.95) -> None:
        self.calls = 0
        self.payload = json.dumps({"label": label, "confidence": conf})

    async def chat(self, *a, **k):
        self.calls += 1
        return self.payload, "fake-slm"


@pytest.fixture(autouse=True)
def _l1_dead_by_default(monkeypatch, tmp_path):
    """L1 không có model (trạng thái mặc định) — cache dir rỗng, singleton mới."""
    monkeypatch.setenv(lineage_embed.MODEL_CACHE_ENV, str(tmp_path / "no-model"))
    monkeypatch.delenv("SCP_ROUTER_CHATBOT_THRESHOLD", raising=False)
    lineage_embed.reset_default()
    yield
    lineage_embed.reset_default()


def _ctx(question: str, decision) -> types.SimpleNamespace:
    return types.SimpleNamespace(req=types.SimpleNamespace(question=question), route_decision=decision)


def _alive_l1(label: str, conf: float = 0.95):
    rest = (1.0 - conf) / 3
    pairs = tuple((c, conf if c == label else rest) for c in lineage_embed.CLASSES)
    return lineage_embed.L1EmbedVerdict(pairs=pairs, degraded=False, reason="ok", model_id="test-fake")


# ---------------------------------------------------------------- B1 / B2
@pytest.mark.parametrize("lang,question", CONVERSATIONAL_PARAPHRASES)
def test_b1_conversational_paraphrase_never_unverified_bypass(lang: str, question: str) -> None:
    decision = qr.route_question(question, gateway=_DeadGateway())
    assert "conversational" not in str(decision.reason)
    assert "user_identity" not in str(decision.reason)
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(question, decision)) is False, (
        lang, decision.to_dict())


@pytest.mark.parametrize("question", UNCERTAIN_NO_L0)
def test_b2_uncertain_with_dead_classifier_is_factual(question: str) -> None:
    assert qr.classify_l0(question) is None
    decision = qr.route_question(question, gateway=_DeadGateway())
    assert decision.lane == qr.LANE_FACTUAL, decision.to_dict()
    assert decision.via == "l2-failsafe"


def test_b2_uncertain_with_garbage_classifier_is_factual() -> None:
    decision = qr.route_question(UNCERTAIN_NO_L0[0], gateway=_GarbageGateway())
    assert decision.lane == qr.LANE_FACTUAL and decision.reason == "unparseable_fail_closed"


def test_b2_l2_reasoning_alone_is_not_conversational() -> None:
    class _Reasoning:
        def chat(self, *a, **k):
            return "REASONING", "fake"

    q = UNCERTAIN_NO_L0[1]
    decision = qr.route_question(q, gateway=_Reasoning())
    # [L0-DEKEYWORD 2026-10-31] Siết: 1 lineage đơn lẻ không còn được gợi ý lane CHATBOT
    # (arbitration Rule 3 — chỉ đồng thuận kép). Intent REASONING giữ, lane FACTUAL.
    assert decision.lane == qr.LANE_FACTUAL and str(decision.intent) == "REASONING"
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(q, decision)) is False


# ---------------------------------------------------------------- B3
def test_b3_dual_consensus_reaches_chatbot_and_conversational(monkeypatch) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    gw = _AsyncJsonGateway("CHATBOT")
    q = UNCERTAIN_NO_L0[2]
    decision = asyncio.run(qr.route_question_async(q, gateway=gw))
    assert decision.lane == qr.LANE_CHATBOT and decision.via == CONSENSUS_VIA, decision.to_dict()
    assert gw.calls == 1
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(q, decision)) is True
    # Cùng quyết định nhưng câu mang chữ số ⇒ không còn là hội thoại thuần.
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(q + " 2024", decision)) is False


def test_b3_sync_route_reaches_consensus_outside_and_inside_event_loop(monkeypatch) -> None:
    """/ask lookup_stage và /v1 compat gọi ``route_question`` (sync). Đồng thuận kép phải
    tới được từ đường sync — kể cả khi đang trong event loop — nếu không cơ chế tổng quát
    là placebo trong production."""
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    q = UNCERTAIN_NO_L0[2]
    gw = _AsyncJsonGateway("CHATBOT")
    d_sync = qr.route_question(q, gateway=gw)
    assert d_sync.via == CONSENSUS_VIA and gw.calls == 1, d_sync.to_dict()

    async def _inside_loop():
        return qr.route_question(q, gateway=gw)

    d_loop = asyncio.run(_inside_loop())
    assert d_loop.via == CONSENSUS_VIA and gw.calls == 2, d_loop.to_dict()
    # L2 bất đồng qua đường sync ⇒ không đồng thuận.
    d_no = qr.route_question(q, gateway=_AsyncJsonGateway("FACTUAL"))
    assert d_no.via != CONSENSUS_VIA


def test_b3_l2_disagrees_no_chatbot(monkeypatch) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    q = UNCERTAIN_NO_L0[2]
    decision = asyncio.run(qr.route_question_async(q, gateway=_AsyncJsonGateway("FACTUAL")))
    assert decision.via != CONSENSUS_VIA
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(q, decision)) is False


def test_b3_l1_dead_never_calls_l2_and_never_consensus() -> None:
    gw = _AsyncJsonGateway("CHATBOT")
    q = UNCERTAIN_NO_L0[2]
    decision = asyncio.run(qr.route_question_async(q, gateway=gw))
    assert decision.via != CONSENSUS_VIA
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx(q, decision)) is False


def test_b3_l1_security_wins(monkeypatch) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("SEC", 0.4))
    decision = qr.route_question(UNCERTAIN_NO_L0[0], gateway=_DeadGateway())
    assert decision.lane == qr.LANE_SECURITY


def test_b3_consensus_via_matches_arbitration_rule3() -> None:
    from scp.runtime.semantic_router.arbitration import arbitrate

    from scp.runtime.semantic_router.consensus import CONSENSUS_VIA as code_via

    d = arbitrate(_alive_l1("CHAT"), _alive_l1("CHAT"))
    assert d.lane == qr.LANE_CHATBOT and d.via == CONSENSUS_VIA == code_via


def test_b3_lane_chatbot_without_consensus_via_is_not_conversational() -> None:
    forged = qr.RouteDecision(intent=qr.REASONING, domain="general", confidence=0.99,
                              via="l0-keyword", reason="anything", lane=qr.LANE_CHATBOT)
    assert DeliveryRuleEngine.is_strictly_conversational(_ctx("hello", forged)) is False


# ---------------------------------------------------------------- B4
def _delivery_ctx(question: str, history=None):
    rd = qr.RouteDecision(intent=qr.REASONING, domain="general", confidence=0.95,
                          via=CONSENSUS_VIA, reason="chatbot_dual_consensus:min=0.95",
                          lane=qr.LANE_CHATBOT)
    return types.SimpleNamespace(
        req=types.SimpleNamespace(question=question), route_decision=rd, is_chatbot_lane=True,
        resolve=lambda name, default=None: default, web_fallback_used=False,
        has_provided_evidence=False, retrieval_res={}, ai_answer="", history=history or [],
        generation_status="", multimodal_block=False,
    )


def _verdict(verdict: str = "PASS"):
    return types.SimpleNamespace(verdict=verdict, evidence={}, final_answer="", reasoning="", confidence=0.9)


@pytest.mark.parametrize("question,history", [
    ("xin chào", None),
    ("bạn là ai", None),
    ("tên tôi là gì", [{"role": "user", "content": "tôi là Lan"}]),
])
def test_b4_no_canned_answer_when_nothing_generated(question, history) -> None:
    d = DeliveryRuleEngine.evaluate(_delivery_ctx(question, history), _verdict("PASS"), "ALLOW")
    assert str(d.final_answer or "").startswith("[SCP: Answer withheld"), d
    assert "SCP —" not in str(d.final_answer) and "Lan" not in str(d.final_answer)


def test_b4_no_fake_pass_greeting_provider_in_pipeline() -> None:
    import ast

    for rel in ("scp/api_server_parts/pipeline/generation_stage.py",
                "scp/api_server_parts/pipeline/verification_stage.py"):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert "deterministic_greeting" not in consts, rel  # provider giả cấp PASS cho câu chào


# ---------------------------------------------------------------- B5
_EXPERT_OLD_KEYS = (
    ("agriculture", "Agriculture", "what is agriculture"),
    ("art", "Art", "what is impressionism"),
    ("economics", "Economics", "what is gdp"),
    ("education", "Education", "what is pedagogy"),
    ("engineering", "Engineering", "what is engineering"),
    ("law", "Law", "what is a contract"),
    ("literature", "Literature", "shakespeare"),
    ("management", "Management", "what is swot"),
    ("military", "Military", "what is strategy"),
    ("philosophy", "Philosophy", "socrates"),
)


@pytest.mark.parametrize("module,cls,question", _EXPERT_OLD_KEYS)
def test_b5_expert_old_key_returns_no_canned_answer(monkeypatch, module, cls, question) -> None:
    import importlib

    from scp.knowledge.store import KnowledgeStore

    monkeypatch.setattr(KnowledgeStore, "is_enabled", staticmethod(lambda: False))
    import scp.data_sources.live_knowledge as live

    monkeypatch.setattr(live, "fetch_live", lambda *a, **k: None)
    expert = getattr(importlib.import_module(f"scp.runtime.experts.{module}"), cls)()
    for attr in ("_ds", "_fred", "_worldbank", "_eric", "_gutenberg"):
        if hasattr(expert, attr):
            setattr(expert, attr, None)
    if hasattr(expert, "_data_sources"):
        expert._data_sources = []
    monkeypatch.setattr(expert, "get_cached", lambda *a, **k: None)
    resp = expert.predict(question)
    assert not str(resp.answer or "").strip(), (module, resp.answer[:80])
    assert "local_knowledge" not in str(getattr(resp, "reasoning", ""))
