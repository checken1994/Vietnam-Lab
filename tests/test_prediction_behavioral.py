import sqlite3
from scp.core import db_manager
from scp.prediction.predictive import init_predictions_db

def test_prediction_db_schema_and_constraints(tmp_path, monkeypatch):
    db_file = tmp_path / "v14.db"
    monkeypatch.setattr(db_manager, "DB_PATH", str(db_file))
    init_predictions_db()
    with sqlite3.connect(str(db_file)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "predictions" in tables
        cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()]
        assert "id" in cols
        assert "status" in cols
        assert "check_date" in cols
