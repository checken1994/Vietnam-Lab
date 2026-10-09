"""
Unit, Contract & Integration Tests for Knowledge Inbox Watcher.
==============================================================
Kiểm chứng 100% các luồng nhân quả của scp/knowledge/inbox_watcher.py:
  1. Trích xuất claims thật (ClaimExtractor) từ payload JSON / text.
  2. Quét và áp dụng kháng thể thật (DomainAntibodySystem).
  3. Áp đặt ranh giới an toàn ở tầng cơ sở dữ liệu SQLite thật (LearningDB):
     - Ghi nhận open_questions & missing_pieces khi vi phạm kháng thể.
     - Ghi nhận hypotheses khi tri thức hợp lệ và có claims.
     - Phân loại NO_CLAIMS khi nội dung không chứa khẳng định thực tế.
  4. Quản lý vòng đời tệp: chuyển vào processed/ (hợp lệ) hoặc quarantine/ (vi phạm/lỗi/NO_CLAIMS).
  5. Xử lý fail-closed khi gặp tệp hỏng / JSON lỗi cú pháp / tệp rỗng '[]'.
  6. Bỏ qua an toàn các tệp ẩn (.gitkeep, .gitignore...) và tệp tạm.
  7. Hỗ trợ đa bảng mã ký tự (UTF-8, UTF-16, Latin-1).
  8. Xử lý an toàn khi nhiều kháng thể cùng vi phạm và chống va chạm tên tệp.
"""
import json
import sqlite3
import pytest
from pathlib import Path

from scp.knowledge.inbox_watcher import InboxWatcher
from scp.knowledge.learning_db import LearningDB


@pytest.fixture
def test_env(tmp_path):
    inbox_dir = tmp_path / "inbox"
    db_path = tmp_path / "learning.sqlite"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    db = LearningDB(db_path)
    watcher = InboxWatcher(
        inbox_dir=inbox_dir,
        db_path=db_path,
        learning_db=db,
        archive=True,
    )
    return {
        "watcher": watcher,
        "inbox_dir": inbox_dir,
        "db_path": db_path,
        "db": db,
        "archive_dir": inbox_dir / "processed",
        "quarantine_dir": inbox_dir / "quarantine",
    }


def test_inbox_watcher_process_valid_factual_knowledge(test_env):
    """Tri thức sạch -> claims trích xuất -> kháng thể PASS -> ghi hypotheses vào DB -> chuyển vào processed/"""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    archive_dir = test_env["archive_dir"]
    db_path = test_env["db_path"]

    # Tạo file json chứa thông tin địa lý và số liệu hợp lệ
    data_file = inbox_dir / "geography_fact.json"
    data_file.write_text(
        json.dumps({
            "item_id": "geo_001",
            "domain": "geography",
            "question": "Thủ đô của Việt Nam là gì?",
            "text": "Hà Nội là thủ đô của Việt Nam. Dân số khoảng 8 triệu người năm 2023.",
        }),
        encoding="utf-8",
    )

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]

    assert res.status == "ACCEPTED"
    assert len(res.items) == 1
    item = res.items[0]
    assert item.status == "ACCEPTED"
    assert len(item.claims) > 0
    assert len(item.failed_antibodies) == 0
    assert item.hypothesis_id is not None

    # Tệp phải được chuyển vào processed/
    assert not data_file.exists()
    assert (archive_dir / "geography_fact.json").exists()

    # Kiểm tra thực tế dữ liệu ghi trong SQLite (Database level verification)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT hypothesis_id, question_ref, hypothesis, status FROM hypotheses")
        rows = cursor.fetchall()
        assert len(rows) == 1
        h_id, q_ref, hyp_text, h_status = rows[0]
        assert h_id == item.hypothesis_id
        assert q_ref == "geo_001"
        assert h_status == "SUPPORTED"
        assert "Hà Nội" in hyp_text


def test_inbox_watcher_detects_antibody_violation_and_records_open_question(test_env):
    """Tri thức vi phạm kháng thể (quá liều thuốc) -> REJECTED -> ghi open_questions & missing_pieces -> quarantine/"""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    quarantine_dir = test_env["quarantine_dir"]
    db_path = test_env["db_path"]

    # Liều paracetamol 25000 mg (vượt ngưỡng an toàn 4000mg/ngày)
    toxic_file = inbox_dir / "overdose_advice.json"
    toxic_file.write_text(
        json.dumps({
            "item_id": "med_danger_999",
            "domain": "medical",
            "question": "Liều dùng paracetamol là bao nhiêu?",
            "text": "Bệnh nhân nên uống paracetamol liều 25000 mg mỗi ngày để giảm sốt.",
        }),
        encoding="utf-8",
    )

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]

    assert res.status == "REJECTED"
    assert len(res.items) == 1
    item = res.items[0]
    assert item.status == "REJECTED"
    assert len(item.failed_antibodies) > 0
    assert any(a.antibody_name == "dosage_validator" for a in item.failed_antibodies)
    assert item.open_question_id is not None
    assert item.missing_piece_id is not None

    # Tệp phải bị cách ly vào quarantine/
    assert not toxic_file.exists()
    assert (quarantine_dir / "overdose_advice.json").exists()

    # Kiểm tra dữ liệu ghi vào SQLite (Database level verification)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, title, trigger, status FROM open_questions")
        oq_rows = cursor.fetchall()
        assert len(oq_rows) == 1
        assert oq_rows[0][0] == item.open_question_id
        assert oq_rows[0][2] == "CONTRADICTION"
        assert oq_rows[0][3] == "OPEN"

        cursor.execute("SELECT missing_piece_id, question_id, kind, discovered_by FROM missing_pieces")
        mp_rows = cursor.fetchall()
        assert len(mp_rows) == 1
        assert mp_rows[0][0] == item.missing_piece_id
        assert mp_rows[0][1] == item.open_question_id
        assert mp_rows[0][2] == "MISSING_EVIDENCE"
        assert mp_rows[0][3] == "inbox_watcher"


def test_inbox_watcher_process_plaintext_file(test_env):
    """File văn bản thuần .txt trong inbox có trích dẫn hợp lệ."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    archive_dir = test_env["archive_dir"]

    txt_file = inbox_dir / "note.txt"
    txt_file.write_text("Theo báo cáo năm 2022 tuổi thọ trung bình tăng (nguồn: https://who.org/report).", encoding="utf-8")

    results = watcher.scan_once()
    assert len(results) == 1
    assert results[0].status == "ACCEPTED"
    assert (archive_dir / "note.txt").exists()


def test_inbox_watcher_fail_closed_on_corrupted_json(test_env):
    """File JSON bị hỏng cú pháp -> status CORRUPTED -> ghi nhận open_questions -> quarantine/"""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    quarantine_dir = test_env["quarantine_dir"]
    db_path = test_env["db_path"]

    bad_file = inbox_dir / "corrupted.json"
    bad_file.write_text("{this is not valid json!@@#", encoding="utf-8")

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]
    assert res.status == "CORRUPTED"
    assert res.error is not None
    assert (quarantine_dir / "corrupted.json").exists()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, trigger FROM open_questions")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "UNKNOWN"


def test_inbox_watcher_fail_closed_on_empty_json_list(test_env):
    """Tệp JSON rỗng '[]' -> CORRUPTED -> cách ly vào quarantine/ -> không được ACCEPTED."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    quarantine_dir = test_env["quarantine_dir"]
    db_path = test_env["db_path"]

    empty_file = inbox_dir / "empty.json"
    empty_file.write_text("[]", encoding="utf-8")

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]
    assert res.status == "CORRUPTED"
    assert (quarantine_dir / "empty.json").exists()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, trigger FROM open_questions WHERE trigger='EMPTY_FILE'")
        rows = cursor.fetchall()
        assert len(rows) == 1


def test_inbox_watcher_no_claims_content(test_env):
    """Văn bản không có khẳng định số liệu/thực thể nào -> status NO_CLAIMS -> chuyển vào quarantine/"""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    quarantine_dir = test_env["quarantine_dir"]
    db_path = test_env["db_path"]

    no_claim_file = inbox_dir / "chat_greeting.json"
    no_claim_file.write_text(
        json.dumps({"item_id": "greet_1", "text": "Xin chào! Chúc một ngày tốt lành."}),
        encoding="utf-8",
    )

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]
    assert res.status == "NO_CLAIMS"
    assert res.items[0].status == "NO_CLAIMS"
    assert (quarantine_dir / "chat_greeting.json").exists()

    # Không ghi vào bảng hypotheses
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM hypotheses")
        assert cursor.fetchone()[0] == 0


def test_inbox_watcher_ignores_hidden_files(test_env):
    """Tệp ẩn như .gitkeep, .gitignore không được quét hoặc di chuyển làm hỏng git repo."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]

    gitkeep = inbox_dir / ".gitkeep"
    gitkeep.write_text("", encoding="utf-8")

    results = watcher.scan_once()
    assert len(results) == 0
    # .gitkeep phải giữ nguyên vị trí, không bị di chuyển
    assert gitkeep.exists()


def test_inbox_watcher_multi_encoding_support(test_env):
    """Tệp mã hóa UTF-16 hoặc Latin-1 phải được đọc thành công mà không văng UnicodeDecodeError."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    archive_dir = test_env["archive_dir"]

    utf16_file = inbox_dir / "utf16_doc.txt"
    utf16_file.write_text("Theo báo cáo 2024 tại https://unesco.org thì di sản được bảo tồn.", encoding="utf-16")

    results = watcher.scan_once()
    assert len(results) == 1
    assert results[0].status == "ACCEPTED"
    assert (archive_dir / "utf16_doc.txt").exists()


def test_inbox_watcher_collision_free_archiving(test_env):
    """Khi có 2 tệp trùng tên rơi vào inbox liên tiếp, tệp sau được đổi tên duy nhất không bị đè."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    archive_dir = test_env["archive_dir"]

    # Lần 1
    f1 = inbox_dir / "report.txt"
    f1.write_text("Theo tài liệu 2020 nguồn https://who.org/data1 dữ liệu tăng 10%.", encoding="utf-8")
    watcher.scan_once()
    assert (archive_dir / "report.txt").exists()

    # Lần 2 với cùng tên 'report.txt'
    f2 = inbox_dir / "report.txt"
    f2.write_text("Theo tài liệu 2021 nguồn https://who.org/data2 dữ liệu tăng 20%.", encoding="utf-8")
    watcher.scan_once()

    # Cả 2 tệp đều phải được lưu trong archive_dir
    archived_files = list(archive_dir.glob("report*"))
    assert len(archived_files) == 2


def test_inbox_watcher_multiple_items_mixed_status(test_env):
    """File chứa danh sách nhiều items: 1 sạch, 1 vi phạm -> tổng thể bị REJECTED."""
    watcher = test_env["watcher"]
    inbox_dir = test_env["inbox_dir"]
    quarantine_dir = test_env["quarantine_dir"]

    mixed_file = inbox_dir / "batch.json"
    mixed_file.write_text(
        json.dumps([
            {
                "item_id": "clean_1",
                "domain": "geography",
                "text": "Tokyo là thủ đô của Nhật Bản.",
            },
            {
                "item_id": "bad_1",
                "domain": "medical",
                "question": "Liều paracetamol?",
                "text": "Uống 50000 mg paracetamol ngay lập tức.",
            },
        ]),
        encoding="utf-8",
    )

    results = watcher.scan_once()
    assert len(results) == 1
    res = results[0]
    assert res.status == "REJECTED"
    assert len(res.items) == 2
    assert res.items[0].status == "ACCEPTED"
    assert res.items[1].status == "REJECTED"
    assert (quarantine_dir / "batch.json").exists()


def test_inbox_watcher_watch_loop_termination(test_env):
    """Kiểm tra vòng lặp watch dừng an toàn qua max_cycles hoặc stop()."""
    watcher = test_env["watcher"]
    # Không có file, chạy 1 cycle
    res = watcher.watch(poll_interval=0.01, max_cycles=1)
    assert res == []

    watcher.stop()
    assert watcher._stop_requested is True
