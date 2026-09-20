import pytest
from scp.knowledge.knowledge_control_db import KnowledgeControlDB
from scp.knowledge.contradiction_authority import ContradictionAuthority, ContradictionMateriality, ContradictionRecord

@pytest.fixture
def auth(tmp_path):
    db = KnowledgeControlDB(tmp_path / "db.sqlite")
    return ContradictionAuthority(db)

def test_register_high_critical_materiality_triggers_under_review(auth):
    record = ContradictionRecord(
        claim_a="c1",
        claim_b="c2",
        evidence_a=["e1"],
        evidence_b=["e2"],
        relation="DIRECT_CONTRADICTION",
        materiality=ContradictionMateriality.HIGH
    )
    auth.register(record)
    
    import sqlite3
    with sqlite3.connect(auth.db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT knowledge_id, to_status FROM knowledge_status_events WHERE to_status='UNDER_REVIEW'").fetchall()
        assert len(rows) == 2
        statuses = {r["knowledge_id"]: r["to_status"] for r in rows}
        assert "c1" in statuses
        assert "c2" in statuses
