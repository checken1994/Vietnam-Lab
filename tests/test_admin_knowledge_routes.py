"""
Integration & Security Tests for Admin Knowledge & Sources Routes.
==================================================================
Kiểm chứng toàn diện scp/api/admin_knowledge_routes.py:
  1. Bảo vệ xác thực verify_admin:
     - 401 Unauthorized khi thiếu header Authorization (Fail-Closed).
     - 401 Unauthorized khi token sai.
  2. GET /api/scp/v3/knowledge/review:
     - Trả về danh sách file cách ly (quarantine).
     - Trả về danh sách câu hỏi / tri thức vi phạm kháng thể từ LearningDB.
  3. POST /api/scp/v3/knowledge/review/action:
     - 400 Bad Request khi action không hợp lệ hoặc thiếu claim.
     - APPROVE: Cập nhật status thành APPROVED trong LearningDB, di chuyển file sang processed/.
     - REJECT: Cập nhật status thành REJECTED trong LearningDB.
  4. GET /api/scp/v3/config/sources:
     - Liệt kê đủ 17 nguồn dữ liệu thương mại.
     - Che giấu API key qua mask_secret (Zero Secret Leak).
     - Báo trạng thái Enabled / Disabled chính xác theo key.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scp.api.admin_knowledge_routes import (
    router as admin_knowledge_router,
)
from scp.data_sources.config_loader import (
    DataSourceConfigLoader,
    reset_default_loader,
    set_default_loader,
)
from scp.knowledge.learning_db import LearningDB


@pytest.fixture
def api_test_env(tmp_path, monkeypatch):
    # Thiết lập biến môi trường cho auth
    test_token = "admin_super_secret_token_123"
    monkeypatch.setenv("SCP_AUTH_TOKEN_SECRET", test_token)

    # Thư mục cách ly và db test
    quarantine_dir = tmp_path / "quarantine"
    processed_dir = tmp_path / "processed"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "learning.sqlite"
    learning_db = LearningDB(db_path)

    monkeypatch.setenv("SCP_QUARANTINE_DIR", str(quarantine_dir))
    monkeypatch.setenv("SCP_PROCESSED_DIR", str(processed_dir))
    monkeypatch.setenv("SCP_LEARNING_DB_PATH", str(db_path))

    # Tạo app FastAPI riêng để test cách ly
    app = FastAPI()
    app.include_router(admin_knowledge_router)
    client = TestClient(app)

    yield {
        "client": client,
        "token": test_token,
        "headers": {"Authorization": f"Bearer {test_token}"},
        "quarantine_dir": quarantine_dir,
        "processed_dir": processed_dir,
        "db_path": db_path,
        "learning_db": learning_db,
    }

    reset_default_loader()


def test_auth_enforcement_fail_closed(api_test_env):
    """Mọi endpoint quản trị đều phải từ chối khi không có quyền."""
    client = api_test_env["client"]

    # Không có header Authorization -> 401
    resp1 = client.get("/api/scp/v3/knowledge/review")
    assert resp1.status_code == 401

    resp2 = client.post("/api/scp/v3/knowledge/review/action", json={"claim": "c1", "action": "APPROVE"})
    assert resp2.status_code == 401

    resp3 = client.get("/api/scp/v3/config/sources")
    assert resp3.status_code == 401

    # Token sai -> 401
    bad_headers = {"Authorization": "Bearer wrong_token_xyz"}
    resp_bad = client.get("/api/scp/v3/config/sources", headers=bad_headers)
    assert resp_bad.status_code == 401


def test_get_knowledge_review(api_test_env):
    """GET /api/scp/v3/knowledge/review trả về tệp cách ly và vi phạm kháng thể."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]
    quarantine_dir = api_test_env["quarantine_dir"]
    learning_db = api_test_env["learning_db"]

    # 1. Tạo tệp trong quarantine
    q_file = quarantine_dir / "quarantined_claim.json"
    q_file.write_text(json.dumps({"claim": "Trái Đất hình vuông"}), encoding="utf-8")

    # 2. Ghi nhận open_question vi phạm kháng thể vào SQLite
    learning_db.execute_insert("open_questions", {
        "question_id": "oq_violation_01",
        "title": "Kháng thể cảnh báo: GeoAntibody trong domain 'geography'",
        "question": "Tri thức bị từ chối do mâu thuẫn hình học Trái Đất",
        "scope_json": "{}",
        "trigger": "CONTRADICTION",
        "related_claim_refs_json": "[]",
        "related_knowledge_refs_json": "[]",
        "known_evidence_refs_json": '["GeoAntibody"]',
        "needed_observations_json": "[]",
        "needed_capabilities_json": "[]",
        "status": "OPEN",
        "created_at": "2026-10-08T00:00:00Z",
    })

    resp = client.get("/api/scp/v3/knowledge/review", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "success"
    assert data["total_quarantined"] >= 1
    assert any(f["filename"] == "quarantined_claim.json" for f in data["quarantined_files"])

    assert data["total_open_questions"] >= 1
    assert data["total_antibody_violations"] >= 1
    assert any(q["question_id"] == "oq_violation_01" for q in data["antibody_violations"])


def test_review_action_validation_fail_closed(api_test_env):
    """POST review action từ chối action không hợp lệ."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]

    # Action không nằm trong APPROVE | REJECT
    resp = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "c1", "action": "INVALID_ACTION"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Hành động không hợp lệ" in resp.json()["detail"]

    # Claim rỗng
    resp_empty = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "  ", "action": "APPROVE"},
        headers=headers,
    )
    assert resp_empty.status_code == 400


def test_review_action_approve_and_reject(api_test_env):
    """POST review action cập nhật trạng thái trong SQLite và di chuyển tệp."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]
    quarantine_dir = api_test_env["quarantine_dir"]
    processed_dir = api_test_env["processed_dir"]
    learning_db = api_test_env["learning_db"]
    db_path = api_test_env["db_path"]

    # Tạo tệp trong quarantine
    target_file = quarantine_dir / "target_claim.json"
    target_file.write_text('{"item": "claim_test"}', encoding="utf-8")

    # Tạo bản ghi trong open_questions
    learning_db.execute_insert("open_questions", {
        "question_id": "target_claim",
        "title": "Nghi vấn tri thức",
        "question": "Nội dung đang chờ xem xét",
        "scope_json": "{}",
        "trigger": "CONTRADICTION",
        "related_claim_refs_json": "[]",
        "related_knowledge_refs_json": "[]",
        "known_evidence_refs_json": "[]",
        "needed_observations_json": "[]",
        "needed_capabilities_json": "[]",
        "status": "OPEN",
        "created_at": "2026-10-08T00:00:00Z",
    })

    # APPROVE
    resp = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "target_claim", "action": "APPROVE"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["new_status"] == "APPROVED"
    assert resp.json()["updated_records"] >= 1

    # File phải được chuyển từ quarantine sang processed
    assert not target_file.exists()
    assert (processed_dir / "target_claim.json").exists()

    # Kiểm tra trạng thái đã cập nhật trong DB
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM open_questions WHERE question_id = 'target_claim'")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "APPROVED"
    finally:
        conn.close()

    # REJECT một bản ghi khác
    learning_db.execute_insert("open_questions", {
        "question_id": "reject_claim",
        "title": "Bản ghi từ chối",
        "question": "Bản ghi bị từ chối",
        "scope_json": "{}",
        "trigger": "CONTRADICTION",
        "related_claim_refs_json": "[]",
        "related_knowledge_refs_json": "[]",
        "known_evidence_refs_json": "[]",
        "needed_observations_json": "[]",
        "needed_capabilities_json": "[]",
        "status": "OPEN",
        "created_at": "2026-10-08T00:00:00Z",
    })
    resp_rej = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "reject_claim", "action": "REJECT"},
        headers=headers,
    )
    assert resp_rej.status_code == 200
    assert resp_rej.json()["new_status"] == "REJECTED"


def test_get_config_sources_lists_17_commercial_sources(api_test_env):
    """GET /api/scp/v3/config/sources liệt kê đủ 17 nguồn và che giấu key."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]

    # Mock cấu hình với 2 nguồn có key, 1 nguồn placeholder, các nguồn còn lại trống
    loader = DataSourceConfigLoader(
        env_dict={
            "ALPHAVANTAGE_API_KEY": "alphavantage_secret_12345678",
            "FRED_API_KEY": "fred_prod_secret_87654321",
            "NEWSAPI_API_KEY": "demo",  # placeholder -> disabled
        },
        auto_load_env=False,
    )
    set_default_loader(loader)

    resp = client.get("/api/scp/v3/config/sources", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 17
    assert data["count"] == 17
    assert len(data["sources"]) == 17

    # Tìm nguồn AlphaVantage
    alpha_item = next(s for s in data["sources"] if s["source"] == "alphavantage")
    assert alpha_item["status"] == "Enabled"
    assert alpha_item["enabled"] is True
    # Đảm bảo KHÔNG rò rỉ secret thô
    assert "alphavantage_secret_12345678" not in alpha_item["masked_key"]
    assert alpha_item["masked_key"] == "alp...78"

    # FRED
    fred_item = next(s for s in data["sources"] if s["source"] == "fred")
    assert fred_item["status"] == "Enabled"
    assert fred_item["masked_key"] == "fre...21"

    # NewsAPI (placeholder) -> Disabled
    news_item = next(s for s in data["sources"] if s["source"] == "newsapi")
    assert news_item["status"] == "Disabled"
    assert news_item["enabled"] is False
    assert news_item["masked_key"] == "<empty>"

    # Agriculture (chưa set) -> Disabled
    agri_item = next(s for s in data["sources"] if s["source"] == "agriculture")
    assert agri_item["status"] == "Disabled"
    assert agri_item["masked_key"] == "<empty>"


def test_review_action_strict_matching_no_unintended_bulk_updates(api_test_env):
    """Bảo đảm hành động review không áp dụng LIKE wildcard lỏng lẻo làm cập nhật nhầm nhiều bản ghi."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]
    learning_db = api_test_env["learning_db"]
    db_path = api_test_env["db_path"]

    # Tạo 2 câu hỏi riêng biệt có chứa chữ 'a'
    learning_db.execute_insert("open_questions", {
        "question_id": "oq_paris",
        "title": "Capital of France",
        "question": "What is the capital of France?",
        "scope_json": "{}",
        "trigger": "CONTRADICTION",
        "related_claim_refs_json": "[]",
        "related_knowledge_refs_json": "[]",
        "known_evidence_refs_json": "[]",
        "needed_observations_json": "[]",
        "needed_capabilities_json": "[]",
        "status": "OPEN",
        "created_at": "2026-10-08T00:00:00Z",
    })
    learning_db.execute_insert("open_questions", {
        "question_id": "oq_mars",
        "title": "Gravity on Mars",
        "question": "Calculate acceleration on Mars",
        "scope_json": "{}",
        "trigger": "CONTRADICTION",
        "related_claim_refs_json": "[]",
        "related_knowledge_refs_json": "[]",
        "known_evidence_refs_json": "[]",
        "needed_observations_json": "[]",
        "needed_capabilities_json": "[]",
        "status": "OPEN",
        "created_at": "2026-10-08T00:00:00Z",
    })

    # Duyệt với claim 'a' (chữ cái phổ biến) -> Không được làm đổi trạng thái của cả 2 câu hỏi
    resp = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "a", "action": "APPROVE"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["updated_records"] == 0

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, status FROM open_questions WHERE question_id IN ('oq_paris', 'oq_mars')")
        rows = dict(cursor.fetchall())
        assert rows["oq_paris"] == "OPEN"
        assert rows["oq_mars"] == "OPEN"
    finally:
        conn.close()

    # Duyệt chính xác 'oq_paris' -> Chỉ cập nhật đúng oq_paris
    resp_exact = client.post(
        "/api/scp/v3/knowledge/review/action",
        json={"claim": "oq_paris", "action": "APPROVE"},
        headers=headers,
    )
    assert resp_exact.status_code == 200
    assert resp_exact.json()["updated_records"] == 1

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, status FROM open_questions WHERE question_id IN ('oq_paris', 'oq_mars')")
        rows = dict(cursor.fetchall())
        assert rows["oq_paris"] == "APPROVED"
        assert rows["oq_mars"] == "OPEN"
    finally:
        conn.close()


def test_quarantine_json_preview_over_500_bytes(api_test_env):
    """File JSON cách ly lớn hơn 500 bytes vẫn parse được data_preview chính xác."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]
    quarantine_dir = api_test_env["quarantine_dir"]

    # Tạo tệp JSON cách ly dài hơn 500 bytes
    large_payload = {
        "item_id": "large_quarantined_claim",
        "domain": "medical",
        "long_text": "A" * 700,
        "metadata": {"source": "inbox_stream", "details": "B" * 200},
    }
    q_file = quarantine_dir / "large_claim.json"
    q_file.write_text(json.dumps(large_payload), encoding="utf-8")

    resp = client.get("/api/v3/knowledge/review", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    target = next(f for f in data["quarantined_files"] if f["filename"] == "large_claim.json")
    assert "data_preview" in target
    assert target["data_preview"]["item_id"] == "large_quarantined_claim"
    assert len(target["preview"]) == 500


def test_inbox_daemon_watcher_and_admin_knowledge_lifecycle_integration(api_test_env, monkeypatch):
    """Kiểm chứng vòng lặp trọn gói: InboxDaemon -> InboxWatcher -> Admin Review Routes -> Retention Cleanup."""
    client = api_test_env["client"]
    headers = api_test_env["headers"]
    quarantine_dir = api_test_env["quarantine_dir"]
    processed_dir = api_test_env["processed_dir"]
    db_path = api_test_env["db_path"]
    tmp_root = quarantine_dir.parent

    inbox_dir = tmp_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    from scp.knowledge.inbox_daemon import InboxDaemon
    from scp.knowledge.inbox_watcher import InboxWatcher

    watcher = InboxWatcher(
        inbox_dir=inbox_dir,
        db_path=db_path,
        archive_dir=processed_dir,
        quarantine_dir=quarantine_dir,
    )
    daemon = InboxDaemon(
        inbox_dir=inbox_dir,
        db_path=db_path,
        watcher=watcher,
        retention_days=15,
    )

    # 1. Tạo 1 file lỗi (bị cách ly) và 1 file sạch trong inbox
    bad_file = inbox_dir / "bad_fact.json"
    bad_file.write_text("{broken json format", encoding="utf-8")

    good_file = inbox_dir / "good_fact.json"
    good_file.write_text(
        json.dumps({
            "item_id": "good_01",
            "domain": "astronomy",
            "question": "Khoảng cách từ Trái Đất đến Mặt Trăng là bao nhiêu?",
            "text": "Khoảng cách từ Trái Đất đến Mặt Trăng là 384400 km và chu kỳ quay là 27.3 ngày.",
        }),
        encoding="utf-8",
    )

    # 2. Daemon chạy quét 1 lượt
    results = daemon.run_once()
    assert len(results) >= 2
    assert daemon.processed_count >= 1
    assert daemon.error_count >= 1

    # 3. File sạch đã nằm trong processed_dir, file lỗi nằm trong quarantine_dir
    assert not (inbox_dir / "good_fact.json").exists()
    assert not (inbox_dir / "bad_fact.json").exists()
    assert (quarantine_dir / "bad_fact.json").exists()

    # 4. Admin API thấy file cách ly
    resp = client.get("/api/v3/knowledge/review", headers=headers)
    assert resp.status_code == 200
    review_data = resp.json()
    assert any(f["filename"] == "bad_fact.json" for f in review_data["quarantined_files"])

    # 5. Admin duyệt APPROVE cho file cách ly
    approve_resp = client.post(
        "/api/v3/knowledge/review/action",
        json={"claim": "bad_fact.json", "action": "APPROVE"},
        headers=headers,
    )
    assert approve_resp.status_code == 200

    # 6. Retention cleanup: đặt thời gian tệp trong processed_dir về 20 ngày trước
    import time
    for f in processed_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            t_old = time.time() - (20 * 86400)
            os.utime(f, (t_old, t_old))

    # Chạy cleanup_archive với retention_days=15 (mặc định của instance)
    cleaned = daemon.cleanup_archive()
    assert cleaned >= 1


