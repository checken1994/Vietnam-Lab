"""Human Confirmation Store for Level >= 3 and Mutating Actions.

Prevents self-attestation of approval in plan definitions. Any action requiring
Level >= 3 execution must be confirmed by an explicit operator/human approval
recorded in this immutable-append store or provided via caller credential.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("scp.security.confirmation_store")


class HumanConfirmationStore:
    """Thread-safe persistent store for human confirmations of high-capability actions."""

    DEFAULT_TTL_SECONDS = 3600.0  # 1 hour

    def __init__(self, store_path: Path | str | None = None) -> None:
        if store_path:
            self.store_path = Path(store_path)
        else:
            data_dir = Path(os.environ.get("SCP_DATA_DIR", "data"))
            self.store_path = data_dir / "human_confirmations.jsonl"
        self._lock = threading.Lock()
        self._memory_cache: dict[str, dict[str, Any]] = {}
        self._load_cache()

    @staticmethod
    def _target_hash(target: str) -> str:
        return hashlib.sha256((target or "").strip().encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        with self._lock:
            if not self.store_path.exists():
                return
            try:
                for line in self.store_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        cid = record.get("confirmation_id")
                        if cid:
                            self._memory_cache[cid] = record
                    except json.JSONDecodeError:
                        continue
            except OSError as exc:
                logger.warning("HumanConfirmationStore: failed to read cache from %s: %s", self.store_path, exc)

    def record_confirmation(
        self,
        action: str,
        target: str,
        user: str = "operator",
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a human operator confirmation for an action/target."""
        cid = f"conf-{uuid.uuid4().hex[:16]}"
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS
        record = {
            "confirmation_id": cid,
            "action": action,
            "target": str(target)[:1000],
            "target_hash": self._target_hash(target),
            "user": user,
            "timestamp": now,
            "expires_at": now + ttl,
            "status": "CONFIRMED",
            "metadata": metadata or {},
        }
        with self._lock:
            self._memory_cache[cid] = record
            try:
                self.store_path.parent.mkdir(parents=True, exist_ok=True)
                with self.store_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
            except OSError as exc:
                logger.warning("HumanConfirmationStore: write failed for %s: %s", cid, exc)
        return cid

    def is_confirmed(
        self,
        action: str,
        target: str = "",
        confirmation_id: str | None = None,
    ) -> bool:
        """Check whether an action is authorized by an active unexpired confirmation.

        [AUDIT-FIX 2026-09-24] Fail-closed on empty target: previously an
        empty `target` degenerated the lookup to action-only matching, so a
        confirmation recorded for ANY target of that action authorized every
        other target. Now `target=""` (without a confirmation_id) returns
        False. The confirmation_id-based override is preserved: a caller
        presenting a concrete confirmation_id proves possession of a specific
        operator record, so target match is enforced only when a target is
        supplied alongside it.
        """
        now = time.time()
        with self._lock:
            if confirmation_id:
                rec = self._memory_cache.get(confirmation_id)
                if rec and rec.get("status") == "CONFIRMED" and rec.get("expires_at", 0) > now:
                    if rec.get("action") == action:
                        if not target or rec.get("target_hash") == self._target_hash(target):
                            return True
                return False

            # Action + target lookup — empty target can never match (fail-closed).
            if not target:
                logger.warning(
                    "HumanConfirmationStore.is_confirmed: empty target for action '%s' — denied (fail-closed)",
                    action,
                )
                return False
            t_hash = self._target_hash(target)
            for rec in self._memory_cache.values():
                if (
                    rec.get("status") == "CONFIRMED"
                    and rec.get("expires_at", 0) > now
                    and rec.get("action") == action
                ):
                    if rec.get("target_hash") == t_hash:
                        return True
            return False

    def revoke(self, confirmation_id: str, reason: str = "operator_revoked") -> bool:
        """Revoke an active confirmation."""
        now = time.time()
        with self._lock:
            rec = self._memory_cache.get(confirmation_id)
            if not rec:
                return False
            rec["status"] = "REVOKED"
            rec["revoked_at"] = now
            rec["revoke_reason"] = reason
            try:
                with self.store_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    handle.flush()
            except OSError as exc:
                logger.warning("HumanConfirmationStore: revoke append failed for %s: %s", confirmation_id, exc)
            return True


_GLOBAL_CONFIRMATION_STORE: HumanConfirmationStore | None = None
_STORE_LOCK = threading.Lock()


def get_confirmation_store() -> HumanConfirmationStore:
    global _GLOBAL_CONFIRMATION_STORE
    with _STORE_LOCK:
        if _GLOBAL_CONFIRMATION_STORE is None:
            _GLOBAL_CONFIRMATION_STORE = HumanConfirmationStore()
        return _GLOBAL_CONFIRMATION_STORE
