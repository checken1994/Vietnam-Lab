"""§15 · Ký HMAC cờ control (global_kill + cờ migration một chiều) — chống sửa trực tiếp SQLite.

R6/R5 để lại: cờ one-way (journal_key_migrated, legacy_migration_done,
journal_key_window_opened_at) và global_kill nằm UNSIGNED trong bảng control —
kẻ ghi SQLite sửa/xoá được mà kernel không phát hiện. §15: mỗi cờ có cột chữ ký
riêng "<flag>_sig" (HMAC-SHA256 dưới K_journal sẵn có qua secret_registry, miền
"control"); đọc qua TaskKernel._control() verify từng cờ, chữ ký sai -> giá trị
an toàn mặc định (kill=ON fail-closed, migration flags=0, mark=None).

Zero-mock: TaskKernel thật trên file SQLite thật dưới tmp_path; "kẻ tấn công"
là một sqlite3 connection thứ hai ghi thẳng bảng control — đúng threat model.
DB tiền-§15 được tái tạo bằng ALTER TABLE DROP COLUMN các cột chữ ký (đúng hình
DB viết bởi code trước §15: có cờ, không có chữ ký).
"""
from __future__ import annotations

import sqlite3

import pytest

from scp.task_kernel import KillSwitchActive, TaskKernel
from scp.task_kernel_parts.definitions import (
    SIGNED_CONTROL_FLAGS,
    compute_control_flag_sig,
    control_flag_sig_column,
)

_CAPABILITY = "sec15-capability-secret-32bytes-distinct!!"
_JOURNAL = "sec15-journal-secret-Kjournal-32bytes-value"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SCP_CAPABILITY_SECRET", _CAPABILITY)
    monkeypatch.setenv("SCP_JOURNAL_SECRET", _JOURNAL)
    for name in ("SCP_PREVIOUS_CAPABILITY_SECRETS", "SCP_PREVIOUS_JOURNAL_SECRETS", "SCP_JOURNAL_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "sec15.sqlite3"


def _raw(db, column: str):
    side = sqlite3.connect(db)
    try:
        row = side.execute(f"SELECT {column} FROM control WHERE id=1").fetchone()
        return row[0]
    finally:
        side.close()


def _set_raw(db, column: str, value) -> None:
    side = sqlite3.connect(db)
    try:
        side.execute(f"UPDATE control SET {column}=? WHERE id=1", (value,))
        side.commit()
    finally:
        side.close()


def _drop_sig_columns(db, flags) -> None:
    """Tái tạo DB tiền-§15: có cờ control, KHÔNG có cột chữ ký."""
    side = sqlite3.connect(db)
    try:
        present = {row[1] for row in side.execute('PRAGMA table_info("control")')}
        for flag in flags:
            col = control_flag_sig_column(flag)
            if col in present:
                side.execute(f'ALTER TABLE control DROP COLUMN "{col}"')
        side.commit()
    finally:
        side.close()


def _queued_task(kernel: TaskKernel, task_id: str) -> None:
    kernel.create_task(task_id, "sec15-owner", "goal", "R0", input_hash="sha256:" + "0" * 64)
    for state in ("PLANNING", "READY", "QUEUED"):
        kernel.transition(task_id, state, actor="sec15", reason="setup")


def test_fresh_db_signs_all_control_flags_and_reads_match(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        control = kernel._control()
        assert set(control["control_flags_sig_valid"]) == set(SIGNED_CONTROL_FLAGS)
        assert all(control["control_flags_sig_valid"].values())
        # Chữ ký trong DB khớp giá trị thô dưới K_journal của test.
        for flag in SIGNED_CONTROL_FLAGS:
            assert _raw(db_path, control_flag_sig_column(flag)) == compute_control_flag_sig(
                flag, _raw(db_path, flag)
            )
        assert control["global_kill"] == 0
        assert control["journal_key_migrated"] == 1  # fresh DB đã cutover từ CREATE
    finally:
        kernel.close()


def test_direct_sqlite_global_kill_clear_fails_closed_kill_on(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        _queued_task(kernel, "sec15-kill")
        kernel.set_global_kill(True, actor="operator")
        assert kernel._control()["global_kill"] == 1
    finally:
        kernel.close()
    # Kẻ ghi SQLite tắt kill switch không qua API.
    _set_raw(db_path, "global_kill", 0)
    assert _raw(db_path, "global_kill") == 0

    reopened = TaskKernel(db_path)
    try:
        control = reopened._control()
        assert control["global_kill"] == 1  # fail-closed: kill vẫn ON
        assert control["control_flags_sig_valid"]["global_kill"] is False  # verify BẮT được
        with pytest.raises(KillSwitchActive):
            reopened.claim("sec15-kill", "sec15-worker")
    finally:
        reopened.close()


def test_direct_sqlite_null_signature_is_never_resigned(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        _queued_task(kernel, "sec15-null")
        kernel.set_global_kill(True, actor="operator")
    finally:
        kernel.close()
    # Kẻ tấn công xoá chữ ký (NULL) hy vọng open sau tự ký lại giá trị đã sửa.
    _set_raw(db_path, "global_kill", 0)
    _set_raw(db_path, control_flag_sig_column("global_kill"), None)

    reopened = TaskKernel(db_path)
    try:
        control = reopened._control()
        assert control["global_kill"] == 1  # mặc định an toàn, KHÔNG tin giá trị đã sửa
        assert control["control_flags_sig_valid"]["global_kill"] is False
        # Cột đã có từ trước -> không có self-sign: chữ ký vẫn NULL trong DB.
        assert _raw(db_path, control_flag_sig_column("global_kill")) is None
        with pytest.raises(KillSwitchActive):
            reopened.claim("sec15-null", "sec15-worker")
    finally:
        reopened.close()


def test_pre_sec15_db_is_self_signed_once_at_open_without_changing_values(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        kernel.set_global_kill(True, actor="operator")  # operator đang kill — phải GIỮ NGUYÊN
    finally:
        kernel.close()
    before = {flag: _raw(db_path, flag) for flag in SIGNED_CONTROL_FLAGS}
    _drop_sig_columns(db_path, SIGNED_CONTROL_FLAGS)

    upgraded = TaskKernel(db_path)
    try:
        after = upgraded._control()
        # Không đổi giá trị: đúng giá trị hiện tại lúc open, được ký lại MỘT lần.
        for flag in SIGNED_CONTROL_FLAGS:
            assert _raw(db_path, flag) == before[flag]
            assert _raw(db_path, control_flag_sig_column(flag)) == compute_control_flag_sig(
                flag, before[flag]
            )
        assert after["global_kill"] == 1
        assert all(after["control_flags_sig_valid"].values())
        first_sig = _raw(db_path, control_flag_sig_column("global_kill"))
    finally:
        upgraded.close()

    # Mở lần hai: cột đã có -> không tự ký lại (chữ ký giữ nguyên bytes).
    again = TaskKernel(db_path)
    try:
        assert _raw(db_path, control_flag_sig_column("global_kill")) == first_sig
        assert again._control()["control_flags_sig_valid"]["global_kill"] is True
    finally:
        again.close()


def test_premature_cutover_flag_tamper_reads_zero_and_api_reanchor_resigns(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        _queued_task(kernel, "sec15-window")
    finally:
        kernel.close()
    # Xây cửa sổ hợp lệ (journal_key_migrated=0 có chữ ký HỢP LỆ) theo đúng hình
    # DB R5-era tiền-§15: đặt giá trị rồi drop cột chữ ký -> open tự ký MỘT lần.
    _set_raw(db_path, "journal_key_migrated", 0)
    _drop_sig_columns(db_path, ("journal_key_migrated", "journal_key_window_opened_at"))
    window = TaskKernel(db_path)
    try:
        control = window._control()
        assert control["journal_key_migrated"] == 0
        assert control["control_flags_sig_valid"]["journal_key_migrated"] is True
        assert control["journal_key_window_opened_at"]  # stamp window mark + ký lại

        # Kẻ ghi SQLite tự tuyên bố "đã cutover" (1) không qua reanchor.
        _set_raw(db_path, "journal_key_migrated", 1)
        tampered = window._control()
        assert tampered["journal_key_migrated"] == 0  # không tin cờ không chứng minh được
        assert tampered["control_flags_sig_valid"]["journal_key_migrated"] is False
        # Chỉ cờ bị tamper mất dấu hợp lệ; kill vẫn đọc bình thường (per-flag).
        assert tampered["global_kill"] == 0
        assert tampered["control_flags_sig_valid"]["global_kill"] is True

        # API chính quy: reanchor sạch -> cờ flip 0->1 và chữ ký khớp.
        migration = window.reanchor_all_journals()
        assert migration["failed_tasks"] == []
        assert migration["status"] == "COMPLETED"
        final = window._control()
        assert final["journal_key_migrated"] == 1
        assert final["legacy_migration_done"] == 1
        assert migration["legacy_migration_done"] is True
        assert final["control_flags_sig_valid"]["journal_key_migrated"] is True
        assert final["control_flags_sig_valid"]["legacy_migration_done"] is True
        assert _raw(db_path, control_flag_sig_column("journal_key_migrated")) == (
            compute_control_flag_sig("journal_key_migrated", 1)
        )
    finally:
        window.close()


def test_set_global_kill_api_resigns_only_the_kill_flag(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        others_before = {
            flag: _raw(db_path, control_flag_sig_column(flag))
            for flag in SIGNED_CONTROL_FLAGS
            if flag != "global_kill"
        }
        epoch_on = kernel.set_global_kill(True, actor="operator")
        assert epoch_on == 1
        assert kernel._control()["global_kill"] == 1
        assert kernel._control()["control_flags_sig_valid"]["global_kill"] is True
        assert _raw(db_path, control_flag_sig_column("global_kill")) == compute_control_flag_sig(
            "global_kill", 1
        )

        kernel.set_global_kill(False, actor="operator")
        assert kernel._control()["global_kill"] == 0
        assert _raw(db_path, control_flag_sig_column("global_kill")) == compute_control_flag_sig(
            "global_kill", 0
        )
        # Kill toggle KHÔNG ký lại chữ ký cờ khác (chống laundering cờ đang bị tamper).
        for flag, sig_before in others_before.items():
            assert _raw(db_path, control_flag_sig_column(flag)) == sig_before
        assert all(kernel._control()["control_flags_sig_valid"].values())
    finally:
        kernel.close()


def test_legacy_flag_tamper_to_one_is_not_trusted(db):
    db_path = db
    kernel = TaskKernel(db_path)
    try:
        _queued_task(kernel, "sec15-legacy")
        # legacy_migration_done=0 hợp lệ trên DB mới; kẻ tấn công tự set 1
        # (đóng cửa legacy fallback / tự tuyên bố migration xong) không qua API.
        _set_raw(db_path, "legacy_migration_done", 1)
        control = kernel._control()
        assert control["legacy_migration_done"] == 0  # mặc định an toàn
        assert control["control_flags_sig_valid"]["legacy_migration_done"] is False
        # journal_key_window_opened_at bị sửa -> đọc về None (mọi live checkpoint chặn cutover).
        _set_raw(db_path, "journal_key_window_opened_at", "1999-01-01T00:00:00+00:00")
        control = kernel._control()
        assert control["journal_key_window_opened_at"] is None
        assert control["control_flags_sig_valid"]["journal_key_window_opened_at"] is False
    finally:
        kernel.close()


def test_kill_clear_route_direct_sqlite_fallback_resigns(db, monkeypatch):
    """/kill/clear fallback ghi thẳng SQLite phải ký lại — không thì fail-closed
    kill=ON đứng sau lưng operator (route best-effort, lỗi thì giữ hướng an toàn)."""
    from scp.api.routes import pc_controller_routes as routes

    monkeypatch.setenv("SCP_KERNEL_DB_PATH", str(db))
    kernel = TaskKernel(db)
    try:
        kernel.set_global_kill(True, actor="operator")
    finally:
        kernel.close()

    results = routes._release_kernel_global_kill()
    assert results["errors"] == []

    reopened = TaskKernel(db)
    try:
        control = reopened._control()
        assert control["global_kill"] == 0
        assert control["control_flags_sig_valid"]["global_kill"] is True  # ký lại, không fail-closed
        assert _raw(db, control_flag_sig_column("global_kill")) == compute_control_flag_sig(
            "global_kill", 0
        )
    finally:
        reopened.close()


def test_k_journal_rotation_fails_closed_then_api_resigns_under_new_key(db, monkeypatch):
    """Rotation K_journal: chữ ký cờ control cũ không verify dưới khóa mới -> đọc fail-closed
    (kill=ON) — người giữ khóa đã nghỉ không ký cờ hợp lệ; API chính quy ký lại dưới khóa mới."""
    kernel = TaskKernel(db)
    try:
        _queued_task(kernel, "sec15-rotate")
    finally:
        kernel.close()

    new_journal = "sec15-NEW-journal-secret-Kjournal-32bytes!"
    monkeypatch.setenv("SCP_JOURNAL_SECRET", new_journal)
    monkeypatch.setenv("SCP_PREVIOUS_JOURNAL_SECRETS", _JOURNAL)

    rotated = TaskKernel(db)
    try:
        control = rotated._control()
        assert control["control_flags_sig_valid"]["global_kill"] is False
        assert control["global_kill"] == 1  # fail-closed kill ON, không tin sig khóa cũ
        assert control["journal_key_migrated"] == 0  # migration flags về mặc định an toàn

        # API chính quy: kill off + reanchor sạch ký lại cờ dưới K_journal mới.
        rotated.set_global_kill(False, actor="operator")
        assert rotated._control()["global_kill"] == 0
        assert rotated._control()["control_flags_sig_valid"]["global_kill"] is True
        migration = rotated.reanchor_all_journals()
        assert migration["status"] == "COMPLETED"
        assert rotated._control()["control_flags_sig_valid"]["journal_key_migrated"] is True
        assert rotated.verify_journal("sec15-rotate")["hash_chain_valid"] is True
    finally:
        rotated.close()
