"""
Lane knowledge-experts wire-or-kill (G-1): registry domain id -> domain expert.

Đỏ-trước trên main 9e2837a2 (khóa bị đè history→Holiday, geography→City,
biology→AnimalFacts do ``os.listdir`` + last-wins; bảng bind tường minh chưa có).
Các test kiểm CƠ CHẾ (judge thực sự triệu hồi class nào), không kiểm tên/huân chương.
Không có mẫu câu hỏi nào ở đây quyết định định tuyến: ``classify_top1`` bị cố định
thành id registry để cô lập đúng tầng bind (tầng của lane này).
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import types
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPERTS_DIR = ROOT / "scp" / "runtime" / "experts"
REGISTRY_FILE = ROOT / "scp" / "runtime" / "expert_registry.py"

# 7 expert từng mồ côi (khai khóa không có trong domain_registry.DOMAINS).
ORPHANS = ("law:Law", "economics:Economics", "engineering:Engineering", "literature:Literature",
           "management:Management", "news:News", "philosophy:Philosophy")


def _fresh_judge_experts() -> dict:
    """Chạy đúng property RealityJudge.domain_experts trên một instance trống."""
    from scp.runtime.judge import RealityJudge

    holder = types.SimpleNamespace()
    return RealityJudge.domain_experts.fget(holder)


def _cls_path(obj) -> str:
    cls = obj if isinstance(obj, type) else type(obj)
    return f"{cls.__module__.rsplit('.', 1)[-1]}:{cls.__name__}"


def _discover_expert_classes() -> set[type]:
    from scp.runtime.slm_base import BaseSLM

    found: set[type] = set()
    for fn in sorted(os.listdir(EXPERTS_DIR)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        mod = importlib.import_module(f"scp.runtime.experts.{fn[:-3]}")
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, BaseSLM) and obj is not BaseSLM and obj.__module__ == mod.__name__:
                found.add(obj)
    return found


# --------------------------------------------------------------------------- static

def test_bindings_cover_every_registry_domain_exactly():
    from scp.data_sources.domain_registry import DOMAINS
    from scp.runtime.expert_registry import DOMAIN_EXPERT_BINDINGS

    assert set(DOMAIN_EXPERT_BINDINGS) == set(DOMAINS), (
        "mỗi id registry phải có binding tường minh (class hoặc None=no-expert): "
        f"thiếu={sorted(set(DOMAINS) - set(DOMAIN_EXPERT_BINDINGS))} "
        f"thừa={sorted(set(DOMAIN_EXPERT_BINDINGS) - set(DOMAINS))}"
    )


def test_binding_literals_have_no_duplicate_or_overwritten_keys():
    tree = ast.parse(REGISTRY_FILE.read_text(encoding="utf-8"))
    checked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id in ("DOMAIN_EXPERT_BINDINGS", "UNBOUND_EXPERTS"):
            assert isinstance(node.value, ast.Dict)
            keys = [k.value for k in node.value.keys]
            dups = sorted({k for k in keys if keys.count(k) > 1})
            assert not dups, f"{node.target.id}: khóa trùng (dict literal sẽ đè im lặng): {dups}"
            checked.add(node.target.id)
    assert checked == {"DOMAIN_EXPERT_BINDINGS", "UNBOUND_EXPERTS"}


def test_every_expert_class_is_bound_or_declared_unbound_exactly_once():
    from scp.runtime.expert_registry import (
        DOMAIN_EXPERT_BINDINGS,
        UNBOUND_EXPERTS,
        resolve_target,
    )

    bound = {resolve_target(t) for t in DOMAIN_EXPERT_BINDINGS.values() if t is not None}
    unbound = {resolve_target(t) for t in UNBOUND_EXPERTS}
    discovered = _discover_expert_classes()
    assert not (bound & unbound), f"vừa bind vừa unbound: {sorted(map(_cls_path, bound & unbound))}"
    missing = discovered - bound - unbound
    assert not missing, f"expert mồ côi chưa có quyết định wire/kill: {sorted(map(_cls_path, missing))}"
    stale = (bound | unbound) - discovered
    assert not stale, f"bảng trỏ tới class không còn trong experts/: {sorted(map(_cls_path, stale))}"


def test_seven_orphans_each_have_an_explicit_decision():
    from scp.runtime.expert_registry import DOMAIN_EXPERT_BINDINGS, UNBOUND_EXPERTS

    bound_targets = {t for t in DOMAIN_EXPERT_BINDINGS.values() if t}
    for target in ORPHANS:
        assert (target in bound_targets) != (target in UNBOUND_EXPERTS), target
    assert DOMAIN_EXPERT_BINDINGS["legal"] == "law:Law"
    assert DOMAIN_EXPERT_BINDINGS["technology"] == "engineering:Engineering"


# ------------------------------------------------------------- runtime resolution

def _oracle_answer_tables(target: str) -> list[str]:
    """Oracle ĐỘC LẬP với product (không gọi expert_registry.embedded_lookup_tables):
    dict không rỗng khóa chuỗi -> văn bản/dict, ở class (MRO) hoặc instance mới tạo."""
    module, _, name = target.partition(":")
    cls = getattr(importlib.import_module(f"scp.runtime.experts.{module}"), name)
    hits = []
    for scope, ns in [(k.__name__, vars(k)) for k in cls.__mro__ if k is not object] + [("instance", vars(cls()))]:
        for attr, val in ns.items():
            if attr.startswith("__") or not isinstance(val, dict) or not val:
                continue
            if any(isinstance(k, str) for k in val) and any(isinstance(v, (str, dict)) for v in val.values()):
                hits.append(f"{scope}.{attr}")
    return hits


def _expected_path(domain_id: str, target: str) -> str | None:
    """Biconditional: id resolve về target IFF target không mang bảng đáp án nhúng."""
    return None if _oracle_answer_tables(target) else target


def test_overwritten_keys_resolve_to_their_own_expert():
    from scp.runtime.expert_registry import DOMAIN_EXPERT_BINDINGS

    assert DOMAIN_EXPERT_BINDINGS["history"] == "humanities:History"
    assert DOMAIN_EXPERT_BINDINGS["geography"] == "humanities:Geography"
    assert DOMAIN_EXPERT_BINDINGS["biology"] is None
    experts = _fresh_judge_experts()
    got = {k: (_cls_path(experts[k]) if k in experts else None) for k in ("history", "geography", "biology")}
    assert got == {
        "history": _expected_path("history", "humanities:History"),
        "geography": _expected_path("geography", "humanities:Geography"),
        # biology: không có chuyên gia sinh học ⇒ không được rơi vào AnimalFacts (catfact ngẫu nhiên)
        "biology": None,
    }
    # Holiday/City chỉ được phép ở id của chính chúng; AnimalFacts không ở đâu cả
    for k, v in experts.items():
        if _cls_path(v) in ("lifestyle:Holiday", "lifestyle:City", "lifestyle:AnimalFacts"):
            assert (k, _cls_path(v)) in (("holiday", "lifestyle:Holiday"), ("city", "lifestyle:City")), (k, v)


def test_history_expert_local_year_table_is_refused_while_present():
    """History mang bảng năm→sự kiện viết cứng (self._local, 143 mục, tiếng Việt) —
    cùng lớp lỗi H12. Bind đúng history→History KHÔNG được kích hoạt bảng đó."""
    from scp.runtime.expert_registry import build_domain_experts
    from scp.runtime.experts.humanities import History

    experts, refusals = build_domain_experts()
    local = getattr(History(), "_local", None)
    if local and any(isinstance(v, str) for v in local.values()):
        assert "instance._local" in _oracle_answer_tables("humanities:History")
        assert "History()._local" in refusals["history"]
        assert "history" not in experts
        assert refusals["history"].startswith("embedded_answer_table:humanities:History:")
    else:
        assert _cls_path(experts["history"]) == "humanities:History"


@pytest.mark.parametrize("order", ["sorted", "reversed"])
def test_resolution_does_not_depend_on_directory_listing_order(monkeypatch, order):
    real_listdir = os.listdir

    def ordered(path=None):
        names = sorted(real_listdir(path) if path is not None else real_listdir())
        return names if order == "sorted" else names[::-1]

    monkeypatch.setattr(os, "listdir", ordered)
    experts = _fresh_judge_experts()
    picked = {k: (_cls_path(experts[k]) if k in experts else None)
              for k in ("history", "geography", "entertainment", "general", "religion")}
    assert picked == {
        "history": _expected_path("history", "humanities:History"),
        "geography": _expected_path("geography", "humanities:Geography"),
        "entertainment": "misc:Entertainment",
        "general": "lifestyle:General",
        "religion": "religion:Religion",
    }


def test_every_judge_key_is_a_registry_domain():
    from scp.data_sources.domain_registry import DOMAINS

    experts = _fresh_judge_experts()
    assert set(experts) <= set(DOMAINS), f"khóa expert không định tuyến được: {sorted(set(experts) - set(DOMAINS))}"


def test_orphan_classes_never_loaded_under_dead_keys():
    experts = _fresh_judge_experts()
    by_cls: dict[str, list[str]] = {}
    for k, v in experts.items():
        by_cls.setdefault(_cls_path(v), []).append(k)
    for target in ("economics:Economics", "news:News", "literature:Literature",
                   "management:Management", "philosophy:Philosophy"):
        assert target not in by_cls, f"{target} vẫn được nạp dưới {by_cls.get(target)}"
    assert by_cls.get("law:Law", ["legal"]) == ["legal"]
    assert by_cls.get("engineering:Engineering", ["technology"]) == ["technology"]


@pytest.mark.parametrize("domain_id,target", [("legal", "law:Law"), ("technology", "engineering:Engineering")])
def test_wired_orphan_binds_iff_it_carries_no_embedded_answer_table(domain_id, target):
    from scp.runtime.expert_registry import build_domain_experts

    experts, refusals = build_domain_experts()
    if _oracle_answer_tables(target):
        assert domain_id not in experts
        assert refusals[domain_id].startswith(f"embedded_answer_table:{target}:")
    else:
        assert _cls_path(experts[domain_id]) == target
        assert domain_id not in refusals


# ------------------------------------------------------------------ fail-closed guard

from scp.runtime.slm_base import BaseSLM as _Base
from scp.runtime.slm_base import SLMResponse as _Resp


class _CleanProbeExpert(_Base):
    def __init__(self, config=None):
        super().__init__(name="CleanProbe", domain="probe", config=config)

    def predict(self, question):
        return _Resp(question=question, answer="", confidence=0.0, domain="probe",
                     reasoning="", evidence={}, slm_name=self.name, processing_time=0.0)

    def get_confidence(self, question, answer):
        return 0.0


class _TableProbeExpert(_CleanProbeExpert):
    _ANSWERS: ClassVar[dict[str, str]] = {"what is x": "x is y"}
    init_calls: ClassVar[list[int]] = []

    def __init__(self, config=None):
        type(self).init_calls.append(1)  # constructor có thể có side-effect (mạng/datasource)
        super().__init__(config)


class _TableInInitExpert(_CleanProbeExpert):
    def __init__(self, config=None):
        super().__init__(config)
        self._local = {"1999": "an event"}
        self.counters = {"hits": 0}


class _BrokenInitExpert(_CleanProbeExpert):
    def __init__(self, config=None):
        raise RuntimeError("boom")


def test_guard_refuses_embedded_table_broken_init_and_non_expert():
    from scp.runtime.expert_registry import build_domain_experts

    experts, refusals = build_domain_experts({
        "clean": f"{__name__}:_CleanProbeExpert",
        "table": f"{__name__}:_TableProbeExpert",
        "inittable": f"{__name__}:_TableInInitExpert",
        "broken": f"{__name__}:_BrokenInitExpert",
        "notexpert": "builtins:dict",
        "missing": "no_such_expert_module:Nope",
        "none": None,
    })
    assert set(experts) == {"clean"}
    assert refusals["table"].startswith("embedded_answer_table:")
    assert "_ANSWERS" in refusals["table"]
    # bảng cấp class bị chặn TRƯỚC khi khởi tạo — không chạy constructor của expert bị từ chối
    assert _TableProbeExpert.init_calls == []
    assert refusals["inittable"].startswith("embedded_answer_table:")
    assert "_TableInInitExpert()._local" in refusals["inittable"]
    assert "counters" not in refusals["inittable"]  # bộ đếm số không phải bảng đáp án
    assert refusals["broken"].startswith("init_failed:")
    assert refusals["notexpert"].startswith("not_a_domain_expert:")
    assert refusals["missing"].startswith("unresolvable:")
    assert "none" not in refusals


# ------------------------------------------------------------ judge summon (e2e in-process)

@pytest.mark.parametrize("domain_id,expected", [
    ("history", "History"),  # chỉ khi History không còn bảng nhúng (xem _expected_path)
    ("geography", "Geography"),
    ("biology", None),
])
def test_judge_summons_the_bound_expert_for_registry_domain(monkeypatch, domain_id, expected):
    monkeypatch.setenv("SCP_MULTI_LLM_CROSSCHECK", "0")
    monkeypatch.setenv("SCP_EGRESS_MODE", "deny")
    import scp.data_sources.domain_classifier as dc
    from scp.runtime.experts.humanities import Geography, History
    from scp.runtime.experts.lifestyle import AnimalFacts, City, Holiday
    from scp.runtime.judge import RealityJudge

    calls: list[str] = []
    for cls in (History, Geography, Holiday, City, AnimalFacts):
        orig = cls.__dict__["predict"]

        def wrapped(self, question, _orig=orig):
            calls.append(type(self).__name__)
            return _orig(self, question)

        monkeypatch.setattr(cls, "predict", wrapped)
    monkeypatch.setattr(dc, "classify_top1", lambda q: domain_id)
    RealityJudge().judge("Question routed to a registry domain?", "A non-empty candidate answer.")
    if expected == "History" and _expected_path("history", "humanities:History") is None:
        expected = None  # guard fail-closed: History mang bảng năm→sự kiện viết cứng
    assert calls == ([expected] if expected else [])
