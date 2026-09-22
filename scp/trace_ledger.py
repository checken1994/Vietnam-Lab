from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SECRET_WORDS = ("secret", "token", "password", "cookie", "api_key", "authorization", "private_key")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(w in str(k).lower() for w in _SECRET_WORDS) else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and any(w in value.lower() for w in _SECRET_WORDS):
        return "[REDACTED_STRING]"
    return value


def _hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _LedgerState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.last_seq: int | None = None
        self.last_hash: str | None = None


_ledger_states: dict[str, _LedgerState] = {}
_meta_lock = threading.Lock()


def _get_ledger_state(canonical_path: str) -> _LedgerState:
    with _meta_lock:
        if canonical_path not in _ledger_states:
            _ledger_states[canonical_path] = _LedgerState()
        return _ledger_states[canonical_path]


class TraceLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = _get_ledger_state(str(self.path.resolve()))

    def _read_tail(self) -> tuple[int, str | None]:
        """Read the last record in O(1) by seeking backwards from EOF in binary mode."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, None
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                pos = size
                buffer_size = 4096
                remainder = b""
                while pos > 0:
                    read_len = min(buffer_size, pos)
                    pos -= read_len
                    f.seek(pos)
                    chunk = f.read(read_len) + remainder
                    lines = chunk.splitlines()
                    if len(lines) > 1:
                        for line in reversed(lines):
                            line = line.strip()
                            if line:
                                try:
                                    data = json.loads(line.decode("utf-8"))
                                    return int(data.get("seq", 0)), data.get("hash")
                                except Exception as exc:
                                    logger.debug("Failed parsing tail record line: %s", exc)
                                    continue
                    remainder = lines[0] if lines else b""
                if remainder.strip():
                    try:
                        data = json.loads(remainder.decode("utf-8"))
                        return int(data.get("seq", 0)), data.get("hash")
                    except Exception as exc:
                        logger.debug("Failed parsing tail remainder record: %s", exc)
        except OSError as exc:
            logger.debug("Error reading trace tail from %s: %s", self.path, exc)
        return 0, None

    def append(self, **fields: Any) -> dict[str, Any]:
        with self._state.lock:
            if self._state.last_seq is None:
                seq, prev_hash = self._read_tail()
                self._state.last_seq = seq
                self._state.last_hash = prev_hash

            new_seq = self._state.last_seq + 1
            prev_hash = self._state.last_hash

            entry = {
                "trace_id": fields.pop("trace_id", None) or "trace_" + secrets.token_hex(8),
                "seq": new_seq,
                "prev_hash": prev_hash,
                "fields": _redact(fields),
            }
            entry["hash"] = _hash(entry)

            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())

            self._state.last_seq = new_seq
            self._state.last_hash = entry["hash"]
            return entry

    def verify(self) -> dict[str, Any]:
        with self._state.lock:
            lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
            errors = []
            prev = None
            for i, line in enumerate(lines, 1):
                e = json.loads(line)
                if e.get("seq") != i:
                    errors.append(f"seq:{i}")
                if e.get("prev_hash") != prev:
                    errors.append(f"prev_hash:{i}")
                body = {k: v for k, v in e.items() if k != "hash"}
                if _hash(body) != e.get("hash"):
                    errors.append(f"hash:{i}")
                serialized = json.dumps(e, ensure_ascii=False)
                if any(w in serialized.lower() and "[redacted" not in serialized.lower() for w in _SECRET_WORDS):
                    errors.append(f"secret:{i}")
                prev = e.get("hash")
            return {"entries": len(lines), "hash_chain_valid": not errors, "errors": errors}

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            if not self.path.exists():
                return None
            for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    if (
                        e.get("trace_id") == trace_id
                        or e.get("fields", {}).get("task_id") == trace_id
                        or e.get("fields", {}).get("trace_id") == trace_id
                    ):
                        return e
                except Exception:
                    continue
            return None


__all__ = ["TraceLedger"]
