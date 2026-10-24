"""[K2-W1 2026-10-23] EffectiveSettings — precedence, legacy redirect, override.

Pin contract wave K2 (HC-03/HC-04/HC-05):
  1. Precedence: process env (sau khi env_loader nạp) là nguồn; settings là
     ĐIỂM ĐỌC CHUẨN — KHÔNG cache giá trị, env đổi runtime phải thấy ngay.
  2. Legacy (SCP_STORAGE_BACKEND) → redirect sang canonical (SCP_KERNEL_BACKEND):
     canonical THẮNG khi cả hai set (đối chiếu contract thật của
     kernel_storage.make_storage), fallback legacy khi canonical vắng mặt,
     WARNING đúng 1 lần/legacy key, KHÔNG BAO GIỜ log giá trị.
  3. get_bool/get_int edge: "1"/"true"/"0"/""/garbage/missing.
  4. temporary_override: set → restore CHÍNH XÁC (kể cả key chưa từng tồn tại
     → pop), restore chạy cả khi body raise (finally) — gốc rễ HC-04.
  5. __main__._load_env_at_startup delegate env_loader: gọi lại trên cùng file
     KHÔNG clobber biến ghi vào os.environ sau lần load đầu (audit 1.2 class).

Mọi env test dùng monkeypatch — hermetic, không đụng env thật của process test.
"""
from __future__ import annotations

import logging
import os

import pytest

from scp.core import settings as st
from scp.security import env_loader

CANON = "SCP_KERNEL_BACKEND"
LEGACY = "SCP_STORAGE_BACKEND"


@pytest.fixture(autouse=True)
def _fresh_settings_state(monkeypatch):
    """Mỗi test khởi động với warning-state + stats sạch (hermetic)."""
    monkeypatch.setattr(st, "_legacy_warned", set())
    monkeypatch.setattr(st, "_bad_value_warned", set())
    monkeypatch.setattr(
        st, "_stats", {"reads": 0, "legacy_redirects": 0, "defaults_used": 0}
    )
    monkeypatch.delenv(CANON, raising=False)
    monkeypatch.delenv(LEGACY, raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Precedence env > default; không cache giá trị
# ---------------------------------------------------------------------------

def test_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("K2S_KEY", "from-env")
    assert st.get("K2S_KEY", "default") == "from-env"
    monkeypatch.delenv("K2S_KEY")
    assert st.get("K2S_KEY", "default") == "default"
    assert st.get("K2S_KEY") is None


def test_value_is_stripped(monkeypatch):
    monkeypatch.setenv("K2S_PADDED", "  spaced  ")
    assert st.get("K2S_PADDED") == "spaced"


def test_no_value_caching_live_read(monkeypatch):
    monkeypatch.setenv("K2S_LIVE", "first")
    assert st.get("K2S_LIVE") == "first"
    monkeypatch.setenv("K2S_LIVE", "second")  # đổi runtime giữa 2 lần đọc
    assert st.get("K2S_LIVE") == "second", (
        "settings cached a stale value — env có thể đổi runtime, đọc phải live"
    )


# ---------------------------------------------------------------------------
# 2. Legacy → canonical redirect (HC-05)
# ---------------------------------------------------------------------------

def test_legacy_redirect_canonical_wins(monkeypatch, caplog):
    monkeypatch.setenv(CANON, "postgres")
    monkeypatch.setenv(LEGACY, "sqlite")
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        value = st.get(LEGACY)
    assert value == "postgres", "canonical phải thắng khi cả hai set (HC-05)"
    warn_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(LEGACY in msg and CANON in msg for msg in warn_messages), (
        f"legacy use must warn naming both keys: {warn_messages}"
    )
    # secrecy invariant: không bao giờ log giá trị
    assert all("postgres" not in msg and "sqlite" not in msg for msg in warn_messages)


def test_legacy_fallback_when_canonical_absent(monkeypatch, caplog):
    """Contract make_storage thật: canonical vắng mặt → legacy vẫn honored."""
    monkeypatch.setenv(LEGACY, "sqlite")
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        value = st.get(LEGACY)
    assert value == "sqlite"
    warn_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(LEGACY in msg for msg in warn_messages)


def test_legacy_warning_emitted_exactly_once(monkeypatch, caplog):
    monkeypatch.setenv(LEGACY, "sqlite")
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        for _ in range(3):
            st.get(LEGACY)
    legacy_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and LEGACY in r.getMessage()
    ]
    assert len(legacy_warnings) == 1, (
        f"WARNING phải đúng 1 lần/legacy key, thấy {len(legacy_warnings)}"
    )


def test_canonical_read_does_not_fall_back_to_legacy(monkeypatch):
    """Đọc bằng TÊN canonical chỉ đọc canonical — điểm đọc mới không lọt legacy."""
    monkeypatch.setenv(LEGACY, "sqlite")
    monkeypatch.delenv(CANON, raising=False)
    assert st.get(CANON, "default") == "default"


def test_legacy_missing_returns_default(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        assert st.get(LEGACY, "dflt") == "dflt"
    # Redirect + cảnh báo vẫn xảy ra dù không có giá trị (giáo dục caller).
    warn_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(LEGACY in msg for msg in warn_messages)


# ---------------------------------------------------------------------------
# 3. get_bool / get_int edges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("  yes  ", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("  ", False), ("off", False),
])
def test_get_bool_edges(monkeypatch, raw, expected):
    monkeypatch.setenv("K2S_FLAG", raw)
    assert st.get_bool("K2S_FLAG") is expected


def test_get_bool_missing_uses_default(monkeypatch):
    monkeypatch.delenv("K2S_FLAG", raising=False)
    assert st.get_bool("K2S_FLAG") is False
    assert st.get_bool("K2S_FLAG", default=True) is True


def test_get_bool_garbage_is_false_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("K2S_FLAG", "maybe")
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        assert st.get_bool("K2S_FLAG") is False
        assert st.get_bool("K2S_FLAG") is False  # lần 2 — không warn nữa
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "K2S_FLAG" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert all("maybe" not in r.getMessage() for r in warnings), "value leaked"


@pytest.mark.parametrize("raw,expected", [
    ("42", 42), ("  7  ", 7), ("-3", -3), ("0", 0),
])
def test_get_int_edges(monkeypatch, raw, expected):
    monkeypatch.setenv("K2S_NUM", raw)
    assert st.get_int("K2S_NUM", 99) == expected


def test_get_int_bad_and_missing_use_default(monkeypatch, caplog):
    monkeypatch.setenv("K2S_NUM", "abc")
    with caplog.at_level(logging.WARNING, logger="scp.core.settings"):
        assert st.get_int("K2S_NUM", 120) == 120
        assert st.get_int("K2S_NUM", 120) == 120
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "K2S_NUM" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert all("abc" not in r.getMessage() for r in warnings), "value leaked"
    monkeypatch.delenv("K2S_NUM")
    assert st.get_int("K2S_NUM", 120) == 120


# ---------------------------------------------------------------------------
# 4. temporary_override — finally-restore (gốc rễ HC-04)
# ---------------------------------------------------------------------------

def test_temporary_override_sets_and_restores_exact(monkeypatch):
    monkeypatch.setenv("K2S_OVR_A", "old")
    monkeypatch.delenv("K2S_OVR_B", raising=False)
    with st.temporary_override({"K2S_OVR_A": "new", "K2S_OVR_B": "temp"}):
        assert os.environ["K2S_OVR_A"] == "new"
        assert os.environ["K2S_OVR_B"] == "temp"
    assert os.environ["K2S_OVR_A"] == "old"
    assert "K2S_OVR_B" not in os.environ, (
        "key chưa từng tồn tại phải được POP, không phải set thành chuỗi rỗng"
    )


def test_temporary_override_restores_on_exception(monkeypatch):
    """Gốc rễ HC-04: exception trong body vẫn phải restore (finally)."""
    monkeypatch.setenv("K2S_OVR_C", "old")
    monkeypatch.delenv("K2S_OVR_D", raising=False)
    with pytest.raises(RuntimeError):
        with st.temporary_override({"K2S_OVR_C": "new", "K2S_OVR_D": "temp"}):
            raise RuntimeError("boom giữa lifespan")
    assert os.environ["K2S_OVR_C"] == "old"
    assert "K2S_OVR_D" not in os.environ


def test_temporary_override_value_coerced_to_str(monkeypatch):
    with st.temporary_override({"K2S_OVR_E": 0}):
        assert os.environ["K2S_OVR_E"] == "0"
    assert "K2S_OVR_E" not in os.environ


# ---------------------------------------------------------------------------
# 5. __main__ delegate — không clobber runtime env (HC-02)
# ---------------------------------------------------------------------------

def test_main_delegate_does_not_clobber_runtime_env(tmp_path, monkeypatch):
    """Delegate phải kế thừa idempotency per-path của env_loader: lần gọi 2
    trên cùng file là no-op, biến ghi vào os.environ sau lần load đầu (argv
    port, launcher override) được giữ nguyên."""
    import scp.__main__ as scp_main

    monkeypatch.setattr(env_loader, "_applied_files", set())
    monkeypatch.delenv("SCP_ENV_FILE", raising=False)  # import __main__ đã chạy với env sạch
    env_file = tmp_path / "main.env"
    env_file.write_text("K2S_MAIN_PIN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SCP_ENV_FILE", str(env_file))

    scp_main._load_env_at_startup()
    assert os.environ["K2S_MAIN_PIN"] == "from-file"

    monkeypatch.setenv("K2S_MAIN_PIN", "runtime-value")  # mô phỏng main() ghi sau
    scp_main._load_env_at_startup()  # lần 2 trên CÙNG file — phải no-op
    assert os.environ["K2S_MAIN_PIN"] == "runtime-value", (
        "delegate re-clobbered a runtime-written variable (audit 1.2 class)"
    )


# ---------------------------------------------------------------------------
# 6. Thống kê tối giản
# ---------------------------------------------------------------------------

def test_stats_counts_reads_and_redirects(monkeypatch):
    monkeypatch.setenv(LEGACY, "sqlite")
    monkeypatch.setenv("K2S_ST", "x")
    st.get("K2S_ST")
    st.get("K2S_MISSING", "d")
    st.get(LEGACY)
    snapshot = st.stats()
    assert snapshot["reads"] >= 3
    assert snapshot["legacy_redirects"] == 1
    assert snapshot["defaults_used"] >= 1
