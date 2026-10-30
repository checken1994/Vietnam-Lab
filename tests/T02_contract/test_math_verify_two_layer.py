"""[MATH-VERIFY · 2026-10-31] SCP không giao đáp án có phép tính khi chưa tự tính lại
VÀ có cách hiểu câu hỏi độc lập với AI (chính sách owner 31-10: "AI và SCP đều sai
thì ai kiểm?").

Hợp đồng:
1. Tầng GIÁ TRỊ: mọi phương trình ký hiệu trong đáp án được tính lại bằng 2 bộ tính
   độc lập (safe_math float + Fraction chính xác). Lệch ⇒ withheld, nêu cả hai giá trị,
   KHÔNG BAO GIỜ thay bằng giá trị SCP (kể cả khi judge LLM nói PASS).
2. Tầng BIỂU THỨC: VERIFIED chỉ khi cách hiểu độc lập (câu hỏi tự là biểu thức ký hiệu,
   hoặc provider KHÁC lineage đọc câu hỏi thành biểu thức) trùng biểu thức AI dùng.
   Chỉ có biểu thức của chính AI (cùng lineage) ⇒ không verified ⇒ withheld.
3. Nhận diện theo KÝ HIỆU (chữ số mọi hệ Unicode, × ÷ √, LaTeX, markdown) — câu hỏi
   bằng chữ ở mọi ngôn ngữ KHÔNG được SCP tự "hiểu" bằng từ khóa.
4. Lệch / UNKNOWN ⇒ đúng 1 bài học HYPOTHESIS (unverified) vào ErrorStore; khớp ⇒ 0.
   Ghi bài học lỗi ⇒ người dùng vẫn nhận withheld (fail-closed).

Hermetic: gateway giả 2 lineage, judge giả LUÔN PASS (mô phỏng judge LLM tự chấm như
E2E c9aa96e7), web search bị cấm, không mạng.
"""
from __future__ import annotations

import asyncio
import random
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request as StarletteRequest

import scp.api_server_parts._ask_impl as ask_impl_mod
import scp.runtime.math_lessons as lessons_mod
import scp.runtime.question_router as qr_mod
from scp.api_server_parts._ask_impl import _ask_impl
from scp.api_server_parts.helpers import AskRequest
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.delivery_rules import apply_delivery_headers
from scp.api_server_parts.pipeline.generation_stage import GenerationStage
from scp.api_server_parts.pipeline.verification_stage import VerificationStage
from scp.brain.error_store import ErrorStore
from scp.runtime import math_claims as mc
from scp.runtime.atomic_verifier import AtomicDeterministicVerifier
from scp.runtime.question_router import LANE_CHATBOT, LANE_FACTUAL, RouteDecision

GEN_LABEL = "glm:glm-5.3"
READER_LABEL = "gemini_compat:gemini-2.5-flash"


class TwoLineageGateway:
    """Provider sinh (glm) + provider đọc biểu thức (gemini) — khác lineage."""

    def __init__(self, gen_answer: str, reader_answer: str | None, reader_label: str = READER_LABEL) -> None:
        self.gen_answer, self.reader_answer, self.reader_label = gen_answer, reader_answer, reader_label
        self.gen_calls = 0
        self.reader_calls = 0

    def get_active_provider_names(self) -> list[str]:
        return ["glm", "gemini_compat"]

    async def chat(self, question: str, context: str = "", system_prompt: str = "", task: str = "default",
                   messages: Any = None, preferred_provider: str = "") -> tuple[str | None, str]:
        if system_prompt.startswith("Rewrite the user's question"):
            self.reader_calls += 1
            return self.reader_answer, self.reader_label
        self.gen_calls += 1
        return self.gen_answer, GEN_LABEL


class _PassingJudge:
    async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
        return {"verdict": "PASS", "confidence": 0.65, "reasoning": "single provider", "failures": [],
                "evidence": {"governance_decision": "UPHOLD"}, "slm_responses": [],
                "final_answer": kwargs.get("ai_answer")}


def _production_class_judge() -> Any:
    """Judge cùng LỚP production (không bị coi là custom judge ⇒ đi đúng đường
    short-circuit tất định như runtime thật); bỏ __init__ nặng, chấm luôn PASS."""
    from scp.api_server_parts import helpers

    class _ProductionClassPassingJudge(type(helpers.get_judge())):  # type: ignore[misc]
        def __init__(self) -> None:  # không khởi tạo crawler/provider
            pass

        async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
            return await _PassingJudge().judge_with_react_fallback(**kwargs)

    judge = _ProductionClassPassingJudge()

    def get_judge() -> Any:  # tên "get_judge" ⇒ không bị nhận là custom judge_fn
        return judge

    return get_judge


class _ForbiddenSearch:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("hermetic: web search forbidden")


class _BrokenStore:
    def add(self, **_k: Any) -> dict[str, Any]:
        raise OSError("disk full (simulated)")


async def _noop(*_a: Any, **_k: Any) -> None:
    return None


def _request() -> StarletteRequest:
    req = StarletteRequest({
        "type": "http", "method": "POST", "path": "/ask", "headers": [(b"host", b"127.0.0.1")],
        "query_string": b"", "client": ("127.0.0.1", 1), "server": ("t", 80), "scheme": "http",
    })
    req.state.scp_run = None
    req.state.trace_id = "test-math-verify"
    return req


@pytest.fixture(autouse=True)
def _isolated_default_lesson_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Không để test ghi bài học vào data/error_store.jsonl thật của repo."""
    monkeypatch.setattr(lessons_mod, "_lesson_stores", {"default": ErrorStore(str(tmp_path / "default_store.jsonl"))})


@pytest.fixture
def ask(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_WEB_FALLBACK", "0")
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    monkeypatch.setenv("SCP_MATH_LESSON_MAX_PER_MIN", "1000")
    monkeypatch.delenv("SCP_BRAIN_MODE", raising=False)
    monkeypatch.setattr(ask_impl_mod, "_async_fact_check", _noop)
    monkeypatch.setattr(ask_impl_mod, "InternetSearch", _ForbiddenSearch)
    store = ErrorStore(str(tmp_path / "error_store.jsonl"))

    async def _run(question: str, gw: TwoLineageGateway, store_obj: Any = None) -> SimpleNamespace:
        monkeypatch.setattr(ask_impl_mod, "get_gateway", lambda: gw, raising=False)
        monkeypatch.setattr(ask_impl_mod, "get_judge", _production_class_judge())
        monkeypatch.setattr(ask_impl_mod, "_math_lesson_store", lambda: store_obj or store, raising=False)
        request = _request()
        res = await _ask_impl(AskRequest(question=question), request)
        out = SimpleNamespace(headers={})
        apply_delivery_headers(out, res, request.state)
        return SimpleNamespace(res=res, headers=out.headers, state=request.state, store=store)

    return _run


def _lessons(store: ErrorStore) -> list[dict[str, Any]]:
    return [r for r in store.get_recent(1000) if r.get("domain") == "math"]


# 1. AI sai + SCP đúng ⇒ withheld, KHÔNG giao giá trị nào như verified ------------
@pytest.mark.asyncio
async def test_wrong_llm_value_is_withheld_even_when_judge_passes(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("12 × 7 = **85**", "12 * 7"))
    assert out.headers["x-scp-delivery"] == "withheld"
    assert out.headers["x-scp-decision-code"] == "ERR_MATH_UNVERIFIED"
    assert out.res.verdict != "PASS"
    assert str(out.res.final_answer).startswith("[SCP: Answer withheld")
    # cả hai giá trị được nêu để người kiểm; không cái nào được giao như đáp án
    assert "85" in out.res.final_answer and "84" in out.res.final_answer
    assert out.res.final_answer != "12 × 7 = **84**"


# 2. AI đúng + cách hiểu độc lập khác lineage trùng ⇒ VERIFIED -------------------
@pytest.mark.asyncio
async def test_correct_llm_value_with_independent_reading_is_verified(ask) -> None:
    gw = TwoLineageGateway("12 × 7 = **84**", "12 * 7")
    out = await ask("12 nhân 7", gw)
    assert out.res.verdict == "PASS"
    assert out.headers["x-scp-delivery"] == "verified"
    assert out.headers["x-scp-decision-code"] == "DELIVER_VERIFIED_PASS"
    assert out.res.final_answer == "12 × 7 = **84**"  # đáp án giao kèm biểu thức cho người đọc
    assert gw.reader_calls == 1
    assert "independent:gemini" in out.res.reasoning


# 3. Không trích được biểu thức ⇒ SCP không đóng dấu kiểm chứng tất định ---------
@pytest.mark.asyncio
async def test_unextractable_answer_never_gets_deterministic_label(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("Kết quả là 84.", "12 * 7"))
    cls = out.res.v98_classification or {}
    assert cls.get("route") != "atomic_deterministic_local"
    assert cls.get("adjudicated_by") != "atomic_deterministic_local"
    assert "[MATH-VERIFY]" not in str(out.res.reasoning or "")
    assert out.res.confidence != 0.98


# 4. Căn bậc hai 144 ⇒ có 12 + nhãn kiểm chứng --------------------------------
@pytest.mark.asyncio
async def test_sqrt_symbol_question_is_deterministic_without_llm(ask) -> None:
    gw = TwoLineageGateway("SHOULD NOT BE CALLED", None)
    out = await ask("√144", gw)
    assert gw.gen_calls == 0 and gw.reader_calls == 0
    assert out.res.final_answer == "sqrt(144) = 12"
    assert out.headers["x-scp-delivery"] == "verified"
    assert out.res.verdict == "PASS"
    assert "question_notation" in out.res.reasoning


@pytest.mark.asyncio
async def test_sqrt_word_question_verified_by_independent_reader(ask) -> None:
    out = await ask("Căn bậc hai của 144?", TwoLineageGateway("√144 = **12**", "sqrt(144)"))
    assert "12" in out.res.final_answer
    assert out.headers["x-scp-delivery"] == "verified"
    assert "independent:gemini" in out.res.reasoning


# 5. Hai cách hiểu khác nhau ⇒ withheld ---------------------------------------
@pytest.mark.asyncio
async def test_different_readings_of_question_are_withheld(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("12 + 7 = 19", "12 * 7"))
    assert out.headers["x-scp-delivery"] == "withheld"
    assert "MISMATCH_EXPRESSION" in out.res.final_answer
    assert "19" in out.res.final_answer and "84" in out.res.final_answer


# 6. Chỉ cùng lineage ⇒ không verified ----------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("reader_label", [GEN_LABEL, "groq:glm-4-9b"])
async def test_same_lineage_reader_never_counts_as_independent(ask, reader_label: str) -> None:
    gw = TwoLineageGateway("12 × 7 = 84", "12 * 7", reader_label=reader_label)
    out = await ask("12 nhân 7", gw)
    assert out.headers["x-scp-delivery"] == "withheld"
    assert "SAME_LINEAGE" in out.res.final_answer


@pytest.mark.asyncio
async def test_reader_saying_not_arithmetic_leaves_incidental_equation_to_judge(ask) -> None:
    out = await ask("Nhiệm kỳ tổng thống Mỹ kéo dài bao lâu?",
                    TwoLineageGateway("Mỗi nhiệm kỳ 4 năm; ví dụ 2021 + 4 = 2025.", "NONE"))
    assert out.headers["x-scp-decision-code"] != "ERR_MATH_UNVERIFIED"


# 7. Đa ngôn ngữ / đa hệ chữ số ----------------------------------------------
MULTILINGUAL = [
    ("Combien font 17 fois 23 ?", "17 × 23 = 391", "17 * 23", "17 × 23 = 392"),
    ("Was ist 15 mal 4?", "15 · 4 = 60", "15 * 4", "15 · 4 = 61"),
    ("¿Cuánto es 9 por 8?", "9 x 8 = 72", "9 * 8", "9 x 8 = 73"),
    ("１２かける７は？", "１２×７＝８４", "12 * 7", "１２×７＝８５"),
    ("十二乘以七等于多少？", "12 × 7 = 84", "12 * 7", "12 × 7 = 48"),
    ("ما هو ١٢ ضرب ٧؟", "١٢ × ٧ = ٨٤", "12 * 7", "١٢ × ٧ = ٨٥"),
    ("What is 15% of 240?", "240 × 15% = 36", "240 * (15 / 100)", "240 × 15% = 34"),
    ("१२ गुणा ७ कितना है?", "१२ × ७ = ८४", "12 * 7", "१२ × ७ = ८६"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "good", "reader", "bad"), MULTILINGUAL)
async def test_multilingual_two_layer(ask, question: str, good: str, reader: str, bad: str) -> None:
    ok = await ask(question, TwoLineageGateway(good, reader))
    assert ok.headers["x-scp-delivery"] == "verified", ok.res.final_answer
    wrong = await ask(question, TwoLineageGateway(bad, reader))
    assert wrong.headers["x-scp-delivery"] == "withheld"
    assert wrong.headers["x-scp-decision-code"] == "ERR_MATH_UNVERIFIED"


@pytest.mark.parametrize("question", [
    "12 nhân 7", "Căn bậc hai của 144?", "Combien font 17 fois 23 ?", "Was ist 15 mal 4?",
    "¿Cuánto es 9 por 8?", "１２かける７は？", "十二乘以七等于多少？", "ما هو ١٢ ضرب ٧؟", "What is 15% of 240?",
])
def test_scp_does_not_read_words_as_operators(question: str) -> None:
    """Không danh sách từ khóa: câu hỏi có chữ ⇒ SCP KHÔNG tự hiểu (cần nguồn độc lập)."""
    assert mc.parse_question_expression(question) is None


@pytest.mark.parametrize(("question", "value"), [
    ("1+1", "2"), ("tính 6 * 7", "42"), ("１２×７", "84"), ("١٢ × ٧", "84"), ("√144", "12"),
    ("12 ÷ 4", "3"), ("(2 + 3) × 4", "20"), ("1,5 + 2", "3.5"), ("۱۲ × ۷", "84"),
])
def test_symbolic_questions_are_parsed_structurally(question: str, value: str) -> None:
    expr = mc.parse_question_expression(question)
    assert expr is not None and expr.value_str() == value


def test_caret_in_question_is_ambiguous_and_never_fast_pathed() -> None:
    # safe_math giữ '^' = XOR (2^10 = 8): đọc nó như đáp án sẽ giao 8 sai.
    assert mc.parse_question_expression("2^10") is None
    assert [e.status for e in mc.extract_equations("2^10 = 1024")] == ["MATCH"]
    assert [e.status for e in mc.extract_equations("2^10 = 8")] == ["MISMATCH"]


@pytest.mark.parametrize("question", ["１２×７", "١٢ × ٧", "√144"])
def test_fast_path_unicode_notation_without_llm(question: str) -> None:
    calls: list[int] = []

    class _Gw:
        async def chat(self, *_a: Any, **_k: Any) -> tuple[str, str]:
            calls.append(1)
            return "x", "y"

    ns = {"get_gateway": lambda: _Gw(), "InternetSearch": _ForbiddenSearch, "_brain_shadow_generate": _noop}
    ctx = AskPipelineContext(req=AskRequest(question=question), request=_request(), namespace=ns)
    ctx.route_decision = RouteDecision(intent="REASONING", domain="general", confidence=0.8, via="test", lane=LANE_FACTUAL)
    asyncio.run(GenerationStage().execute(ctx))
    assert calls == [] and ctx.generation_status == "deterministic"
    assert ctx.ai_answer.endswith(" = 84") or ctx.ai_answer == "sqrt(144) = 12"


# 8. Bài học: lệch ⇒ đúng 1 bản ghi unverified; khớp ⇒ 0; ghi lỗi ⇒ vẫn withheld --
@pytest.mark.asyncio
async def test_mismatch_records_exactly_one_unverified_lesson(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("12 × 7 = 85", "12 * 7"))
    lessons = _lessons(out.store)
    assert len(lessons) == 1
    rec = lessons[0]
    assert rec["verdict"] == "UNVERIFIED"
    assert rec["details"]["epistemic_status"] == "HYPOTHESIS"
    assert rec["details"]["verified"] is False
    assert rec["details"]["promotion"] == "never_automatic"
    assert rec["details"]["equations"][0]["claimed"] == "85"
    assert rec["details"]["equations"][0]["scp_value"] == "84"
    assert rec["details"]["generator"]["lineage"] == "glm"


@pytest.mark.asyncio
async def test_verified_match_records_no_lesson(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("12 × 7 = 84", "12 * 7"))
    assert out.headers["x-scp-delivery"] == "verified"
    assert _lessons(out.store) == []


@pytest.mark.asyncio
async def test_lesson_write_failure_keeps_user_outcome_withheld(ask) -> None:
    out = await ask("12 nhân 7", TwoLineageGateway("12 × 7 = 85", "12 * 7"), store_obj=_BrokenStore())
    assert out.headers["x-scp-delivery"] == "withheld"
    assert out.headers["x-scp-decision-code"] == "ERR_MATH_UNVERIFIED"


def test_lesson_redacts_secrets_and_respects_rate_cap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    store = ErrorStore(str(tmp_path / "es.jsonl"))
    ctx = SimpleNamespace(
        req=SimpleNamespace(question="api_key=sk-THISISSECRET123 tính 12 × 7 gửi a@b.com"),
        ai_answer="12 × 7 = 85", llm_provider=GEN_LABEL, generation_status="llm", run_id="r",
        resolve=lambda name, default=None: (lambda: store) if name == "_math_lesson_store" else default,
    )
    monkeypatch.setattr(lessons_mod, "_lesson_times", [])
    monkeypatch.setenv("SCP_MATH_LESSON_MAX_PER_MIN", "2")
    assessment = mc.assess("x", "12 × 7 = 85").to_dict()
    assert [lessons_mod.record_math_lesson(ctx, assessment) for _ in range(3)] == [True, True, False]
    rec = _lessons(store)[0]
    assert "sk-THISISSECRET123" not in rec["question"] and "a@b.com" not in rec["question"]


# 9. Lane CHATBOT (đường E2E B2): đã kiểm chứng ⇒ nhãn verified, không conversational
def test_chatbot_lane_verified_math_is_not_labeled_conversational() -> None:
    gw = TwoLineageGateway("unused", "12 * 7")
    ctx = AskPipelineContext(req=AskRequest(question="12 nhân 7"), request=_request(),
                             namespace={"get_gateway": lambda: gw, "_math_lesson_store": lambda: _BrokenStore(),
                                        "get_judge": _production_class_judge()})
    ctx.route_decision = RouteDecision(intent="REASONING", domain="general", confidence=0.8, via="l2-llm", lane=LANE_CHATBOT)
    ctx.is_chatbot_lane = True
    ctx.ai_answer, ctx.llm_provider, ctx.generation_status = "12 × 7 = **84**", GEN_LABEL, "llm"
    asyncio.run(VerificationStage().execute(ctx))
    assert ctx.v98_context["decision_code"] == "DELIVER_VERIFIED_PASS"
    assert ctx.request.state.is_conversational is False
    out = SimpleNamespace(headers={})
    apply_delivery_headers(out, SimpleNamespace(verdict="PASS", governance_decision="UPHOLD",
                                                final_answer=ctx.api_final_answer, lane=LANE_CHATBOT), ctx.request.state)
    assert out.headers["x-scp-delivery"] == "verified"


# 10. Atomic verifier: phép tính sai là CONTRADICTED, không bao giờ "sửa" ---------
@pytest.mark.parametrize("draft", ["12 × 7 = **85**", "2 + 2 = 5.", "17 * 23 bằng 400"])
def test_atomic_never_substitutes_scp_value(draft: str) -> None:
    res = AtomicDeterministicVerifier().verify_and_prune(question="q", draft_answer=draft)
    assert res["claims_payload"][0]["category"] == "arithmetic"
    assert res["claims_payload"][0]["status"] == "CONTRADICTED"
    assert res["repaired_answer"] is None
    assert res["all_verified_locally"] is False


# 11. Bộ tính: known-answer + đối chứng Fraction độc lập + giới hạn ---------------
@pytest.mark.parametrize(("expr", "expected"), [
    ("12*7", Fraction(84)), ("sqrt(144)", Fraction(12)), ("10/4", Fraction(5, 2)), ("2**10", Fraction(1024)),
    ("(1+2)*(3+4)", Fraction(21)), ("0.1+0.2", Fraction(3, 10)), ("-3*-3", Fraction(9)), ("sqrt(1/4)", Fraction(1, 2)),
])
def test_calculator_known_answers(expr: str, expected: Fraction) -> None:
    assert mc.evaluate_dual(expr) == expected


def test_calculator_matches_independent_fraction_oracle() -> None:
    rng = random.Random(20261031)
    ops = [("+", lambda a, b: a + b), ("-", lambda a, b: a - b), ("*", lambda a, b: a * b), ("/", lambda a, b: a / b)]
    for _ in range(300):
        a, b, c = (rng.randint(-999, 999) for _ in range(3))
        (s1, f1), (s2, f2) = rng.choice(ops), rng.choice(ops)
        if (s1 == "/" and b == 0) or (s2 == "/" and c == 0):
            continue
        oracle = f2(f1(Fraction(a), Fraction(b)), Fraction(c))
        assert mc.evaluate_dual(f"(({a}) {s1} ({b})) {s2} ({c})") == oracle


@pytest.mark.parametrize("expr", ["5/0", "sqrt(-1)", "2**5000", "10**400"])
def test_calculator_limits_are_unrecomputable_not_guessed(expr: str) -> None:
    with pytest.raises(ValueError):
        mc.evaluate_dual(expr)


def test_calculator_disagreement_between_evaluators_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hai bộ tính độc lập: một bộ có lỗi ⇒ UNRECOMPUTABLE, không bao giờ tin một bên."""
    monkeypatch.setattr(mc, "safe_eval_arithmetic", lambda expr, **_k: 85.0)
    with pytest.raises(ValueError, match="evaluators disagree"):
        mc.evaluate_dual("12*7")
    assert [e.status for e in mc.extract_equations("12 × 7 = 84")] == ["UNRECOMPUTABLE"]


def test_calculator_float_bound_is_enforced_even_when_exact_value_exists() -> None:
    with pytest.raises(ValueError, match="float evaluator"):
        mc.evaluate_dual("10**301")


@pytest.mark.asyncio
async def test_custom_judge_keeps_authority_on_verified_math_but_not_on_mismatch(ask, monkeypatch) -> None:
    """SEC-R2-02 nhất quán: judge tuỳ biến vẫn chấm phép tính đã khớp; lệch thì
    cổng toán withheld bất kể judge nói gì."""
    class _NoGovJudge:
        async def judge_with_react_fallback(self, **kwargs: Any) -> dict[str, Any]:
            return {"verdict": "PASS", "confidence": 0.9, "reasoning": "ok", "evidence": {},
                    "final_answer": kwargs.get("ai_answer")}

    from fastapi import HTTPException

    monkeypatch.setattr(ask_impl_mod, "get_gateway", lambda: TwoLineageGateway("12 × 7 = 84", "12 * 7"), raising=False)
    monkeypatch.setattr(ask_impl_mod, "get_judge", lambda: _NoGovJudge())
    with pytest.raises(HTTPException):
        await _ask_impl(AskRequest(question="12 nhân 7"), _request())
    monkeypatch.setattr(ask_impl_mod, "get_gateway", lambda: TwoLineageGateway("12 × 7 = 85", "12 * 7"), raising=False)
    res = await _ask_impl(AskRequest(question="12 nhân 7"), _request())
    assert str(res.final_answer).startswith("[SCP: Answer withheld") and res.verdict != "PASS"


# 12. [E2E raw2 B1] "khớp nguồn" ≠ "trả lời câu hỏi": câu hỏi có phép tính mà đáp án
#     KHÔNG chứa kết quả ⇒ không bao giờ VERIFIED_PASS, dù đi đường lookup hay LLM.
WIKI_SQRT = ("Trong toán học, căn bậc hai của một số a là một số x sao cho x2 = a, hay một cách nói "
             "khác là số x mà bình phương lên thì = a. Ví dụ, 4 và −4 là căn bậc hai của 16 vì .")


def _wiki_payload(text: str) -> dict[str, Any]:
    return {"text": text, "api_name": "Wikipedia (vi) — Căn bậc hai",
            "api_url": "https://vi.wikipedia.org/wiki/C%C4%83n_b%E1%BA%ADc_hai", "domain": "general"}


@pytest.mark.asyncio
async def test_lookup_fork_never_delivers_passage_without_question_numbers(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    monkeypatch.setattr(lessons_mod, "_lesson_times", [])
    monkeypatch.setattr(lessons_mod, "_lesson_stores", {"default": ErrorStore(str(tmp_path / "error_store.jsonl"))})
    monkeypatch.setattr(qr_mod, "resolve_lookup_data", lambda *_a, **_k: _wiki_payload(WIKI_SQRT))
    req = SimpleNamespace(question="Căn bậc hai của 144 là bao nhiêu?", contexts=[], retrieved_context="",
                          ai_answer="", session_id=None)
    decision = await qr_mod.route_question_async(req.question)
    assert decision.intent == qr_mod.LOOKUP, "precondition: E2E route (L0 lookup) reproduced"
    assert await qr_mod.attempt_lookup_fork(req) is None
    rec = [r for r in ErrorStore(str(tmp_path / "error_store.jsonl")).get_recent(100) if r.get("domain") == "math"]
    assert len(rec) == 1 and rec[0]["details"]["status"] == "LOOKUP_NUMERIC_IRRELEVANT"
    assert rec[0]["details"]["verified"] is False


@pytest.mark.usefixtures("relevance_confirmed")  # [RELEVANCE] tiền điều kiện: reader độc lập đã xác nhận
@pytest.mark.asyncio
async def test_lookup_fork_still_serves_passage_that_contains_question_numbers(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCP_T2_ROUTER", "0")
    text = WIKI_SQRT + " Căn bậc hai của 144 là 12."
    monkeypatch.setattr(qr_mod, "resolve_lookup_data", lambda *_a, **_k: _wiki_payload(text))
    req = SimpleNamespace(question="Căn bậc hai của 144 là bao nhiêu?", contexts=[], retrieved_context="",
                          ai_answer="", session_id=None)
    res = await qr_mod.attempt_lookup_fork(req)
    assert res is not None and res["verdict"] == "PASS" and "144" in res["final_answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "Căn bậc hai của a là số x sao cho x nhân x bằng a.",  # không có số nào
    WIKI_SQRT,  # có số (4, 16) nhưng không có kết quả 12
])
async def test_llm_answer_without_computed_result_is_withheld(ask, answer: str) -> None:
    out = await ask("Căn bậc hai của 144 là bao nhiêu?", TwoLineageGateway(answer, "sqrt(144)"))
    assert out.headers["x-scp-delivery"] == "withheld"
    assert out.headers["x-scp-decision-code"] == "ERR_MATH_UNVERIFIED"
    assert "MISSING_RESULT" in out.res.final_answer
    assert len(_lessons(out.store)) == 1


@pytest.mark.asyncio
async def test_llm_answer_with_bare_result_but_no_expression_is_not_verified(ask) -> None:
    out = await ask("Căn bậc hai của 144 là bao nhiêu?", TwoLineageGateway("Đáp án là 12.", "sqrt(144)"))
    assert out.headers["x-scp-delivery"] == "withheld"
    assert "EXPRESSION_UNCONFIRMED" in out.res.final_answer


@pytest.mark.asyncio
async def test_digit_question_that_reader_calls_non_arithmetic_is_untouched(ask) -> None:
    out = await ask("Viết 2 câu thơ về con mèo", TwoLineageGateway("Mèo nằm phơi nắng sân hiên,\nLim dim đôi mắt ngủ yên giấc nồng.", "NONE"))
    assert out.headers["x-scp-decision-code"] != "ERR_MATH_UNVERIFIED"


# Câu hỏi tự là biểu thức ký hiệu + đáp án CHỈ nêu giá trị ⇒ VERIFIED (biểu thức hiển thị = câu hỏi);
# có số cạnh tranh ⇒ vẫn withheld; thiếu kết quả ⇒ MISSING_RESULT.
@pytest.mark.parametrize("question,answer,status", [
    ("17×23", "391", "VERIFIED"),
    ("17×23", "= 391.", "VERIFIED"),
    ("۱۷ × ۲۳", "٣٩١", "VERIFIED"),
    ("17×23", "391, hoặc 392", "EXPRESSION_UNCONFIRMED"),
    ("17×23", "Kết quả: 17 và 391", "EXPRESSION_UNCONFIRMED"),
    ("17×23", "392", "MISSING_RESULT"),
])
def test_bare_value_answer_to_pure_notation_question(question: str, answer: str, status: str) -> None:
    res = mc.assess(question, answer)
    assert res.status == status
    if status == "VERIFIED":
        assert res.reference_lineage == "question_notation"
        assert res.to_dict()["independent"] is False  # không gọi là kiểm chứng độc lập khác lineage
