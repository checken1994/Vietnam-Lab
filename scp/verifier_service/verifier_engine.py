"""Verifier engine khóa C (1.A) — 5 bước thẩm định ĐỘC LẬP trước khi ký receipt vr2.

Chạy TRONG tiến trình daemon (scp.verifier_service.daemon). Không tin claim của worker: mọi
điều kiện đọc lại từ DB kernel qua kết nối SQLite CHỈ-ĐỌC (``mode=ro`` + ``query_only``) trong
MỘT read-transaction. DB cố định lúc khởi động daemon — request KHÔNG chọn được DB.

Giao thức = phong bì L3 ``{v, id, op, auth}`` + thân/đáp của lane L1 (scp.security.receipt_authority:
``validate_receipt_request``, ``build_receipt_response``, ``build_error_response``, ``ErrorCode``) —
một schema, một bộ mã lỗi, một codec. ``reason`` là mã chi tiết của L3 kèm theo ``error_code``.
Phần mở rộng L3 ``evidence`` (chỉ profile file) mang preimage ``params`` để daemon tự tính lại
planned-action hash mà kernel đã ký vào checkpoint TRƯỚC khi dispatch.

  0. Token IPC (sai/thiếu -> UNAUTHORIZED) rồi schema (-> MALFORMED_REQUEST/PROFILE_UNSUPPORTED).
  1. Task VERIFYING; lease là active lease của ĐÚNG task (không strip), chưa release, chưa hết
     hạn, fencing mới nhất và bằng task.active_fencing_token.
  2. Không global kill; lease.global_kill_epoch == control.global_kill_epoch.
  3. Reality theo ``verifier_scope``: ``journal_integrity`` (hash chain keyed K_journal) |
     ``hands_file_postcondition`` (checkpoint keyed khớp planned action + đọc lại file vật lý).
  4. Ký vr2 qua signer do daemon giữ (codec L1 ``mint_vr2``).
  5. Ghi audit ledger JSONL (fsync) — không ghi được thì KHÔNG trả receipt (fail-closed).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scp.security.receipt_authority import (
    ErrorCode,
    ReceiptAuthorityError,
    build_error_response,
    build_receipt_response,
    validate_receipt_request,
)

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 256 * 1024
MAX_ID_LEN = 256
MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_VERIFIER_ID = "scp-verifier-daemon-v1"
DEFAULT_TTL_SECONDS = 120.0
PROFILE_JOURNAL = "journal_integrity"
PROFILE_HANDS_FILE = "hands_file_postcondition"
PROFILES = (PROFILE_JOURNAL, PROFILE_HANDS_FILE)
HANDS_FILE_ACTIONS = ("pc.write_file",)
METRIC_ISSUED = "security.key_c.receipts_issued_count"
METRIC_REFUSED = "security.key_c.receipts_refused_count"

# reason chi tiết (L3) -> error_code dùng chung (L1 ErrorCode)
REASONS: dict[str, str] = {
    "bad_json": ErrorCode.MALFORMED_REQUEST, "frame_too_large": ErrorCode.MALFORMED_REQUEST,
    "schema_invalid": ErrorCode.MALFORMED_REQUEST, "unknown_op": ErrorCode.MALFORMED_REQUEST,
    "unauthorized": ErrorCode.UNAUTHORIZED,
    "task_not_found": ErrorCode.TASK_NOT_VERIFYING, "task_not_verifying": ErrorCode.TASK_NOT_VERIFYING,
    "lease_not_found": ErrorCode.LEASE_INVALID, "lease_not_active": ErrorCode.LEASE_INVALID,
    "lease_released": ErrorCode.LEASE_INVALID, "lease_expired": ErrorCode.LEASE_INVALID,
    "lease_stale_fencing": ErrorCode.LEASE_INVALID,
    "global_kill_active": ErrorCode.KILL_SWITCH_ACTIVE, "kill_epoch_mismatch": ErrorCode.KILL_SWITCH_ACTIVE,
    "profile_unsupported": ErrorCode.PROFILE_UNSUPPORTED,
    "journal_invalid": ErrorCode.POSTCONDITION_FAILED, "evidence_invalid": ErrorCode.POSTCONDITION_FAILED,
    "postcondition_failed": ErrorCode.POSTCONDITION_FAILED,
    "db_unavailable": ErrorCode.INTERNAL_ERROR, "signing_failed": ErrorCode.INTERNAL_ERROR,
    "audit_unavailable": ErrorCode.INTERNAL_ERROR, "internal_error": ErrorCode.INTERNAL_ERROR,
}

_ENVELOPE: dict[str, type] = {"v": int, "id": str, "op": str, "auth": str}
_OP_FIELDS: dict[str, tuple[dict[str, type], dict[str, type]]] = {
    "issue": ({"request": dict}, {"evidence": dict}),
    "pubkey": ({}, {}), "health": ({}, {}), "metrics": ({}, {}),
}
_HANDS_EVIDENCE: dict[str, type] = {"action": str, "params": dict}


class EngineRefusal(Exception):
    """Từ chối có lý do (``reason`` thuộc REASONS) — không bao giờ kèm receipt."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown refusal reason {reason!r}")
        super().__init__(message)
        self.reason = reason
        self.code = REASONS[reason]
        self.message = message


def _check_fields(obj: dict[str, Any], required: dict[str, type], optional: dict[str, type], where: str) -> None:
    extra = sorted(set(obj) - set(required) - set(optional))
    if extra:
        raise EngineRefusal("schema_invalid", f"{where}: unexpected field(s) {extra}")
    for name, kind in {**required, **optional}.items():
        if name not in obj:
            if name in required:
                raise EngineRefusal("schema_invalid", f"{where}: missing field {name!r}")
            continue
        if type(obj[name]) is not kind:  # đúng kiểu tuyệt đối: bool không phải int, không ép kiểu
            raise EngineRefusal("schema_invalid", f"{where}: field {name!r} must be {kind.__name__}")


def validate_frame(obj: Any) -> dict[str, Any]:
    """Phong bì chặt + thân issue qua validator L1 + phần mở rộng evidence theo profile."""
    if not isinstance(obj, dict):
        raise EngineRefusal("schema_invalid", "frame must be a JSON object")
    op = obj.get("op")
    if not isinstance(op, str):
        raise EngineRefusal("schema_invalid", "missing string field 'op'")
    if op not in _OP_FIELDS:
        raise EngineRefusal("unknown_op", f"unknown op {op[:40]!r}")
    required, optional = _OP_FIELDS[op]
    _check_fields(obj, {**_ENVELOPE, **required}, optional, "frame")
    if obj["v"] != PROTOCOL_VERSION:
        raise EngineRefusal("schema_invalid", f"unsupported protocol version {obj['v']!r}")
    if not obj["id"] or len(obj["id"]) > MAX_ID_LEN:
        raise EngineRefusal("schema_invalid", "frame id must be 1..256 chars")
    if op != "issue":
        return obj
    try:
        request = validate_receipt_request(obj["request"])
    except ReceiptAuthorityError as exc:
        raise EngineRefusal("schema_invalid", str(exc)) from exc
    if any(len(value) > MAX_ID_LEN for key, value in request.items() if key != "evidence_ref"):
        raise EngineRefusal("schema_invalid", f"request identifiers must be <= {MAX_ID_LEN} chars")
    scope, evidence = request["verifier_scope"], obj.get("evidence")
    if scope not in PROFILES:
        raise EngineRefusal("profile_unsupported", f"verifier_scope {scope[:40]!r} has no verifier")
    if scope == PROFILE_JOURNAL:
        if evidence is not None or "checkpoint_id" in request or "execution_hash" in request:
            raise EngineRefusal("schema_invalid", "journal_integrity takes no checkpoint/execution/evidence")
    else:
        if evidence is None or "checkpoint_id" not in request:
            raise EngineRefusal("schema_invalid", "hands_file_postcondition needs checkpoint_id and evidence")
        _check_fields(evidence, _HANDS_EVIDENCE, {}, "evidence")
        if evidence["action"] not in HANDS_FILE_ACTIONS:
            raise EngineRefusal("profile_unsupported", f"action {evidence['action'][:40]!r} is not re-observable")
        params = evidence["params"]
        if type(params.get("path")) is not str or type(params.get("content")) is not str:
            raise EngineRefusal("schema_invalid", "evidence.params needs string 'path' and 'content'")
    return {**obj, "request": request}


class _ReadOnlyKernelView:
    """Đủ bề mặt cho verify_journal_impl / CheckpointEngine.validate_checkpoint — không có ghi."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _control(self) -> Any:
        return self.conn.execute("SELECT * FROM control WHERE id=1").fetchone()

    def get_events(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
        return [dict(r) for r in rows]


class VerifierEngine:
    """Thẩm định + ký. ``signer`` (do daemon tạo) là nơi DUY NHẤT giữ private key C."""

    def __init__(self, *, db_path: str | Path, signer: Any, ipc_token: str, audit_log: str | Path,
                 verifier_id: str = DEFAULT_VERIFIER_ID, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        self.db_path = Path(db_path).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(f"kernel DB not found: {self.db_path}")
        if not isinstance(ipc_token, str) or len(ipc_token) < 32:
            raise ValueError("IPC token must be >= 32 chars (no unauthenticated daemon)")
        self._token = ipc_token.encode("utf-8")
        self._signer = signer
        self.audit_log = Path(audit_log)
        self.verifier_id = verifier_id
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self.metrics: Counter[str] = Counter()

    # -- frame / dispatch -----------------------------------------------------------------
    def handle_frame(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > MAX_FRAME_BYTES:
            return self.refuse_frame("frame_too_large", f"frame > {MAX_FRAME_BYTES} bytes")
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self.refuse_frame("bad_json", "frame is not UTF-8 JSON")
        return self.handle(obj)

    def handle(self, obj: Any) -> dict[str, Any]:
        req_id = obj.get("id") if isinstance(obj, dict) and isinstance(obj.get("id"), str) else None
        try:
            self._authenticate(obj)
            frame = validate_frame(obj)
            op = frame["op"]
            if op == "issue":
                return {"v": PROTOCOL_VERSION, "id": req_id, **self._issue(frame)}
            if op == "pubkey":
                body = {"kid": self._signer.kid, "public_key": self._signer.public_key_b64,
                        "domain": self._signer.domain}
            elif op == "health":
                body = {"status": "ready", "pid": os.getpid(), "kid": self._signer.kid}
            else:
                body = {"metrics": dict(self.metrics)}
            return {"v": PROTOCOL_VERSION, "id": req_id, "success": True, **body}
        except EngineRefusal as exc:
            return self._refused(req_id, obj, exc)
        except Exception as exc:  # fail-closed: lỗi nội bộ không bao giờ thành receipt
            logger.exception("verifier internal error")
            return self._refused(req_id, obj, EngineRefusal("internal_error", type(exc).__name__))

    def refuse_frame(self, reason: str, message: str) -> dict[str, Any]:
        """Từ chối ở tầng transport (frame quá cỡ/không phải JSON, bắt tay IPC sai) — vẫn đếm."""
        return self._refused(None, None, EngineRefusal(reason, message))

    def _authenticate(self, obj: Any) -> None:
        auth = obj.get("auth") if isinstance(obj, dict) else None
        if not isinstance(auth, str) or not hmac.compare_digest(auth.encode("utf-8"), self._token):
            raise EngineRefusal("unauthorized", "missing or invalid IPC token")

    def _refused(self, req_id: Any, obj: Any, exc: EngineRefusal) -> dict[str, Any]:
        self.metrics[METRIC_REFUSED] += 1
        self.metrics[f"{METRIC_REFUSED}.{exc.reason}"] += 1
        request = obj.get("request") if isinstance(obj, dict) and obj.get("op") == "issue" else None
        if isinstance(request, dict) and exc.reason != "unauthorized":
            try:
                self._append_audit({"event": "RECEIPT_REFUSED", "error_code": exc.code, "reason": exc.reason,
                                    "task_id": str(request.get("task_id"))[:MAX_ID_LEN],
                                    "lease_id": str(request.get("lease_id"))[:MAX_ID_LEN]})
            except OSError as audit_exc:  # từ chối vẫn là từ chối; chỉ mất vết audit
                logger.error("verifier audit append failed for refusal: %s", audit_exc)
        return {"v": PROTOCOL_VERSION, "id": req_id, **build_error_response(exc.code, exc.message),
                "reason": exc.reason}

    # -- 5 bước -----------------------------------------------------------------------------
    def _issue(self, frame: dict[str, Any]) -> dict[str, Any]:
        request = frame["request"]
        task_id, lease_id, scope = request["task_id"], request["lease_id"], request["verifier_scope"]
        now = self._clock()
        conn = self._open_readonly()
        try:
            conn.execute("BEGIN")  # một snapshot cho cả 3 bước đọc
            lease = self._step1_task_and_lease(conn, task_id, lease_id, now)
            self._step2_kill(conn, lease)
            if scope == PROFILE_JOURNAL:
                evidence_ref = self._step3_journal(conn, task_id)
            else:
                evidence_ref = self._step3_hands_file(conn, request, lease, frame["evidence"])
        finally:
            conn.close()
        try:  # bước 4
            token, claims = self._signer.mint(
                task_id=task_id, attempt_id=str(lease["attempt_id"]), lease_id=lease_id,
                fencing_token=int(lease["fencing_token"]), verifier_id=self.verifier_id, profile=scope,
                evidence_ref=evidence_ref, ttl_seconds=self.ttl_seconds, now=now)
        except Exception as exc:
            logger.exception("vr2 minting failed")
            raise EngineRefusal("signing_failed", type(exc).__name__) from exc
        try:  # bước 5
            self._append_audit({"event": "RECEIPT_ISSUED", "task_id": task_id, "lease_id": lease_id,
                                "attempt_id": claims["attempt_id"], "fencing_token": claims["fencing_token"],
                                "profile": scope, "kid": claims["kid"], "jti": claims["jti"],
                                "evidence_ref": evidence_ref, "claimed_evidence_ref": request["evidence_ref"],
                                "iat": claims["iat"], "exp": claims["exp"]})
        except OSError as exc:
            raise EngineRefusal("audit_unavailable", "audit ledger not writable; receipt withheld") from exc
        self.metrics[METRIC_ISSUED] += 1
        response = build_receipt_response(token)
        response["audit_trail"].update({"verified_checks": 5, "daemon_pid": os.getpid(), "profile": scope})
        return response

    def _open_readonly(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=5.0,
                                   isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            return conn
        except sqlite3.Error as exc:
            raise EngineRefusal("db_unavailable", f"kernel DB not readable: {type(exc).__name__}") from exc

    @staticmethod
    def _step1_task_and_lease(conn: sqlite3.Connection, task_id: str, lease_id: str, now: float) -> Any:
        task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            raise EngineRefusal("task_not_found", "task does not exist in kernel DB")
        if task["state"] != "VERIFYING":
            raise EngineRefusal("task_not_verifying", f"task state is {task['state']}, not VERIFYING")
        lease = conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        if lease is None:
            raise EngineRefusal("lease_not_found", "lease does not exist in kernel DB")
        if lease["task_id"] != task_id or task["active_lease_id"] != lease_id:
            raise EngineRefusal("lease_not_active", "lease is not the active lease of this exact task")
        if int(lease["released"]):
            raise EngineRefusal("lease_released", "lease already released")
        if float(lease["expires_at"]) <= now:
            raise EngineRefusal("lease_expired", "lease expired")
        latest = conn.execute("SELECT COALESCE(MAX(fencing_token),0) AS n FROM leases WHERE task_id=?",
                              (task_id,)).fetchone()["n"]
        fencing = int(lease["fencing_token"])
        if fencing != int(latest) or int(task["active_fencing_token"]) != fencing:
            raise EngineRefusal("lease_stale_fencing", "lease fencing token is not the latest")
        return lease

    @staticmethod
    def _step2_kill(conn: sqlite3.Connection, lease: Any) -> None:
        control = conn.execute("SELECT * FROM control WHERE id=1").fetchone()
        if control is None:
            raise EngineRefusal("db_unavailable", "kernel control row missing")
        if int(control["global_kill"]):
            raise EngineRefusal("global_kill_active", "global kill switch is active")
        if int(lease["global_kill_epoch"]) != int(control["global_kill_epoch"]):
            raise EngineRefusal("kill_epoch_mismatch", "lease predates the current global kill epoch")

    @staticmethod
    def _step3_journal(conn: sqlite3.Connection, task_id: str) -> str:
        from scp.task_kernel_parts.definitions import KernelError
        from scp.task_kernel_parts.lease_manager import verify_journal_impl

        try:
            result = verify_journal_impl(_ReadOnlyKernelView(conn), task_id)
        except KernelError as exc:
            raise EngineRefusal("journal_invalid", f"journal not verifiable: {type(exc).__name__}") from exc
        if not result.get("hash_chain_valid") or int(result.get("event_count") or 0) <= 0:
            raise EngineRefusal("journal_invalid", f"hash chain invalid: {list(result.get('errors', []))[:5]}")
        if result.get("legacy_unkeyed") or result.get("capability_key_events"):
            raise EngineRefusal("journal_invalid", "chain is not keyed under the current K_journal")
        head = conn.execute("SELECT event_hash FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1",
                            (task_id,)).fetchone()["event_hash"]
        return f"evidence://scp-verifier/{PROFILE_JOURNAL}/{head}"

    @staticmethod
    def _step3_hands_file(conn: sqlite3.Connection, request: dict[str, str], lease: Any,
                          evidence: dict[str, Any]) -> str:
        from scp.hands.task_kernel_bridge import TaskKernelHandsBridge
        from scp.task_kernel_parts.checkpoint import CheckpointEngine
        from scp.task_kernel_parts.definitions import KernelError, compute_keyed_hash

        cp_id, action, params = request["checkpoint_id"], evidence["action"], evidence["params"]
        row = conn.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (cp_id,)).fetchone()
        if (row is None or row["task_id"] != request["task_id"] or row["attempt_id"] != lease["attempt_id"]
                or row["step_id"] != action):
            raise EngineRefusal("evidence_invalid", "checkpoint is not this task/attempt/action")
        planned = TaskKernelHandsBridge._planned_action(action, params)
        try:
            CheckpointEngine(_ReadOnlyKernelView(conn)).validate_checkpoint(cp_id, planned)
            keyed = compute_keyed_hash(planned, domain="checkpoint")
        except KernelError as exc:
            raise EngineRefusal("evidence_invalid", f"planned action does not match checkpoint: {exc}") from exc
        if not hmac.compare_digest(str(row["planned_action_hash"]), keyed):
            raise EngineRefusal("evidence_invalid", "checkpoint not keyed under the current K_journal")
        path = Path(params["path"])
        if not path.is_absolute():
            raise EngineRefusal("evidence_invalid", "file postcondition needs an absolute path")
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_EVIDENCE_FILE_BYTES + 1)
        except OSError as exc:
            raise EngineRefusal("postcondition_failed", f"file not readable: {type(exc).__name__}") from exc
        if len(data) > MAX_EVIDENCE_FILE_BYTES:
            raise EngineRefusal("postcondition_failed", "file larger than verifier limit")
        observed = "sha256:" + hashlib.sha256(data).hexdigest()
        expected = "sha256:" + hashlib.sha256(params["content"].encode("utf-8")).hexdigest()
        if not hmac.compare_digest(observed, expected):
            raise EngineRefusal("postcondition_failed", "file content differs from the planned write")
        claimed = request.get("execution_hash")
        if claimed is not None and not hmac.compare_digest(claimed, observed):
            raise EngineRefusal("postcondition_failed", "execution_hash differs from the observed file")
        return f"evidence://scp-verifier/{PROFILE_HANDS_FILE}/{cp_id}/{observed}"

    def _append_audit(self, record: dict[str, Any]) -> None:
        line = json.dumps({"ts": self._clock(), "pid": os.getpid(), **record}, sort_keys=True, ensure_ascii=False)
        with self.audit_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
