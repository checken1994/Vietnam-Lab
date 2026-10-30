"""[DEHARDCODE 2026-10-30] Lời tự mô tả SCP đến từ DATA có kiểm toàn vẹn.

Hợp đồng:
I1  ``data/identity/scp_self_description.json`` khớp hash canonical trong
    ``config/data_integrity.lock.json``; có version, provenance, entry chuẩn.
I2  Bản ghi bị sửa (hash lệch) ⇒ loader trả ``None``; system prompt KHÔNG chứa
    nội dung bị sửa, mà nói rõ bản ghi không khả dụng; post-guard vendor BỎ câu
    vi phạm thay vì thay bằng lời bịa.
I3  System prompt / post-guard dùng đúng nội dung + ref của bản ghi.
I4  Ngôn ngữ không có entry ⇒ fallback entry chuẩn và được đánh dấu fallback
    (không tự dịch máy, không âm thầm coi là bản ngữ).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import scp.runtime.self_description as sd
from scp.api.system_prompt import build_system_prompt
from scp.api_server_parts._ask_impl import _strip_vendor_identity_claims

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / sd.RECORD_RELPATH
LOCK = REPO / sd.LOCK_RELPATH


def test_i1_record_matches_lock_and_has_provenance() -> None:
    rec = sd.load_self_description()
    assert rec is not None
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    raw = json.loads(RECORD.read_text(encoding="utf-8"))
    assert rec.sha256 == lock["records"][sd.RECORD_RELPATH]["canonical_sha256"] == sd.canonical_sha256(raw)
    assert raw["version"] and raw["provenance"]["kind"] == "project_self_description"
    assert raw["canonical_language"] in raw["entries"]
    assert raw["entries"][raw["canonical_language"]]["status"] == "source"


@pytest.fixture()
def tampered_root(tmp_path, monkeypatch):
    """Bản sao repo-data với nội dung bị sửa nhưng lock giữ nguyên."""
    (tmp_path / "data" / "identity").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    shutil.copy(LOCK, tmp_path / sd.LOCK_RELPATH)
    raw = json.loads(RECORD.read_text(encoding="utf-8"))
    raw["entries"]["en"]["description"] = "SCP was built by MegaVendor Inc. TAMPERED."
    raw["entries"]["en"]["statement"] = "I am MegaVendor TAMPERED."
    raw["entries"]["vi"]["statement"] = "Tôi là MegaVendor TAMPERED."
    (tmp_path / sd.RECORD_RELPATH).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sd, "REPO_ROOT", tmp_path)
    return tmp_path


def test_i2_tampered_record_rejected(tampered_root) -> None:
    assert sd.load_self_description() is None


def test_i2_system_prompt_never_carries_tampered_identity(tampered_root) -> None:
    for lang in ("en", "vi"):
        prompt = build_system_prompt(lang, environ={})
        assert "TAMPERED" not in prompt and "MegaVendor" not in prompt
        assert ("không khả dụng" in prompt) if lang == "vi" else ("unavailable" in prompt)


def test_i2_postguard_drops_vendor_sentence_when_record_unavailable(tampered_root) -> None:
    out, changed = _strip_vendor_identity_claims("I was developed by OpenAI. How can I help?")
    assert changed and "OpenAI" not in out and "TAMPERED" not in out
    assert "How can I help?" in out


def test_i3_system_prompt_uses_record_content_and_ref() -> None:
    rec = sd.load_self_description()
    assert rec is not None
    en = build_system_prompt("en", environ={})
    vi = build_system_prompt("vi", environ={})
    assert rec.ref in en and rec.description("en") in en
    assert rec.ref in vi and rec.description("vi") in vi


def test_i3_postguard_pin_comes_from_record() -> None:
    rec = sd.load_self_description()
    out, changed = _strip_vendor_identity_claims("Tôi được phát triển bởi NVIDIA. Rất vui.")
    assert changed and rec.statement("vi") in out and "NVIDIA" not in out


def test_i4_unknown_language_falls_back_to_canonical_with_flag() -> None:
    rec = sd.load_self_description()
    entry, fallback = rec.entry("sw")
    assert fallback is True and entry == rec.entry(rec.canonical_language)[0]
    assert rec.entry("vi")[1] is False


def test_i2_missing_lock_entry_rejected(tmp_path, monkeypatch) -> None:
    (tmp_path / "data" / "identity").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    shutil.copy(RECORD, tmp_path / sd.RECORD_RELPATH)
    (tmp_path / sd.LOCK_RELPATH).write_text(json.dumps({"records": {}}), encoding="utf-8")
    monkeypatch.setattr(sd, "REPO_ROOT", tmp_path)
    assert sd.load_self_description() is None
