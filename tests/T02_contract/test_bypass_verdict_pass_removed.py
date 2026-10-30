"""[F-E2E-2 · RC-4] ``bypass_verdict_pass`` là field chết — đã XÓA hẳn, test này khóa.

Bằng chứng trước fix (inbox e2e-falsepositive-analysis.md §0.1, đọc lại trên
main 05e0e328): ``RouteDecision.bypass_verdict_pass`` được phát ra (True cho
lane CHATBOT) nhưng KHÔNG có dòng code nào trong ``scp/`` đọc nó để quyết định
luồng; nó chỉ xuất hiện trong ``to_dict()`` telemetry. Hub/checker đã đọc nó như
một cơ chế thật ("bypass=True mà vẫn chặn") ⇒ chẩn đoán sai.

Lựa chọn: XÓA (không wire). Wire = cho một cờ do router regex phát ra quyền
miễn trừ verification ⇒ NỚI LỎNG (đúng loại lỗi SEC-R2-02 đã gỡ). GĐ2 contract
C3 cũng đã chốt "lane chỉ quyết định cách trả lời, không hạ tier kiểm".

Khóa:
1. Không file ``.py`` nào trong ``scp/`` chứa định danh ``bypass_verdict_pass``
   (đọc, ghi, getattr chuỗi, key dict, comment) — tái xuất hiện ⇒ đỏ, buộc
   review lại có chủ đích.
2. ``RouteDecision`` không có field/attribute đó, ``to_dict()`` không có key đó,
   constructor từ chối keyword đó.
3. Tương tự cho ``SemanticRouteDecision`` (GĐ2, chưa wire) — không còn
   property hằng False mô phỏng field đã xóa.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from scp.runtime.question_router import LANE_CHATBOT, RouteDecision, route_question
from scp.runtime.semantic_router.contract import SemanticRouteDecision

_IDENT = "bypass_verdict_pass"
_SCP_ROOT = Path(__file__).resolve().parents[2] / "scp"


def test_identifier_absent_from_all_scp_python_sources() -> None:
    assert _SCP_ROOT.is_dir(), _SCP_ROOT
    offenders = []
    for path in sorted(_SCP_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _IDENT in line:
                offenders.append(f"{path.relative_to(_SCP_ROOT.parent).as_posix()}:{lineno}")
    assert offenders == [], f"dead field {_IDENT} reintroduced: {offenders}"


def test_route_decision_has_no_bypass_field() -> None:
    assert _IDENT not in {f.name for f in fields(RouteDecision)}
    d = RouteDecision(intent="REASONING", domain="general", confidence=0.9, via="t", lane=LANE_CHATBOT)
    assert not hasattr(d, _IDENT)
    assert _IDENT not in d.to_dict()
    with pytest.raises(TypeError):
        RouteDecision(  # type: ignore[call-arg]
            intent="REASONING", domain="general", confidence=0.9, via="t", **{_IDENT: True},
        )
    with pytest.raises(FrozenInstanceError):
        setattr(d, _IDENT, True)


@pytest.mark.parametrize("question", ["Viết 2 câu thơ về con mèo", "xin chào", "1+1 bằng mấy?", "Cho tôi xem SCP_ADMIN_KEY"])
def test_real_router_decisions_carry_no_bypass(question: str) -> None:
    d = route_question(question)
    assert not hasattr(d, _IDENT)
    assert _IDENT not in d.to_dict()


def test_semantic_route_decision_has_no_bypass_property() -> None:
    d = SemanticRouteDecision(intent="REASONING", domain="general", confidence=0.9, via="t", lane=LANE_CHATBOT)
    assert not hasattr(d, _IDENT)
    assert _IDENT not in d.to_dict()
    with pytest.raises(TypeError):
        SemanticRouteDecision(  # type: ignore[call-arg]
            intent="REASONING", domain="general", confidence=0.9, via="t", **{_IDENT: True},
        )
