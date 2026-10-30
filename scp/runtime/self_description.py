"""DEHARDCODE — SCP self-description read from DATA, never from code strings.

TẠI SAO: luật owner cấp cao nhất (30/10/2026) — "SCP chỉ lấy dữ liệu trong data,
internet đã được kiểm chứng để trả lời". Trước đây lời tự giới thiệu của SCP là
chuỗi trong code (delivery_rules, generation_stage, system_prompt, _ask_impl).
Nay nội dung nằm ở ``data/identity/scp_self_description.json`` (có version,
provenance, khoá ngôn ngữ BCP-47 mở) và CHỈ được dùng sau khi hash canonical
khớp ``config/data_integrity.lock.json``.

Fail-closed: thiếu file / JSON hỏng / hash lệch / thiếu entry chuẩn ⇒ ``None``
(caller không được bịa lời tự giới thiệu thay thế).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.runtime.self_description")

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
RECORD_RELPATH = "data/identity/scp_self_description.json"
LOCK_RELPATH = "config/data_integrity.lock.json"

__all__ = ["SelfDescription", "canonical_sha256", "load_self_description"]


def canonical_sha256(obj: Any) -> str:
    """Hash canonical JSON — không phụ thuộc EOL/khoảng trắng (autocrlf an toàn)."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelfDescription:
    record_id: str
    version: str
    canonical_language: str
    entries: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    sha256: str

    def entry(self, lang: str) -> tuple[dict[str, str], bool]:
        """(entry, is_fallback). Ngôn ngữ không có entry ⇒ entry chuẩn + cờ fallback."""
        table = {k: dict(v) for k, v in self.entries}
        key = str(lang or "").strip().lower()
        if key in table:
            return table[key], False
        return table[self.canonical_language], True

    def statement(self, lang: str) -> str:
        return self.entry(lang)[0].get("statement", "")

    def description(self, lang: str) -> str:
        return self.entry(lang)[0].get("description", "")

    @property
    def ref(self) -> str:
        return f"{self.record_id}@{self.version}#{self.sha256[:12]}"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_self_description(
    record_path: str | Path | None = None,
    lock_path: str | Path | None = None,
) -> SelfDescription | None:
    """Đọc + kiểm toàn vẹn bản ghi tự mô tả. Mọi lỗi ⇒ ``None`` (fail-closed)."""
    rpath = Path(record_path) if record_path else REPO_ROOT / RECORD_RELPATH
    lpath = Path(lock_path) if lock_path else REPO_ROOT / LOCK_RELPATH
    try:
        record = _read_json(rpath)
        lock = _read_json(lpath)
        expected = str(lock["records"][RECORD_RELPATH]["canonical_sha256"]).strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DEHARDCODE] self-description unavailable: %s", type(exc).__name__)
        return None
    actual = canonical_sha256(record)
    if len(expected) != 64 or actual != expected:
        logger.warning("[DEHARDCODE] self-description integrity mismatch — not used")
        return None
    try:
        canon = str(record["canonical_language"]).strip().lower()
        entries = record["entries"]
        if not isinstance(entries, dict) or canon not in entries:
            return None
        packed = tuple(
            (str(lang).strip().lower(), tuple((str(k), str(v)) for k, v in dict(body).items()))
            for lang, body in entries.items()
        )
        return SelfDescription(
            record_id=str(record["record_id"]),
            version=str(record["version"]),
            canonical_language=canon,
            entries=packed,
            sha256=actual,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DEHARDCODE] self-description malformed: %s", type(exc).__name__)
        return None
