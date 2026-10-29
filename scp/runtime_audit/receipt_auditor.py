"""Key C lane L6 — offline receipt auditor (GĐ2 acceptance condition, checker-keyc-merge directive 8).

Vì sao cần: K_journal là khóa ĐỐI XỨNG nên kẻ tấn công trong tiến trình kernel giữ K_journal ký lại
được floor/journal (keyc-1A §5 rủi ro 6). Bảo đảm thật là một lượt kiểm OFFLINE, chỉ dùng PUBLIC
keyring của verifier, quét mọi COMPLETED: giả một receipt ``vr2`` cần private key C, vốn chỉ nằm ở
verifier daemon.

Đầu vào: file SQLite của kernel + file PUBLIC keyring. KHÔNG BAO GIỜ nhận private key, K_journal,
``.env``; module này không đọc ``os.environ`` (không gọi ``load_keyring``/``resolve``).

Chỉ đọc: mở bằng URI SQLite ``mode=ro&immutable=1``, KHÔNG khởi tạo TaskKernel (``__init__`` của nó
chạy migration và ghi). Chỉ ``mode=ro`` là chưa đủ: với DB ở chế độ WAL (chế độ của kernel), SQLite
3.53 vẫn tạo và để lại ``-wal``/``-shm`` cho kết nối read-only (đã probe). ``immutable=1`` không ghi gì
nhưng sẽ lặng lẽ bỏ qua frame trong WAL, nên DB đang có ``-wal``/``-journal`` bị TỪ CHỐI (đang chạy
hoặc chưa checkpoint: hãy kiểm một bản sao đã checkpoint). sha256 + tập file phụ được đo lại sau khi
đọc; có thay đổi thì verdict là UNKNOWN.

PASS chỉ có nghĩa "không quan sát thấy lỗi trong snapshot DB này với keyring này" (PASS ≠ TRUE).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scp.security import receipt_authority as ra

REPORT_SCHEMA = "scp.receipt_offline_audit.v1"
SEMANTICS = (
    "PASS means only that no failure was observed in this DB snapshot with the supplied public keyring. "
    "It is not proof that every completion is genuine (SCP rule: PASS != TRUE)."
)
VERIFIED_KEY_C = ra.PROVENANCE_KEY_C
SELF_ATTESTED_HMAC = ra.PROVENANCE_SELF_ATTESTED
INVALID = "INVALID"
MISSING_RECEIPT = "MISSING_RECEIPT"
CLASSIFICATIONS = (VERIFIED_KEY_C, SELF_ATTESTED_HMAC, INVALID, MISSING_RECEIPT)
VERDICT_PASS, VERDICT_FAIL, VERDICT_UNKNOWN = "PASS", "FAIL", "UNKNOWN"
EXIT_CODES = {VERDICT_PASS: 0, VERDICT_FAIL: 1, VERDICT_UNKNOWN: 2}
# Tên event do commit_approval.py ghi (L2a); test giữ hai bên khớp nhau.
COMPLETED_EVENT = "TASK_COMPLETED"
KEYRING_LOADED_EVENT = "RECEIPT_KEYRING_LOADED"
GLOBAL_STREAM = "__global__"
OPEN_URI_PARAMS = "mode=ro&immutable=1"
_LIVE_SIDECARS = ("-wal", "-journal")
_ALL_SIDECARS = ("-wal", "-shm", "-journal")
_KEY_C_REQUIRED = (
    "receipt_mode", "receipt_kid", "receipt_jti", "receipt_token", "receipt_keyring_digest",
    "lease_id", "attempt_id", "fencing_token", "verifier_id", "evidence_ref", "verifier_verdict",
)
_PRIVATE_FIELD_RE = re.compile(r"priv|secret|seed|^d$|^sk$", re.IGNORECASE)
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
LIMITS = (
    "HMAC (SELF_ATTESTED_HMAC) receipts cannot be verified offline: that needs the symmetric secret, which this tool never takes.",
    "The journal hash chain is not verified: that needs K_journal, which this tool never takes.",
    "events.created_at is not covered by the journal hash: an attacker with DB write access can back-date a "
    "SELF_ATTESTED/MISSING completion to before a floor; for a post-GD2 DB use --since 0.",
    "Journal floor detection trusts enforce markers written by the kernel; stripping them hides the floor, "
    "so supply --since from an out-of-band record of the cutover.",
    "The verdict covers only this DB file snapshot and this keyring; the keyring must be the trusted public archive.",
)


class AuditCode:
    """Mã của auditor, bổ sung cho ``receipt_authority.ErrorCode``."""

    MALFORMED_PAYLOAD = "MALFORMED_PAYLOAD"
    MISSING_PAYLOAD_FIELD = "MISSING_PAYLOAD_FIELD"
    UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    SELF_ATTESTED_IN_ENFORCE = "SELF_ATTESTED_IN_ENFORCE"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    LEASE_ROW_MISSING = "LEASE_ROW_MISSING"
    NO_RECEIPT_PAYLOAD = "NO_RECEIPT_PAYLOAD"
    COMPLETION_EVENT_MISSING = "COMPLETION_EVENT_MISSING"
    DUPLICATE_COMPLETION_EVENT = "DUPLICATE_COMPLETION_EVENT"
    TASK_STATE_MISMATCH = "TASK_STATE_MISMATCH"
    SELF_ATTESTED_AFTER_FLOOR = "SELF_ATTESTED_AFTER_FLOOR"
    MISSING_RECEIPT_AFTER_FLOOR = "MISSING_RECEIPT_AFTER_FLOOR"
    # Đầu vào bị từ chối (không có verdict, exit 2).
    PRIVATE_KEY_INPUT_REFUSED = "PRIVATE_KEY_INPUT_REFUSED"
    ENV_INPUT_REFUSED = "ENV_INPUT_REFUSED"
    KEYRING_UNAVAILABLE = ra.ErrorCode.KEYRING_UNAVAILABLE
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    DB_LIVE_SIDECAR = "DB_LIVE_SIDECAR"
    NOT_A_KERNEL_DB = "NOT_A_KERNEL_DB"
    BAD_SINCE = "BAD_SINCE"
    SNAPSHOT_CHANGED = "SNAPSHOT_CHANGED"


_E, _A = ra.ErrorCode, AuditCode
_CATEGORY = {
    **dict.fromkeys((_E.BAD_SIGNATURE, _E.MALFORMED_TOKEN, _E.WRONG_TOKEN_TYPE), "signature"),
    **dict.fromkeys((_E.UNKNOWN_KID, _E.KID_REVOKED, _E.KID_EXPIRED), "key"),
    **dict.fromkeys((_E.BAD_VALIDITY_WINDOW, _E.RECEIPT_NOT_YET_VALID, _E.RECEIPT_EXPIRED), "validity"),
    _E.VERDICT_NOT_VERIFIED: "verdict",
    **dict.fromkeys(
        (_E.TASK_MISMATCH, _E.LEASE_MISMATCH, _E.ATTEMPT_MISMATCH, _E.FENCING_MISMATCH, _A.LEASE_ROW_MISSING),
        "binding",
    ),
    **dict.fromkeys((_E.CARRIER_MISMATCH, _A.MALFORMED_PAYLOAD, _A.MISSING_PAYLOAD_FIELD, _A.UNKNOWN_PROVENANCE,
                     _A.PROVENANCE_MISMATCH, _A.SELF_ATTESTED_IN_ENFORCE, _A.MALFORMED_TIMESTAMP), "payload"),
    **dict.fromkeys((_A.COMPLETION_EVENT_MISSING, _A.DUPLICATE_COMPLETION_EVENT, _A.TASK_STATE_MISMATCH), "journal"),
    **dict.fromkeys((_A.SELF_ATTESTED_AFTER_FLOOR, _A.MISSING_RECEIPT_AFTER_FLOOR), "floor"),
}


class AuditInputError(ValueError):
    """Đầu vào bị từ chối fail-closed; không có verdict (exit 2)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class _Mismatch(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# ---------------------------------------------------------------- inputs (keyring, DB, time)

def parse_timestamp(value: Any) -> float | None:
    """Epoch giây từ số, chuỗi số hoặc ISO-8601 (không có múi giờ → UTC). Không đọc được → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        return number if math.isfinite(number) else None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _is_bare_key_material(text: str) -> bool:
    """Base64/hex trần dài 32 hoặc 64 byte: seed private Ed25519 không phân biệt được với public key."""
    if _HEX_RE.fullmatch(text) and len(text) in (64, 128):
        return True
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decode(text + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(raw) in (32, 64):
            return True
    return False


def _json_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _json_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _json_keys(value)


def _refuse_private_material(path: Path, text: str) -> None:
    name = path.name.lower()
    if name.startswith(".env"):
        raise AuditInputError(AuditCode.ENV_INPUT_REFUSED, "a .env file is never an auditor input")
    if "private" in name:
        raise AuditInputError(AuditCode.PRIVATE_KEY_INPUT_REFUSED, "the keyring file name says it is a private key")
    if "PRIVATE KEY" in text.upper():
        raise AuditInputError(AuditCode.PRIVATE_KEY_INPUT_REFUSED, "the keyring file contains PEM private key material")
    stripped = text.strip()
    try:
        doc = json.loads(stripped)
    except ValueError:
        if _is_bare_key_material(stripped):
            raise AuditInputError(
                AuditCode.PRIVATE_KEY_INPUT_REFUSED,
                "bare key material is refused (an Ed25519 private seed looks like a public key); supply the JSON public keyring",
            ) from None
        return  # parse_keyring từ chối văn bản không phải JSON
    for key in _json_keys(doc):
        if _PRIVATE_FIELD_RE.search(key):
            raise AuditInputError(AuditCode.PRIVATE_KEY_INPUT_REFUSED, f"the keyring carries a private-looking field {key!r}")


def load_public_keyring(path: str | Path) -> ra.Keyring:
    """Nạp PUBLIC keyring từ file (không bao giờ từ env). Private key / .env / keyring hỏng → AuditInputError."""
    keyring_path = Path(path)
    try:
        raw = keyring_path.read_bytes()
    except OSError as exc:
        raise AuditInputError(AuditCode.KEYRING_UNAVAILABLE, f"cannot read the keyring file ({type(exc).__name__})") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AuditInputError(AuditCode.KEYRING_UNAVAILABLE, "the keyring file is not UTF-8") from exc
    _refuse_private_material(keyring_path, text)
    try:
        return ra.parse_keyring(text, forbidden_public_keys=())
    except ra.ReceiptAuthorityError as exc:
        raise AuditInputError(AuditCode.KEYRING_UNAVAILABLE, str(exc)) from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def sidecars(db_path: Path) -> list[str]:
    return [suffix for suffix in _ALL_SIDECARS if Path(f"{db_path}{suffix}").exists()]


def open_snapshot(db_path: str | Path) -> sqlite3.Connection:
    """Kết nối chỉ đọc, không tạo file phụ; DB đang có ``-wal``/``-journal`` bị từ chối."""
    path = Path(db_path)
    if not path.is_file():
        raise AuditInputError(AuditCode.DB_UNAVAILABLE, "the kernel DB path is not a file")
    live = [suffix for suffix in _LIVE_SIDECARS if Path(f"{path}{suffix}").exists()]
    if live:
        raise AuditInputError(
            AuditCode.DB_LIVE_SIDECAR,
            f"the DB has {live} sidecar(s): it is live or not checkpointed; audit a checkpointed copy",
        )
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?{OPEN_URI_PARAMS}", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        raise AuditInputError(AuditCode.DB_UNAVAILABLE, f"cannot open the DB read-only ({type(exc).__name__})") from exc
    if not {"tasks", "events", "leases"} <= tables:
        conn.close()
        raise AuditInputError(AuditCode.NOT_A_KERNEL_DB, "the DB has no tasks/events/leases tables")
    return conn


# --------------------------------------------------------------------- classification

def _finding(classification: str, code: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    return {"classification": classification, "code": code, "category": _CATEGORY.get(code), "reason": reason, **extra}


def _check_journal_binding(signed: dict[str, Any], task_id: str, payload: dict[str, Any]) -> None:
    """Token phải bind ĐÚNG task/lease/attempt/fencing mà hàng journal ghi (so khớp chính xác)."""
    if signed["task_id"] != task_id:
        raise _Mismatch(ra.ErrorCode.TASK_MISMATCH, "the receipt is bound to another task id")
    if signed["lease_id"] != payload["lease_id"]:
        raise _Mismatch(ra.ErrorCode.LEASE_MISMATCH, "the receipt lease differs from the journal row")
    if signed["attempt_id"] != payload["attempt_id"]:
        raise _Mismatch(ra.ErrorCode.ATTEMPT_MISMATCH, "the receipt attempt differs from the journal row")
    fencing = payload["fencing_token"]
    if type(fencing) is not int or fencing != signed["fencing_token"]:
        raise _Mismatch(ra.ErrorCode.FENCING_MISMATCH, "the receipt fencing token differs from the journal row")


def _check_lease_row(signed: dict[str, Any], task_id: str, lease_row: Any) -> None:
    """Lease mà token bind phải tồn tại trong bảng leases, thuộc đúng task, attempt, fencing."""
    if lease_row is None:
        raise _Mismatch(AuditCode.LEASE_ROW_MISSING, "the bound lease has no row in the leases table")
    if lease_row["task_id"] != task_id:
        raise _Mismatch(ra.ErrorCode.LEASE_MISMATCH, "the bound lease row belongs to another task")
    if lease_row["attempt_id"] != signed["attempt_id"]:
        raise _Mismatch(ra.ErrorCode.ATTEMPT_MISMATCH, "the bound lease row has another attempt")
    fencing = lease_row["fencing_token"]
    if type(fencing) is not int or fencing != signed["fencing_token"]:
        raise _Mismatch(ra.ErrorCode.FENCING_MISMATCH, "the bound lease row has another fencing token")


def _check_journal_claims(signed: dict[str, Any], payload: dict[str, Any], token: str) -> None:
    """Các trường journal ghi lại từ receipt phải bằng đúng payload đã ký."""
    claims = (
        ("receipt_kid", signed["kid"]), ("receipt_jti", signed["jti"]), ("verifier_id", signed["verifier_id"]),
        ("evidence_ref", signed["evidence_ref"]), ("verifier_verdict", signed["verdict"]),
    )
    for field, expected in claims:
        if payload[field] != expected:
            raise _Mismatch(ra.ErrorCode.CARRIER_MISMATCH, f"journal {field} differs from the signed receipt")
    issued_at = payload.get("issued_at")
    if issued_at is not None and parse_timestamp(issued_at) != float(signed["iat"]):
        raise _Mismatch(ra.ErrorCode.CARRIER_MISMATCH, "journal issued_at differs from the signed receipt")
    digest = payload.get("receipt_signature_sha256")
    if digest is not None and digest != "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest():
        raise _Mismatch(ra.ErrorCode.CARRIER_MISMATCH, "journal receipt_signature_sha256 is not the token's sha256")


def classify_completion(task_id: str, payload: Any, completed_at: float | None, keyring: ra.Keyring,
                        lease_row_for) -> dict[str, Any]:
    """Phân loại MỘT event COMPLETED: VERIFIED_KEY_C / SELF_ATTESTED_HMAC / INVALID / MISSING_RECEIPT."""
    if not isinstance(payload, dict):
        return _finding(INVALID, AuditCode.MALFORMED_PAYLOAD, "the TASK_COMPLETED payload is not a JSON object")
    if "receipt_provenance" not in payload:
        return _finding(MISSING_RECEIPT, AuditCode.NO_RECEIPT_PAYLOAD, "COMPLETED without receipt fields (e.g. pre-L2a)")
    provenance, mode = payload["receipt_provenance"], payload.get("receipt_mode")
    if provenance == SELF_ATTESTED_HMAC:
        if "receipt_token" not in payload or "receipt_mode" not in payload:
            return _finding(INVALID, AuditCode.MISSING_PAYLOAD_FIELD, "self-attested row lacks receipt_token/receipt_mode")
        if payload["receipt_token"] is not None:
            return _finding(INVALID, AuditCode.PROVENANCE_MISMATCH, "a self-attested row carries a vr2 token")
        if mode == ra.MODE_ENFORCE:
            return _finding(INVALID, AuditCode.SELF_ATTESTED_IN_ENFORCE, "enforce mode never accepts an HMAC receipt")
        if mode != ra.MODE_AUDIT:
            return _finding(INVALID, AuditCode.MALFORMED_PAYLOAD, "receipt_mode must be audit or enforce")
        return _finding(SELF_ATTESTED_HMAC, None, "HMAC receipt accepted in audit mode; not verifiable offline", mode=mode)
    if provenance != VERIFIED_KEY_C:
        return _finding(INVALID, AuditCode.UNKNOWN_PROVENANCE, "receipt_provenance is not a known value")
    missing = [field for field in _KEY_C_REQUIRED if field not in payload]
    token = payload.get("receipt_token")
    if missing or not isinstance(token, str):
        return _finding(INVALID, AuditCode.MISSING_PAYLOAD_FIELD, f"Key C row lacks fields {missing or ['receipt_token']}")
    if mode not in ra.MODES:
        return _finding(INVALID, AuditCode.MALFORMED_PAYLOAD, "receipt_mode must be audit or enforce")
    if completed_at is None:
        return _finding(INVALID, AuditCode.MALFORMED_TIMESTAMP, "the completion event time is unreadable")
    try:
        signed = ra.verify_vr2(token, keyring, now=completed_at)
        _check_journal_binding(signed, task_id, payload)
        _check_lease_row(signed, task_id, lease_row_for(signed["lease_id"]))
        _check_journal_claims(signed, payload, token)
    except ra.ReceiptAuthorityError as exc:
        return _finding(INVALID, exc.code, str(exc))
    except _Mismatch as exc:
        return _finding(INVALID, exc.code, str(exc))
    return _finding(VERIFIED_KEY_C, None, "vr2 verified offline with the public keyring and bound to the journal row",
                    mode=mode, kid=signed["kid"], jti=signed["jti"])


def _load_payload(text: Any) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _journal_floor(conn: sqlite3.Connection, completion_rows: list[Any]) -> tuple[float | None, list[str]]:
    """Mốc enforce sớm nhất thấy trong journal (TASK_COMPLETED / RECEIPT_KEYRING_LOADED có receipt_mode=enforce).

    Marker enforce mà thời điểm không đọc được → floor = -inf (mọi completion đều phải là Key C).
    """
    keyring_rows = conn.execute(
        "SELECT event_id, created_at, payload_json FROM events WHERE task_id=? AND type=?",
        (GLOBAL_STREAM, KEYRING_LOADED_EVENT),
    ).fetchall()
    floor, evidence = None, []
    for row in [*completion_rows, *keyring_rows]:
        payload = _load_payload(row["payload_json"])
        if not (isinstance(payload, dict) and payload.get("receipt_mode") == ra.MODE_ENFORCE):
            continue
        moment = parse_timestamp(row["created_at"])
        moment = -math.inf if moment is None else moment
        if floor is None or moment < floor:
            floor, evidence = moment, [row["event_id"]]
    return floor, evidence


def _after_floor(completed_at: float | None, floor: float | None) -> bool:
    if floor is None:
        return False
    return completed_at is None or completed_at >= floor


def _finite(moment: float | None) -> float | None:
    return moment if moment is not None and math.isfinite(moment) else None


def _iso(moment: float | None) -> str | None:
    if moment is None or not math.isfinite(moment):
        return None
    return datetime.fromtimestamp(moment, tz=timezone.utc).isoformat()


def _audit_rows(conn: sqlite3.Connection, keyring: ra.Keyring, since: float | None) -> dict[str, Any]:
    completed = {row["task_id"]: row["state"] for row in conn.execute("SELECT task_id, state FROM tasks WHERE state='COMPLETED'")}
    completion_rows = conn.execute(
        "SELECT event_id, task_id, seq, type, to_state, payload_json, created_at FROM events "
        "WHERE (type=? OR to_state='COMPLETED') AND task_id<>? ORDER BY task_id, seq",
        (COMPLETED_EVENT, GLOBAL_STREAM),
    ).fetchall()
    by_task: dict[str, list[Any]] = {}
    for row in completion_rows:
        by_task.setdefault(row["task_id"], []).append(row)

    def lease_row_for(lease_id: str) -> Any:
        return conn.execute("SELECT task_id, attempt_id, fencing_token FROM leases WHERE lease_id=?", (lease_id,)).fetchone()

    def task_state(task_id: str) -> str | None:
        if task_id in completed:
            return completed[task_id]
        row = conn.execute("SELECT state FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return None if row is None else row["state"]

    journal_floor, floor_evidence = _journal_floor(conn, completion_rows)
    known = [moment for moment in (since, journal_floor) if moment is not None]
    floor = min(known) if known else None
    findings = []
    for task_id in sorted(set(completed) | set(by_task)):
        events, state = by_task.get(task_id, []), task_state(task_id)
        completed_at = parse_timestamp(events[0]["created_at"]) if len(events) == 1 else None
        base = {"task_id": task_id, "task_state": state, "event_ids": [row["event_id"] for row in events],
                "completed_at": events[0]["created_at"] if len(events) == 1 else None}
        if not events:
            result = _finding(INVALID, AuditCode.COMPLETION_EVENT_MISSING, "task is COMPLETED but has no TASK_COMPLETED event")
        elif len(events) > 1:
            result = _finding(INVALID, AuditCode.DUPLICATE_COMPLETION_EVENT, "task has more than one completion event")
        elif state is not None and state != "COMPLETED":
            result = _finding(INVALID, AuditCode.TASK_STATE_MISMATCH, f"a completion event exists but the task is {state}")
        else:
            result = classify_completion(task_id, _load_payload(events[0]["payload_json"]), completed_at, keyring, lease_row_for)
        after = _after_floor(completed_at, floor)
        if result["classification"] == SELF_ATTESTED_HMAC and after:
            result.update(code=AuditCode.SELF_ATTESTED_AFTER_FLOOR, category="floor",
                          reason="self-attested HMAC completion at or after the enforce floor")
        if result["classification"] == MISSING_RECEIPT and after:
            result.update(code=AuditCode.MISSING_RECEIPT_AFTER_FLOOR, category="floor",
                          reason="completion without a receipt at or after the enforce floor")
        fails = result["classification"] == INVALID or (
            after and result["classification"] in (SELF_ATTESTED_HMAC, MISSING_RECEIPT))
        findings.append({**base, **result, "after_floor": after, "fails": fails})
    counts = {name: sum(1 for f in findings if f["classification"] == name) for name in CLASSIFICATIONS}
    counts.update(completed_tasks=len(completed), completion_events=len(completion_rows),
                  audited_tasks=len(findings), failing=sum(1 for f in findings if f["fails"]))
    return {
        "floor": {
            "since_arg": since, "journal_enforce_first_seen": _finite(journal_floor),
            "journal_evidence_event_ids": floor_evidence, "effective": _finite(floor), "effective_iso": _iso(floor),
            # Marker enforce không rõ thời điểm → floor không có cận dưới: mọi completion đều phải là Key C.
            "unbounded": floor == -math.inf,
            "source": ("cli+journal" if since is not None and journal_floor is not None
                       else "cli" if since is not None else "journal" if journal_floor is not None else "none"),
        },
        "counts": counts,
        "failing_task_ids": [f["task_id"] for f in findings if f["fails"]],
        "findings": findings,
    }


def audit_database(db_path: str | Path, keyring_path: str | Path, *, since: Any = None) -> dict[str, Any]:
    """Kiểm offline mọi COMPLETED trong ``db_path`` bằng PUBLIC keyring ``keyring_path``; trả report JSON.

    ``since``: mốc floor (epoch hoặc ISO-8601) từ nguồn ngoài; floor hiệu lực = min(since, mốc enforce trong journal).
    Đầu vào bị từ chối → AuditInputError.
    """
    since_value = None
    if since is not None:
        since_value = parse_timestamp(since)
        if since_value is None:
            raise AuditInputError(AuditCode.BAD_SINCE, "--since must be epoch seconds or ISO-8601")
    keyring = load_public_keyring(keyring_path)
    path = Path(db_path)
    if not path.is_file():
        raise AuditInputError(AuditCode.DB_UNAVAILABLE, "the kernel DB path is not a file")
    sha_before, sidecars_before = file_sha256(path), sidecars(path)
    conn = open_snapshot(path)
    try:
        body = _audit_rows(conn, keyring, since_value)
    finally:
        conn.close()
    sha_after, sidecars_after = file_sha256(path), sidecars(path)
    unchanged = sha_before == sha_after and sidecars_before == sidecars_after
    verdict = VERDICT_UNKNOWN if not unchanged else VERDICT_FAIL if body["failing_task_ids"] else VERDICT_PASS
    return {
        "schema": REPORT_SCHEMA,
        "semantics": SEMANTICS,
        "verdict": verdict,
        "verdict_code": None if unchanged else AuditCode.SNAPSHOT_CHANGED,
        "db": {"path": str(path.resolve()), "open": f"sqlite uri {OPEN_URI_PARAMS}", "sha256_before": sha_before,
               "sha256_after": sha_after, "sidecars_before": sidecars_before, "sidecars_after": sidecars_after,
               "snapshot_unchanged": unchanged},
        "keyring": {"path": str(Path(keyring_path).resolve()), "digest": keyring.digest,
                    "keys": [{"kid": e.kid, "status": e.status, "not_after": e.not_after} for e in keyring.entries]},
        **body,
        "limits": list(LIMITS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="receipt_offline_auditor",
        description="Offline Key C receipt auditor: re-verify every COMPLETED with only the PUBLIC verifier keyring. "
                    "Exit 0 = PASS (no failure observed; PASS != TRUE), 1 = FAIL, 2 = input refused / UNKNOWN.",
    )
    parser.add_argument("--db", required=True, help="kernel SQLite file (a checkpointed copy; never a live DB)")
    parser.add_argument("--keyring", required=True, help="PUBLIC verifier keyring JSON {\"keys\": [...]}")
    parser.add_argument("--since", help="enforce floor from an out-of-band record (epoch seconds or ISO-8601); 0 = all")
    parser.add_argument("--out", help="also write the JSON report to this file")
    args = parser.parse_args(argv)
    try:
        report = audit_database(args.db, args.keyring, since=args.since)
    except AuditInputError as exc:
        report = {"schema": REPORT_SCHEMA, "semantics": SEMANTICS, "verdict": VERDICT_UNKNOWN,
                  "verdict_code": exc.code, "error": str(exc)}
    except (OSError, sqlite3.Error) as exc:  # không đọc trọn được snapshot: không có verdict, KHÔNG phải PASS
        report = {"schema": REPORT_SCHEMA, "semantics": SEMANTICS, "verdict": VERDICT_UNKNOWN,
                  "verdict_code": AuditCode.DB_UNAVAILABLE, "error": f"{type(exc).__name__} while reading the inputs"}
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if args.out:
        out = Path(args.out)
        if out.resolve() in {Path(args.db).resolve(), Path(args.keyring).resolve()}:
            sys.stderr.write("refusing to write the report over an input file\n")
            return EXIT_CODES[VERDICT_UNKNOWN]
        try:
            out.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"cannot write the report file ({type(exc).__name__})\n")
            return EXIT_CODES[VERDICT_UNKNOWN]
    sys.stdout.write(text + "\n")
    return EXIT_CODES[report["verdict"]]


__all__ = [
    "AuditCode", "AuditInputError", "CLASSIFICATIONS", "COMPLETED_EVENT", "EXIT_CODES", "INVALID",
    "KEYRING_LOADED_EVENT", "MISSING_RECEIPT", "OPEN_URI_PARAMS", "REPORT_SCHEMA", "SELF_ATTESTED_HMAC",
    "SEMANTICS", "VERIFIED_KEY_C", "audit_database", "classify_completion", "file_sha256", "load_public_keyring",
    "main", "open_snapshot", "parse_timestamp", "sidecars",
]
