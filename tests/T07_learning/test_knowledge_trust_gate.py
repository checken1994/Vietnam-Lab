"""HC-20 / S4-F-01 — Knowledge Trust Gate tại DomainKnowledge.store().

Governed by SCP DNA:
- DNA #6 (Gốc tin cậy / One trust root per source): verdict trust lấy từ
  trust_hierarchy + source_reputation + source_watchlist (API sẵn có), không tự chế tiêu chí riêng.
- DNA #11 (Anti-Placebo Gate Pattern): negative test bắt buộc — gate tắt phải
  trả về hành vi cũ; nguồn watchlist phải bị chặn THẬT (không phải mock PASS).
- Reality over Model: nguồn chưa biết ≠ nguồn xấu — fail-open có kiểm soát
  (unassessed), chỉ nguồn có bằng chứng xấu rõ mới bị chặn fail-closed.

Behavior change đã duyệt bởi owner:
- store() tính trust verdict của source TRƯỚC INSERT (Rule 0).
- Nguồn watchlist/blocked → REJECT (return None + WARNING, trùng contract Rule 1/2).
- Nguồn trust cao → vào kho, trust_status="trusted".
- Nguồn lạ → vào kho kèm trust_status="unassessed".
- Schema knowledge_records KHÔNG đổi — trust gắn vào record trả về + structured log.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from scp.knowledge import source_reputation as source_reputation_module
from scp.knowledge.domain_knowledge import DomainKnowledge
from scp.knowledge.source_reputation import ReputationStore


def _make_dk(tmp_path, trust_gate_enabled: bool = True) -> DomainKnowledge:
    """DomainKnowledge với tmp_path DB (pattern test dedup) + tmp reputation DB."""
    return DomainKnowledge(
        data_dir=str(tmp_path / "kb"),
        sqlite_path=str(tmp_path / "knowledge.sqlite3"),
        reputation_db_path=str(tmp_path / "reputation.sqlite"),
        trust_gate_enabled=trust_gate_enabled,
    )


def _count_rows(db_path, question: str) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge_records WHERE question = ?", (question,))
        return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Positive: nguồn trust cao → vào kho kèm trust_status="trusted"
# ---------------------------------------------------------------------------
def test_trusted_source_stored_with_trusted_status(tmp_path):
    """'wikipedia' là well-known trusted source (G5-FIX allowlist) → phải vào kho."""
    dk = _make_dk(tmp_path)
    q = "Thủ đô của Nhật Bản là gì?"

    rec = dk.store(question=q, answer="Tokyo", domain="geography", confidence=0.95, source="wikipedia")

    assert rec is not None, "Trusted source record must be stored"
    assert getattr(rec, "trust_status", None) == "trusted", (
        "Stored record must carry trust_status='trusted' on the return value"
    )
    trust_meta = getattr(rec, "trust", None)
    assert isinstance(trust_meta, dict), "Record must carry structured trust verdict"
    assert trust_meta.get("action") == "commit"
    assert trust_meta.get("tier") == 2, "get_tier('wikipedia') must be CONSENSUS (tier 2) via trust_hierarchy"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 1, "Trusted record must reach knowledge_records"


def test_active_reputation_source_stored_with_trusted_status(tmp_path):
    """Source có reputation status='active' trong ReputationStore → trusted."""
    dk = _make_dk(tmp_path)
    rep = ReputationStore(db_path=str(tmp_path / "reputation.sqlite"))
    rep.on_fact_committed("verified-source.example", tier=1)  # tạo bản ghi active
    assert rep.get("verified-source.example").status == "active"

    q = "Ai viết 'Hoàng Lê Nhất Thống Chí'?"
    rec = dk.store(
        question=q,
        answer="Ngô gia văn phái",
        domain="history",
        confidence=0.9,
        source="verified-source.example",
    )

    assert rec is not None
    assert getattr(rec, "trust_status", None) == "trusted"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 1


# ---------------------------------------------------------------------------
# Negative (DNA #11, anti-placebo): nguồn xấu rõ → REJECT fail-closed THẬT
# ---------------------------------------------------------------------------
def test_blocked_source_rejected_fail_closed(tmp_path):
    """Source bị block vĩnh viễn (reputation < 0.1 / force_block) → store() phải từ chối."""
    dk = _make_dk(tmp_path)
    rep = ReputationStore(db_path=str(tmp_path / "reputation.sqlite"))
    rep.force_block("poison-source.example", reason="detected_poisoning")

    q = "1 + 1 bằng mấy?"
    rec = dk.store(question=q, answer="2", domain="math", confidence=0.99, source="poison-source.example")

    assert rec is None, "Blocked source record must be REJECTED"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 0, "Blocked source must NOT reach knowledge_records"


def test_watchlist_source_rejected_fail_closed(tmp_path):
    """Source trong watchlist (status='watchlist', xấu rõ) → store() phải từ chối."""
    dk = _make_dk(tmp_path)
    rep = ReputationStore(db_path=str(tmp_path / "reputation.sqlite"))
    # 15 invalidations: 1.0 * 0.9^15 ≈ 0.206 < 0.3 (watchlist) nhưng > 0.1 (chưa block)
    for _ in range(15):
        rep.on_fact_committed("flaky-source.example", tier=2)
        rep.on_fact_invalidated("flaky-source.example")
    assert rep.get("flaky-source.example").status == "watchlist", "Fixture: source must be in watchlist"

    q = "Thành phố nào lớn nhất Việt Nam?"
    rec = dk.store(
        question=q,
        answer="Thành phố Hồ Chí Minh",
        domain="geography",
        confidence=0.9,
        source="flaky-source.example",
    )

    assert rec is None, "Watchlist source record must be REJECTED"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 0, "Watchlist source must NOT reach knowledge_records"


def test_watchlist_rejection_emits_structured_warning(tmp_path, caplog):
    """Rejection phải để lại structured WARNING log (ledger) — contract trùng Rule 1/2."""
    dk = _make_dk(tmp_path)
    rep = ReputationStore(db_path=str(tmp_path / "reputation.sqlite"))
    rep.force_block("poison-source.example", reason="detected_poisoning")

    with caplog.at_level(logging.WARNING, logger="scp.knowledge.autonomous"):
        rec = dk.store(
            question="Câu hỏi từ nguồn bị block?",
            answer="Câu trả lời độc hại",
            domain="general",
            confidence=0.9,
            source="poison-source.example",
        )

    assert rec is None
    reject_logs = [r for r in caplog.records if "Trust gate REJECT" in r.message]
    assert reject_logs, "Trust gate rejection must emit a structured WARNING log"
    assert any("poison-source.example" in r.getMessage() for r in reject_logs)
    assert any("blocked" in r.getMessage() for r in reject_logs)


# ---------------------------------------------------------------------------
# Fail-open có kiểm soát: nguồn lạ ≠ nguồn xấu
# ---------------------------------------------------------------------------
def test_unknown_source_stored_with_unassessed_status(tmp_path):
    """Nguồn chưa biết KHÔNG được chặn — vào kho kèm trust_status='unassessed'."""
    dk = _make_dk(tmp_path)
    q = "Nguồn lạ chưa có reputation thì sao?"

    rec = dk.store(
        question=q,
        answer="Được ghi nhận với trạng thái unassessed",
        domain="general",
        confidence=0.9,
        source="brand-new-unknown-source",
    )

    assert rec is not None, "Unknown source must NOT be blocked (nguồn chưa biết ≠ nguồn xấu)"
    assert getattr(rec, "trust_status", None) == "unassessed"
    trust_meta = getattr(rec, "trust", None)
    assert isinstance(trust_meta, dict) and trust_meta.get("action") == "commit"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 1


def test_trust_backend_failure_fails_open_with_unassessed(tmp_path, monkeypatch):
    """Reputation backend hỏng → fail-open: vẫn ghi, trust_status='unassessed' + WARNING."""

    def _boom(*args, **kwargs):
        raise RuntimeError("reputation backend unavailable")

    monkeypatch.setattr(source_reputation_module, "ReputationStore", _boom)
    dk = _make_dk(tmp_path)
    q = "Backend trust hỏng có chặn ghi tri thức không?"

    rec = dk.store(
        question=q,
        answer="Không — fail-open với unassessed",
        domain="general",
        confidence=0.9,
        source="any-source.example",
    )

    assert rec is not None, "Trust backend failure must NOT block knowledge ingestion"
    assert getattr(rec, "trust_status", None) == "unassessed"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 1


# ---------------------------------------------------------------------------
# Anti-placebo: tắt gate (flag) → hành vi cũ
# ---------------------------------------------------------------------------
def test_trust_gate_disabled_restores_old_behavior(tmp_path):
    """DNA #11: tắt trust_gate_enabled → nguồn watchlist/blocked vẫn vào kho như trước."""
    dk = _make_dk(tmp_path, trust_gate_enabled=False)
    rep = ReputationStore(db_path=str(tmp_path / "reputation.sqlite"))
    rep.force_block("poison-source.example", reason="detected_poisoning")

    q = "Gate tắt thì hành vi cũ thế nào?"
    rec = dk.store(
        question=q,
        answer="Vào kho như trước khi có trust gate",
        domain="general",
        confidence=0.9,
        source="poison-source.example",
    )

    assert rec is not None, "Gate disabled → old behavior: no trust filtering"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 1


# ---------------------------------------------------------------------------
# Gate không phá các rule hiện có (regression của Rule 1/2 trong cùng flow)
# ---------------------------------------------------------------------------
def test_trust_gate_does_not_break_approximate_rejection(tmp_path):
    """Trust pass nhưng answer approximate → vẫn bị Rule 1 reject fail-closed."""
    dk = _make_dk(tmp_path)
    q = "Vận tốc ánh sáng trong chân không là bao nhiêu?"

    rec = dk.store(
        question=q,
        answer="Khoảng 300.000 km/s",
        domain="physics",
        confidence=0.99,
        source="wikipedia",
    )

    assert rec is None, "Approximate answer must still be rejected regardless of trusted source"
    assert _count_rows(tmp_path / "knowledge.sqlite3", q) == 0
