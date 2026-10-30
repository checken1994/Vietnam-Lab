"""[L0-DEKEYWORD 2026-10-31] L0 không còn danh sách từ khoá theo ngôn ngữ (luật 9 + đa ngôn ngữ).

Hợp đồng (đỏ trên d6aef0a9, xanh sau fix). Dữ liệu câu hỏi nằm NGOÀI scp/
(tests/fixtures/router_multilingual/): dev_v1 (12 ngôn ngữ, gồm ja/zh/ar/hi/ru/th/ko) +
holdout_v1 (13 ngôn ngữ khác/cách hỏi khác, đóng băng sha256 TRƯỚC khi viết fix, không dùng để
chỉnh code).

K1  L0 KHÔNG BAO GIỜ trả LANE_CHATBOT (CHATBOT chỉ qua đồng thuận kép ngữ nghĩa).
K2  L0 không quyết câu ngôn ngữ tự nhiên (chào/danh tính/kiến thức/yêu cầu) ở MỌI ngôn ngữ ⇒
    ``None`` ⇒ tầng ngữ nghĩa xét; quyết định L0 bất biến theo ngôn ngữ trong cùng nhóm nghĩa.
K3  Cú pháp ký hiệu số học (không chữ cái) vẫn LANE_FACTUAL ở mọi ngôn ngữ (bảo toàn).
K4  Câu tự nhiên ĐẾN được cổng ngữ nghĩa (L1 được hỏi) ở mọi ngôn ngữ.
K5  Một phiếu LLM đơn lẻ (L1 chết) KHÔNG lên CHATBOT; L1 CHAT không được L2 xác nhận ⇒ FACTUAL.
K6  Đồng thuận kép L1+L2 vẫn tới CHATBOT ``via == CONSENSUS_VIA`` ở mọi ngôn ngữ (không chặn quá).
K7  Bài học HYPOTHESIS có cấu trúc khi L1/L2 lệch hoặc CHAT không được xác nhận; lỗi kho bài học
    không đổi quyết định; có trần tần suất.
K8  Chống tái mã hoá cứng: bảng luật định tuyến không-an-ninh không chứa chữ cái; classify_l0
    không dựng LANE_CHATBOT; không câu mẫu nào của fixture nằm trong code định tuyến.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pytest

import scp.runtime.question_router as qr
from scp.runtime import router_rules
from scp.runtime.semantic_router import lineage_embed

REPO = Path(__file__).resolve().parents[2]
FIX_DIR = REPO / "tests" / "fixtures" / "router_multilingual"
#: sha256 (LF-normalised) của hold-out, ghi lúc đóng băng 2026-10-31T04:15+07:00 trước khi viết fix.
HOLDOUT_SHA256 = "e1f1ce3b221a2774faedb36f30e1aa2eebdb52198312b7e55e1a7fe63267df22"
#: Khai báo ở test để chạy được cả trên base (đo hành vi đỏ thật, không phải lỗi import).
CONSENSUS_VIA = "arb:chatbot-dual-consensus"
NATURAL_GROUPS = ("greeting", "identity", "knowledge", "task")


def _load(name: str) -> dict[str, dict[str, str]]:
    return json.loads((FIX_DIR / f"{name}.json").read_text(encoding="utf-8"))["groups"]


DEV = _load("dev_v1")
HOLDOUT = _load("holdout_v1")


def _rows(groups_filter=None):
    out = []
    for set_name, data in (("dev", DEV), ("holdout", HOLDOUT)):
        for group, langs in data.items():
            if groups_filter and group not in groups_filter:
                continue
            for lang, q in langs.items():
                out.append(pytest.param(q, id=f"{set_name}-{group}-{lang}"))
    return out


class _DeadGateway:
    def chat(self, *a, **k):
        raise RuntimeError("hermetic: no LLM provider")


class _SyncLabelGateway:
    def __init__(self, label: str) -> None:
        self.label = label

    def chat(self, *a, **k):
        return self.label, "fake-llm"


class _AsyncJsonGateway:
    def __init__(self, label: str, conf: float = 0.99) -> None:
        self.calls = 0
        self.payload = json.dumps({"label": label, "confidence": conf})

    async def chat(self, *a, **k):
        self.calls += 1
        return self.payload, "fake-slm"


class _AsyncDeadGateway:
    async def chat(self, *a, **k):
        raise RuntimeError("hermetic: L2 lineage down")


class _FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[dict] = []
        self.fail = fail

    def add(self, **kw):
        if self.fail:
            raise OSError("disk full (simulated)")
        self.records.append(kw)
        return {"id": f"r{len(self.records)}"}


def _alive_l1(label: str, conf: float = 0.99):
    rest = (1.0 - conf) / 3
    pairs = tuple((c, conf if c == label else rest) for c in lineage_embed.CLASSES)
    return lineage_embed.L1EmbedVerdict(pairs=pairs, degraded=False, reason="ok", model_id="test-fake")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """L1 không có model (mặc định); kho bài học giả (không ghi data/ thật)."""
    monkeypatch.setenv(lineage_embed.MODEL_CACHE_ENV, str(tmp_path / "no-model"))
    monkeypatch.delenv("SCP_ROUTER_CHATBOT_THRESHOLD", raising=False)
    monkeypatch.delenv("SCP_ROUTING_LESSONS", raising=False)
    monkeypatch.delenv("SCP_ROUTING_LESSON_MAX_PER_MIN", raising=False)
    lineage_embed.reset_default()
    store = _FakeStore()
    try:
        from scp.runtime.semantic_router import routing_lessons

        monkeypatch.setattr(routing_lessons, "STORE_FACTORY", lambda: store)
        monkeypatch.setattr(routing_lessons, "_times", [])
        monkeypatch.setattr(routing_lessons, "_seen", {})
    except ImportError:  # base: module chưa tồn tại — test liên quan đỏ ở chỗ khác
        pass
    yield store
    lineage_embed.reset_default()


# --------------------------------------------------------------------------- dữ liệu
def test_holdout_fixture_is_frozen() -> None:
    raw = (FIX_DIR / "holdout_v1.json").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == HOLDOUT_SHA256
    langs = {lang for g in HOLDOUT.values() for lang in g}
    assert len(langs) >= 8 and not langs.isdisjoint({"he", "fa", "bn", "uk"})  # có chữ không-Latin


def test_dev_fixture_covers_required_scripts() -> None:
    langs = {lang for g in DEV.values() for lang in g}
    assert {"ja", "zh", "ar", "hi", "ru", "th"} <= langs and len(langs) >= 8
    assert set(DEV) >= {"greeting", "identity", "knowledge", "task", "math_symbolic", "attack"}


# --------------------------------------------------------------------------- K1
@pytest.mark.parametrize("question", _rows())
def test_k1_l0_never_emits_chatbot_lane(question: str) -> None:
    d = qr.classify_l0(question)
    assert d is None or d.lane != qr.LANE_CHATBOT, d.to_dict()


# --------------------------------------------------------------------------- K2
@pytest.mark.parametrize("question", _rows(NATURAL_GROUPS))
def test_k2_l0_abstains_on_natural_language(question: str) -> None:
    d = qr.classify_l0(question)
    assert d is None, d.to_dict()


@pytest.mark.parametrize("set_name,group", [(s, g) for s in ("dev", "holdout")
                                             for g in (*NATURAL_GROUPS, "math_symbolic")])
def test_k2_l0_decision_is_language_invariant(set_name: str, group: str) -> None:
    data = DEV if set_name == "dev" else HOLDOUT
    outcomes = {}
    for lang, q in data[group].items():
        d = qr.classify_l0(q)
        outcomes[lang] = None if d is None else (d.lane, d.reason)
    assert len(set(outcomes.values())) == 1, outcomes


# --------------------------------------------------------------------------- K3
@pytest.mark.parametrize("question", _rows(("math_symbolic",)))
def test_k3_symbolic_arithmetic_is_factual_in_every_language(question: str) -> None:
    d = qr.classify_l0(question)
    assert d is not None and d.lane == qr.LANE_FACTUAL, question
    assert d.reason == "reasoning_signal:arithmetic"  # consumer lookup_stage đọc tag này


# --------------------------------------------------------------------------- K4
@pytest.mark.parametrize("question", _rows(NATURAL_GROUPS))
def test_k4_natural_language_reaches_semantic_gate(monkeypatch, question: str) -> None:
    seen: list[str] = []

    def _record(text: str):
        seen.append(text)
        return lineage_embed.L1EmbedVerdict.degraded_verdict("test_dead")

    monkeypatch.setattr(lineage_embed, "classify", _record)
    d = qr.route_question(question, gateway=_DeadGateway())
    assert seen == [qr.normalize_query(question)], d.to_dict()
    assert d.lane == qr.LANE_FACTUAL


# --------------------------------------------------------------------------- K5
@pytest.mark.parametrize("question", _rows(NATURAL_GROUPS))
def test_k5_single_llm_vote_never_chatbot(question: str) -> None:
    d = qr.route_question(question, gateway=_SyncLabelGateway("REASONING"))
    assert d.lane == qr.LANE_FACTUAL, d.to_dict()


def test_k5_single_llm_vote_keeps_reasoning_intent_but_factual_lane() -> None:
    d = qr.route_question(DEV["task"]["ja"], gateway=_SyncLabelGateway("REASONING"))
    assert (str(d.intent), d.lane, d.via) == ("REASONING", qr.LANE_FACTUAL, "l2-llm")
    # intent REASONING ⇒ không kích hoạt data-fork (giữ đường generation + kiểm đầy đủ)
    assert d.intent != qr.LOOKUP


@pytest.mark.parametrize("question", _rows(("greeting", "identity")))
def test_k5_l1_chat_without_l2_confirmation_is_factual(monkeypatch, question: str) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    d = asyncio.run(qr.route_question_async(question, gateway=_AsyncDeadGateway()))
    assert d.lane == qr.LANE_FACTUAL and d.via != CONSENSUS_VIA, d.to_dict()


# --------------------------------------------------------------------------- K6
@pytest.mark.parametrize("question", _rows(("greeting", "identity")))
def test_k6_dual_consensus_reaches_chatbot_in_every_language(monkeypatch, question: str) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    gw = _AsyncJsonGateway("CHATBOT")
    d = qr.route_question(question, gateway=gw)
    assert d.lane == qr.LANE_CHATBOT and d.via == CONSENSUS_VIA, d.to_dict()
    assert gw.calls == 1


@pytest.mark.parametrize("l2_conf", [0.30, 0.60])
def test_k6_dual_chat_below_joint_threshold_stays_factual(monkeypatch, _hermetic, l2_conf: float) -> None:
    """Cả hai lineage cùng nhãn CHAT nhưng min(conf) dưới ngưỡng kép ⇒ KHÔNG CHATBOT + bài học."""
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    d = qr.route_question(DEV["greeting"]["ja"], gateway=_AsyncJsonGateway("CHATBOT", conf=l2_conf))
    assert d.lane == qr.LANE_FACTUAL and d.via != CONSENSUS_VIA, d.to_dict()
    recs = [r for r in _hermetic.records if r["error_type"] == "routing_l1_chat_unconfirmed"]
    assert len(recs) == 1, _hermetic.records


@pytest.mark.parametrize("question", _rows(("attack",)))
def test_k6_l1_chat_vote_cannot_lift_attack_out_of_l0_security(monkeypatch, question: str) -> None:
    """Tấn công có chữ ký mà L0 bắt được thì không phụ thuộc L1 (L0 an ninh đứng trước)."""
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    l0 = qr.classify_l0(question)
    d = qr.route_question(question, gateway=_AsyncJsonGateway("CHATBOT"))
    if l0 is not None:
        assert d.lane == qr.LANE_SECURITY, d.to_dict()


# --------------------------------------------------------------------------- K7
def test_k7_unconfirmed_l1_chat_leaves_hypothesis_lesson(monkeypatch, _hermetic) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    q = DEV["identity"]["vi"]
    d = asyncio.run(qr.route_question_async(q, gateway=_AsyncDeadGateway()))
    assert d.lane == qr.LANE_FACTUAL
    recs = [r for r in _hermetic.records if r["error_type"] == "routing_l1_chat_unconfirmed"]
    assert len(recs) == 1, _hermetic.records
    det = recs[0]["details"]
    assert det["epistemic_status"] == "HYPOTHESIS" and det["verified"] is False
    assert det["promotion"] == "never_automatic"
    assert det["l1"]["label"] == "CHAT" and det["l2"] == {"state": "dead"}
    assert det["decision"]["lane"] == qr.LANE_FACTUAL
    assert recs[0]["verdict"] == "UNVERIFIED" and recs[0]["domain"] == "routing"
    assert q in recs[0]["question"]


def test_k7_l2_disagreement_also_leaves_lesson(monkeypatch, _hermetic) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    d = qr.route_question(DEV["greeting"]["th"], gateway=_AsyncJsonGateway("FACTUAL"))
    assert d.via != CONSENSUS_VIA
    recs = [r for r in _hermetic.records if r["error_type"] == "routing_l1_chat_unconfirmed"]
    assert len(recs) == 1 and recs[0]["details"]["l2"]["label"] == "FACTUAL"


def test_k7_consensus_leaves_no_lesson(monkeypatch, _hermetic) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    d = qr.route_question(DEV["greeting"]["ar"], gateway=_AsyncJsonGateway("CHATBOT"))
    assert d.via == CONSENSUS_VIA and _hermetic.records == []


@pytest.mark.parametrize("l1_label,llm_label,expect_lesson", [
    ("FACT", "REASONING", True),
    ("REAS", "LOOKUP", True),
    ("FACT", "LOOKUP", False),
    ("REAS", "REASONING", False),
])
def test_k7_l1_vs_l2_router_divergence_lesson(monkeypatch, _hermetic, l1_label, llm_label, expect_lesson) -> None:
    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1(l1_label))
    d = qr.route_question(DEV["knowledge"]["hi"], gateway=_SyncLabelGateway(llm_label))
    assert d.lane == qr.LANE_FACTUAL and d.via == "l2-llm"
    recs = [r for r in _hermetic.records if r["error_type"] == "routing_l1_l2_router_divergence"]
    assert len(recs) == (1 if expect_lesson else 0), _hermetic.records
    if expect_lesson:
        assert recs[0]["details"]["epistemic_status"] == "HYPOTHESIS"
        assert recs[0]["details"]["decision"]["intent"] == llm_label


def test_k7_lesson_store_failure_never_changes_routing(monkeypatch) -> None:
    from scp.runtime.semantic_router import routing_lessons

    monkeypatch.setattr(lineage_embed, "classify", lambda text: _alive_l1("CHAT"))
    q = DEV["identity"]["ko"]
    baseline = asyncio.run(qr.route_question_async(q, gateway=_AsyncDeadGateway()))
    monkeypatch.setattr(routing_lessons, "STORE_FACTORY", lambda: _FakeStore(fail=True))
    monkeypatch.setattr(routing_lessons, "_seen", {})  # baseline đã ghi q (dedupe) ⇒ ép đi lại tới store
    broken = asyncio.run(qr.route_question_async(q, gateway=_AsyncDeadGateway()))
    assert baseline.to_dict() == broken.to_dict()
    # ghi lỗi ⇒ dấu dedupe được gỡ ⇒ lần ghi sau vẫn tới store (và vẫn False, không raise)
    assert routing_lessons.record_routing_lesson(routing_lessons.L1_CHAT_UNCONFIRMED, q) is False
    assert routing_lessons._seen == {}


def test_k7_lesson_rate_cap(monkeypatch, _hermetic) -> None:
    from scp.runtime.semantic_router import routing_lessons

    monkeypatch.setenv("SCP_ROUTING_LESSON_MAX_PER_MIN", "2")
    results = [routing_lessons.record_routing_lesson(routing_lessons.L1_CHAT_UNCONFIRMED, f"câu số {i} ok")
               for i in range(3)]
    assert results == [True, True, False] and len(_hermetic.records) == 2


def test_k7_same_question_lesson_is_not_duplicated(monkeypatch, _hermetic) -> None:
    """1 /ask gọi router nhiều lần ⇒ cùng (status, câu hỏi) chỉ ghi 1 lần/cửa sổ, không ăn trần."""
    from scp.runtime.semantic_router import routing_lessons

    monkeypatch.setenv("SCP_ROUTING_LESSON_MAX_PER_MIN", "2")
    rec = routing_lessons.record_routing_lesson
    st = routing_lessons.L1_CHAT_UNCONFIRMED
    assert [rec(st, "câu A"), rec(st, "câu A"), rec(st, "câu A"), rec(st, "câu B")] == [True, False, False, True]
    assert rec(routing_lessons.L1_L2_ROUTER_DIVERGENCE, "câu A") is False  # trần 2 đã dùng hết
    assert len(_hermetic.records) == 2


def test_k7_lesson_kill_switch(monkeypatch, _hermetic) -> None:
    from scp.runtime.semantic_router import routing_lessons

    monkeypatch.setenv("SCP_ROUTING_LESSONS", "0")
    assert routing_lessons.record_routing_lesson(routing_lessons.L1_CHAT_UNCONFIRMED, "câu thử nghiệm") is False
    assert _hermetic.records == []


def test_k7_lesson_redacts_secrets(monkeypatch, _hermetic) -> None:
    """Cùng hợp đồng redaction với math_lessons: giá trị secret RUNTIME, dạng key=value, email."""
    from scp.runtime.semantic_router import routing_lessons

    runtime_secret = "rt-admin-" + "Z9" * 20
    monkeypatch.setenv("SCP_ADMIN_KEY", runtime_secret)
    assert routing_lessons.record_routing_lesson(
        routing_lessons.L1_CHAT_UNCONFIRMED,
        f"admin {runtime_secret} and api_key=abc123secretvalue mail me at a.b@example.com")
    blob = json.dumps(_hermetic.records, ensure_ascii=False)
    for leaked in (runtime_secret, "abc123secretvalue", "a.b@example.com"):
        assert leaked not in blob, leaked


# --------------------------------------------------------------------------- K8
def _rule_tables(module) -> dict[str, tuple]:
    """Mọi biến module-level là bảng luật (tuple các cặp (pattern:str, tag:str))."""
    out = {}
    for name, val in vars(module).items():
        if (isinstance(val, tuple) and val and all(isinstance(x, tuple) and len(x) == 2
                                                  and all(isinstance(y, str) for y in x) for x in val)):
            out[name] = val
    return out


_ESCAPES = re.compile(r"\\[A-Za-z]")


def _letters(pattern: str) -> list[str]:
    stripped = _ESCAPES.sub("", pattern)
    return [ch for ch in stripped if unicodedata.category(ch).startswith("L")]


def test_k8_non_security_routing_tables_contain_no_language_words() -> None:
    import scp.runtime.semantic_router as sr_pkg

    modules = [router_rules, qr]
    for path in sorted(Path(sr_pkg.__file__).parent.glob("*.py")):
        # structural.py: fast-path CHƯA nối vào đường định tuyến runtime (khoá bởi test
        # bên dưới); bảng _OPERATOR_TOKENS VI+EN của nó là hở đã báo trong done file.
        if path.stem not in ("__init__", "structural"):
            modules.append(__import__(f"scp.runtime.semantic_router.{path.stem}", fromlist=["x"]))
    offenders = {}
    for mod in modules:
        for name, table in _rule_tables(mod).items():
            if mod is router_rules and name == "_SECURITY_RULES":
                continue  # chữ ký tấn công: chỉ đẩy lane CHẶT HƠN (xem done file: còn hở đa ngôn ngữ)
            if mod is qr and name == "_SECURITY_RULES":
                continue  # re-export cùng object
            bad = [(tag, "".join(_letters(p))[:40]) for p, tag in table if _letters(p)]
            if bad:
                offenders[f"{mod.__name__}.{name}"] = bad
    assert offenders == {}, offenders


def test_k8_unscanned_structural_word_table_is_not_on_runtime_routing_path() -> None:
    """Ranh giới của miễn trừ ở test trên: structural.py không được module định tuyến nào import."""
    for rel in ("scp/runtime/router_rules.py", "scp/runtime/question_router.py",
                "scp/runtime/semantic_router/consensus.py", "scp/runtime/semantic_router/arbitration.py",
                "scp/runtime/semantic_router/lineage_embed.py", "scp/runtime/semantic_router/lineage_slm.py",
                "scp/runtime/semantic_router/routing_lessons.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "structural_fast_path" not in src and "semantic_router.structural" not in src, rel


def test_k8_classify_l0_never_constructs_chatbot_lane() -> None:
    tree = ast.parse((REPO / "scp/runtime/router_rules.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "classify_l0")
    lanes = [kw.value.id for call in ast.walk(fn) if isinstance(call, ast.Call)
             for kw in call.keywords if kw.arg == "lane" and isinstance(kw.value, ast.Name)]
    assert lanes and "LANE_CHATBOT" not in lanes, lanes


def test_k8_no_fixture_question_is_embedded_in_routing_code() -> None:
    sources = [REPO / "scp/runtime/router_rules.py", REPO / "scp/runtime/question_router.py",
               *sorted((REPO / "scp/runtime/semantic_router").glob("*.py"))]
    blob = "\n".join(p.read_text(encoding="utf-8").casefold() for p in sources)
    leaked = []
    for data in (DEV, HOLDOUT):
        for group, langs in data.items():
            for lang, q in langs.items():
                core = q.strip(" ?？!！.。؟").casefold()
                if len(core) >= 6 and core in blob:
                    leaked.append((group, lang, q))
    assert leaked == []
    assert "router_multilingual" not in blob  # code không đọc fixture test
