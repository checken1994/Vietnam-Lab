"""Shared state-machine definitions, exceptions, and helpers for TaskKernel."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

# [K1-W2 2026-10-23] HC-44: WAITING_APPROVAL added as the 18th lifecycle state.
# It was already a key in ALLOWED_TRANSITIONS (PLANNING -> WAITING_APPROVAL ->
# READY/CANCELLED) and enforced via a carve-out hack in state_machine.py; the
# schema now agrees with the transition map (no more split-brain).
STATES = {
    "CREATED", "PLANNING", "WAITING_APPROVAL", "READY", "QUEUED", "LEASED",
    "RUNNING", "WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "UNKNOWN",
    "RECOVERING", "RECONCILING", "HUMAN_REVIEW", "RETRY_SCHEDULED",
    "COMPLETED", "FAILED", "CANCELLED",
}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

# [R7 2026-10-28] Edge-class security constants (kill-switch theo loại cạnh +
# authority gate theo loại cạnh). Phân loại THEO LỚP trạng thái, không liệt kê
# cặp from->to (OWNER MANDATE @ bfb40026: cấm phân loại bằng danh sách mẫu).
# Khi global kill active, CHỈ cạnh về SAFE_UNDER_KILL được phép (nhả lease /
# contain, không tiếp tục công việc); mọi cạnh "khởi động lại" có target trong
# RESUME_TARGETS bị chặn. Cạnh đưa task từ CONTROLLED_STATES về
# DISPATCH_TARGETS phải qua authority gate chính thức (commit_approval /
# reconcile_unknown / auto_resolve_human_review), không qua transition()
# generic — kể cả qua trung gian (xem state_machine._checkpointed_from_controlled).
SAFE_UNDER_KILL = frozenset({
    "FAILED", "CANCELLED", "HUMAN_REVIEW", "CHECKPOINTED", "UNKNOWN",
    "RECONCILING", "RECOVERING", "RETRY_SCHEDULED",
})
RESUME_TARGETS = frozenset({
    "READY", "QUEUED", "LEASED", "RUNNING", "WAITING_TOOL", "VERIFYING", "PLANNING",
})
CONTROLLED_STATES = frozenset({
    "WAITING_APPROVAL", "HUMAN_REVIEW", "RECONCILING", "UNKNOWN", "RECOVERING",
})
DISPATCH_TARGETS = frozenset({"READY", "QUEUED", "LEASED", "RUNNING"})


# [§15 follow-up / R7 tightening 2026-10-29] System-authority waive theo CALLER,
# không theo kernel object. Trước đây recover_on_boot() bật
# ``kernel._system_authority = True`` như một instance attribute: mọi thread
# khác gọi CHUNG kernel object trong cửa sổ recovery cũng bị miễn yêu cầu
# lease (probe 2026-10-29: thread B không giữ lease vẫn chuyển
# RUNNING->HUMAN_REVIEW thành công trong khi thread A đang giữ flag). Giờ
# flag sống trong ContextVar — mỗi thread/caller-context có giá trị riêng,
# chỉ caller khai báo rõ (system_authority_scope) mới được waive.
# TaskKernel._system_authority là property đọc/ghi ContextVar này nên mọi
# call-site cũ (cùng thread) giữ nguyên hành vi.
_system_authority_var: ContextVar[bool] = ContextVar(
    "scp_task_kernel_system_authority", default=False
)


def current_system_authority() -> bool:
    """[§15] System-authority flag của caller HIỆN TẠI (mặc định False — fail-closed)."""
    return _system_authority_var.get()


@contextmanager
def system_authority_scope():
    """[§15] Khai báo system-authority trong phạm vi caller này (thread/context-local).

    Chỉ miễn YÊU CẦU LEASE (R7-4). Kill-check theo loại cạnh (R7-1) và edge-class
    authority gate (R7-2) vẫn chạy đầy đủ trong state machine — TRƯỚC nhánh
    lease — nên scope này không thể dùng để vòng qua chúng.
    """
    token = _system_authority_var.set(True)
    try:
        yield
    finally:
        _system_authority_var.reset(token)


def checkpointed_from_controlled(conn: Any, task_id: str) -> bool:
    """[R7-2] Did the task enter its current CHECKPOINTED stay from a CONTROLLED_STATES state?

    The journal is the authority: read ``from_state`` of the latest state-changing event
    (``from_state != to_state`` skips audit self-loops such as LEASE_EXPIRED containment).
    Every exit from CHECKPOINTED changes state, so this is the edge that entered it. Shared by
    the state machine and the lease watchdog so neither can requeue a contained task.
    """
    row = conn.execute(
        "SELECT from_state FROM events "
        "WHERE task_id=? AND from_state IS NOT NULL AND to_state IS NOT NULL "
        "AND from_state != to_state "
        "ORDER BY seq DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return bool(row and row["from_state"] in CONTROLLED_STATES)

ALLOWED_TRANSITIONS = {
    "CREATED": {"PLANNING", "CANCELLED"},
    "PLANNING": {"READY", "WAITING_APPROVAL", "FAILED", "CANCELLED"},
    "WAITING_APPROVAL": {"READY", "CANCELLED"},
    "READY": {"QUEUED", "CANCELLED"},
    "QUEUED": {"LEASED", "CANCELLED"},
    "LEASED": {"RUNNING", "RECOVERING", "CANCELLED"},
    # [Agent2 2026-09-29] RETRY_SCHEDULED added to execution states: the
    # retry branch of commit_failed() (pinned by b5/autonomous/gap13 tests)
    # commits it from RUNNING/WAITING_TOOL/VERIFYING; the map now agrees.
    "RUNNING": {"WAITING_TOOL", "VERIFYING", "CHECKPOINTED", "RECOVERING", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED", "CANCELLED"},
    "WAITING_TOOL": {"VERIFYING", "UNKNOWN", "RECOVERING", "RETRY_SCHEDULED", "FAILED", "CANCELLED"},
    "VERIFYING": {"VERIFYING", "RUNNING", "COMPLETED", "HUMAN_REVIEW", "RETRY_SCHEDULED", "FAILED"},
    "CHECKPOINTED": {"RUNNING", "QUEUED", "CANCELLED"},
    "UNKNOWN": {"RECONCILING", "HUMAN_REVIEW", "RECOVERING", "FAILED", "CANCELLED"},
    "HUMAN_REVIEW": {"READY", "CANCELLED", "FAILED"},
    "RECOVERING": {"RECONCILING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED"},
    "RECONCILING": {"RECOVERING", "CHECKPOINTED", "QUEUED", "HUMAN_REVIEW", "FAILED", "CANCELLED"},
    "RETRY_SCHEDULED": {"QUEUED", "FAILED", "CANCELLED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


class KernelError(RuntimeError):
    pass


class InvalidTransition(KernelError):
    pass


class StaleLease(KernelError):
    pass


class OptimisticLockError(StaleLease):
    """Raised when an atomic OCC version check fails (0 rows updated due to version mismatch)."""

    def __init__(
        self,
        message: str = "",
        *,
        table: str | None = None,
        entity_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        self.table = table
        self.entity_id = entity_id
        self.expected_version = expected_version
        if not message:
            message = (
                f"Optimistic lock conflict on table '{table}' for entity '{entity_id}'"
                f" (expected version {expected_version})"
            )
        elif entity_id and entity_id not in message:
            message = f"{message} (table={table}, entity_id={entity_id}, expected_version={expected_version})"
        super().__init__(message)


class KillSwitchActive(KernelError):
    pass


class JournalIntegrityHold(KillSwitchActive):
    """[JOURNAL-CHAIN 2026-10-31] Kill theo PHẠM VI TASK, do chính journal quyết.

    Chuỗi journal của task KHÔNG chứng minh được dưới các khóa kernel này giữ (K_journal hiện hành,
    K_journal đã nghỉ khai báo, khóa capability trong cửa sổ dual-accept) ⇒ mọi sự kiện TIẾN TRIỂN
    (lease, resume, checkpoint trước side effect, hoàn tất) bị từ chối, cả transaction rollback;
    journal không bị sửa/xóa (bằng chứng giữ nguyên). Cạnh an toàn (SAFE_UNDER_KILL) vẫn đi được.
    HOLD tự gỡ khi journal chứng minh được lại — không có cờ nào để ai bật/tắt.
    Lớp con của KillSwitchActive: mọi nơi đã xử lý kill (containment) xử lý HOLD giống vậy.
    """

    def __init__(self, task_id: str, integrity_class: str | None, errors: list[str] | None = None) -> None:
        self.task_id = task_id
        self.integrity_class = integrity_class
        self.errors = list(errors or [])[:5]
        super().__init__(
            f"journal integrity hold: task {task_id} chain is not provable under this kernel's keys "
            f"(class={integrity_class}, errors={self.errors})"
        )


class CheckpointCorrupt(KernelError):
    pass


class NotFound(KernelError):
    pass


@dataclass(frozen=True)
class Lease:
    lease_id: str
    task_id: str
    attempt_id: str
    worker_id: str
    expires_at: float
    fencing_token: int
    global_kill_epoch: int


@dataclass(frozen=True)
class RecoveryDecision:
    decision: str
    reason: str
    safe_to_retry: bool
    required_evidence: tuple[str, ...]
    next_state: str
    escalation: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


# [R5 · K_journal] Journal + checkpoint HMAC dùng khóa RIÊNG (SCP_JOURNAL_SECRET),
# không còn dùng chung SCP_CAPABILITY_SECRET: trước R5 ai giữ khóa capability vừa
# mint được approval token, vừa ký receipt, vừa viết lại journal hợp lệ.
# Mỗi miền có tiền tố riêng để một chữ ký checkpoint (planned_action do caller
# cung cấp) không bao giờ dùng được làm event_hash của journal.
# [§15 2026-10-29] Thêm miền "control": ký HMAC TỪNG cờ control (global_kill +
# cờ migration một chiều) dưới chính K_journal qua secret_registry — không khóa mới.
KEYED_HASH_DOMAINS: dict[str, bytes] = {
    "journal": b"scp.journal.v1\x00",
    "checkpoint": b"scp.checkpoint.v1\x00",
    "control": b"scp.control.v1\x00",
}


def _split_secret_list(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def journal_secret() -> bytes:
    """K_journal hiện hành, đọc qua secret_registry (env hoặc SCP_JOURNAL_SECRET_FILE).

    Fail-closed khi thiếu, hoặc khi giá trị trùng SCP_CAPABILITY_SECRET (tách khóa cả giá trị).
    """
    from scp.security.secret_registry import resolve_distinct

    try:
        value = resolve_distinct("SCP_JOURNAL_SECRET", "SCP_CAPABILITY_SECRET")
    except ValueError as exc:
        raise KernelError(str(exc)) from exc
    if not value:
        raise KernelError("SCP_JOURNAL_SECRET (K_journal) is required to compute HMAC journal hash")
    return value.encode()


def previous_journal_secrets() -> list[bytes]:
    """K_journal đã nghỉ (rotation) khai báo qua SCP_PREVIOUS_JOURNAL_SECRETS (comma-separated).

    Fail-closed nếu danh sách chứa giá trị SCP_CAPABILITY_SECRET hay một khóa capability đã nghỉ
    (SCP_PREVIOUS_CAPABILITY_SECRETS): một "khóa đã nghỉ" như vậy cho
    người giữ khóa capability ký journal hợp lệ ở định dạng hiện hành (có tiền tố miền).
    """
    keys = _split_secret_list(os.environ.get("SCP_PREVIOUS_JOURNAL_SECRETS", ""))
    capability_keys = set(_split_secret_list(os.environ.get("SCP_CAPABILITY_SECRET", "")))
    capability_keys |= set(_split_secret_list(os.environ.get("SCP_PREVIOUS_CAPABILITY_SECRETS", "")))
    if capability_keys.intersection(keys):
        raise KernelError(
            "SCP_PREVIOUS_JOURNAL_SECRETS must not reuse a value of SCP_CAPABILITY_SECRET or "
            "SCP_PREVIOUS_CAPABILITY_SECRETS (R5 key separation)"
        )
    return [s.encode() for s in keys]


def legacy_capability_journal_keys(kernel: Any) -> list[bytes]:
    """Khóa journal trước R5 (SCP_CAPABILITY_SECRET + SCP_PREVIOUS_CAPABILITY_SECRETS, định dạng
    không tiền tố miền) — CHỈ trong cửa sổ dual-accept, tức khi control.journal_key_migrated == 0.

    Sau khi reanchor_all_journals ký lại sạch toàn bộ sang K_journal, cờ chuyển 1 và các khóa
    này không còn được chấp nhận (cutover). Không đọc được cờ -> coi như đã cutover (fail-closed).
    """
    try:
        ctrl = kernel._control()
        if int(ctrl["journal_key_migrated"] or 0) != 0:
            return []
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return []
    raw = [os.environ.get("SCP_CAPABILITY_SECRET", "")]
    raw += _split_secret_list(os.environ.get("SCP_PREVIOUS_CAPABILITY_SECRETS", ""))
    return [s.strip().encode() for s in raw if s.strip()]


# [R6] legacy_migration_done, [R5] journal_key_migrated: one-way 0->1 flags. A DB written by pre-R5
# code gets journal_key_migrated=0 (dual-accept window open); a fresh DB gets DEFAULT 1 from CREATE.
# [R5.1] journal_key_window_opened_at marks when the window opened: checkpoints created before it
# are signed with the pre-R5 key, later ones with K_journal.
# [Key C L2b] receipt_mode_floor: floor một chiều (audit -> enforce) của receipt authority
# (Key C). Fresh DB (CREATE trong TaskKernel._schema) và DB tiền-L2b (ALTER ở đây) = 'audit' (GĐ1);
# chỉ API raise ghi 'enforce'.
# Giá trị = scp.security.receipt_authority.MODE_AUDIT / MODE_ENFORCE (không import để tránh vòng).
RECEIPT_MODE_FLOOR_FLAG = "receipt_mode_floor"
CONTROL_MIGRATION_COLUMNS = (
    ("journal_head_hash", "TEXT"),
    ("event_count", "INTEGER NOT NULL DEFAULT 0"),
    ("legacy_migration_done", "INTEGER NOT NULL DEFAULT 0"),
    ("journal_key_migrated", "INTEGER NOT NULL DEFAULT 0"),
    ("journal_key_window_opened_at", "TEXT"),
    (RECEIPT_MODE_FLOOR_FLAG, "TEXT NOT NULL DEFAULT 'audit'"),
)

# [§15 2026-10-29] Ký cờ control (R6/R5 để lại: cờ one-way + global_kill nằm
# UNSIGNED trong bảng control — kẻ ghi SQLite sửa/xoá được). Mỗi cờ có cột chữ
# ký riêng "<flag>_sig" (HMAC-SHA256, K_journal, miền "control", body ràng buộc
# tên cờ) nên: (i) kernel ghi cờ nào thì ký lại đúng cờ đó — không ký bừa cả
# tuple và vô tình "giặt" (launder) cờ khác đang bị tamper; (ii) đọc qua
# verified_control_row() thay cờ có chữ ký sai bằng GIÁ TRỊ AN TOÀN MẶC ĐỊNH:
# global_kill=1 (kill ON, fail-closed), các cờ migration=0 / mark=None
# (không tin trạng thái "đã xong" không chứng minh được).
# Chữ ký chỉ được tạo bởi 2 đường: (a) self-sign MỘT lần khi cột chữ ký vừa
# được ALTER thêm tại open (DB tiền-§15, ký đúng giá trị hiện tại, không đổi
# giá trị); (b) kernel API ghi cờ đó. Đọc thấy sig NULL/sai -> mặc định an toàn,
# KHÔNG tự ký lại (kẻ tấn công NULL-hóa chữ ký không được tặng chữ ký mới).
SIGNED_CONTROL_FLAGS = (
    "global_kill",
    "journal_key_migrated",
    "legacy_migration_done",
    "journal_key_window_opened_at",
    RECEIPT_MODE_FLOOR_FLAG,
)
CONTROL_FLAG_SIG_SUFFIX = "_sig"
CONTROL_FLAG_SAFE_DEFAULTS: dict[str, Any] = {
    "global_kill": 1,  # kill ON — fail-closed
    "journal_key_migrated": 0,
    "legacy_migration_done": 0,
    "journal_key_window_opened_at": None,  # không có mark: mọi live checkpoint đều chặn cutover
    # [Key C L2b] thiếu/sai chữ ký -> giá trị CHẶT: không bao giờ đọc ra 'audit' không chứng minh được.
    RECEIPT_MODE_FLOOR_FLAG: "enforce",
}
# [Key C L2b] Cờ có mặc định an toàn là giá trị CHẶT và KHÔNG có API nào ký lại giá trị lỏng:
# cột chữ ký chỉ được thêm khi ký được NGAY (có K_journal). Thêm cột mà không ký được thì sig NULL
# khóa floor ở enforce vĩnh viễn (open không tự ký cột đã có). Chưa thêm cột -> đọc vẫn fail-closed;
# lần open sau có K_journal mới thêm + tự ký MỘT lần đúng giá trị hiện tại (quy tắc §15).
_SIGN_BEFORE_ADD_FLAGS = frozenset({RECEIPT_MODE_FLOOR_FLAG})


def control_flag_sig_column(flag: str) -> str:
    return flag + CONTROL_FLAG_SIG_SUFFIX


def compute_control_flag_sig(flag: str, value: Any) -> str:
    """[§15] Chữ ký của MỘT cờ control dưới K_journal; tên cờ nằm trong body nên
    chữ ký của cờ này không replay được sang cờ khác dù giá trị trùng nhau."""
    return compute_keyed_hash({"flag": flag, "value": value}, domain="control")


def sign_control_flag(conn: Any, flag: str) -> None:
    """[§15] Ký lại MỘT cờ control theo giá trị ĐANG LƯU — gọi trong cùng
    transaction với lần ghi cờ đó (identifier là hằng số compile-time của
    SIGNED_CONTROL_FLAGS, cùng mô-típ ALTER an toàn ở migrate_control_columns)."""
    row = conn.execute("SELECT * FROM control WHERE id=1").fetchone()
    conn.execute(
        f"UPDATE control SET {control_flag_sig_column(flag)}=? WHERE id=1",
        (compute_control_flag_sig(flag, row[flag]),),
    )


def verified_control_row(row: Any) -> dict[str, Any]:
    """[§15] Đọc verified control row: cờ có chữ ký hợp lệ giữ nguyên giá trị;
    cờ thiếu/sai chữ ký bị thay bằng CONTROL_FLAG_SAFE_DEFAULTS (fail-closed).

    Trả về dict (index + .keys() như sqlite3.Row) kèm "control_flags_sig_valid":
    map flag->bool cho telemetry/test; thiếu K_journal khi verify cũng coi là
    không hợp lệ (fail-closed), không raise.
    """
    data = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    validity: dict[str, bool] = {}
    for flag in SIGNED_CONTROL_FLAGS:
        stored = data.get(control_flag_sig_column(flag))
        valid = False
        if stored:
            try:
                valid = hmac.compare_digest(
                    str(stored), compute_control_flag_sig(flag, data.get(flag))
                )
            except KernelError:
                valid = False
        validity[flag] = valid
        if not valid:
            data[flag] = CONTROL_FLAG_SAFE_DEFAULTS[flag]
    data["control_flags_sig_valid"] = validity
    return data


def _control_key_available() -> bool:
    try:
        journal_secret()
    except KernelError:
        return False
    return True


def migrate_control_columns(conn: Any) -> None:
    """Add missing control columns, then stamp the window mark if the window is open without one.

    Run at every open; the stamp is idempotent. A DB upgraded before R5.1 (window open, no mark)
    is stamped now, which conservatively treats every checkpoint that already exists as pre-R5.

    [§15] Cột chữ ký "<flag>_sig" được ALTER thêm khi thiếu; DB tiền-§15 được tự ký
    MỘT lần đúng giá trị hiện tại (không đổi giá trị) ngay tại open này — và chỉ
    khi cột VỪA được thêm (cột đã có mà sig NULL/sai là tamper: đọc ra mặc định
    an toàn, không ký lại). Stamp window mark là một kernel-write lên cờ đã ký
    nên phải ký lại cờ đó (rowcount>0 = thực sự vừa ghi).

    [§15 fix · satellite legacy shape] Control table thời tiền-R5 có thể THIẾU
    luôn cột giá trị cơ bản (vd chỉ có id+global_kill_epoch): kernel mọi đường
    đọc đều giả định global_kill tồn tại nên phải bổ sung cột giá trị bị thiếu
    (DEFAULT 0, đúng nghĩa fresh CREATE) TRƯỚC khi ký — sửa lỗi latent IndexError
    mà signature path lộ ra (test_satellite_occ_anti_placebo).
    """
    cols = {row["name"] for row in conn.execute('PRAGMA table_info("control")').fetchall()}
    if "global_kill" not in cols:
        conn.execute('ALTER TABLE "control" ADD COLUMN global_kill INTEGER NOT NULL DEFAULT 0')
        cols.add("global_kill")
    for col, ddl in CONTROL_MIGRATION_COLUMNS:
        if col not in cols:
            conn.execute(f'ALTER TABLE "control" ADD COLUMN {col} {ddl}')
    newly_signed: list[str] = []
    for flag in SIGNED_CONTROL_FLAGS:
        sig_col = control_flag_sig_column(flag)
        if sig_col not in cols:
            if flag in _SIGN_BEFORE_ADD_FLAGS and not _control_key_available():
                import logging

                logging.getLogger(__name__).warning(
                    "[Key C L2b] K_journal unavailable: control flag '%s' stays unsigned (reads fail closed) "
                    "and its signature column is added at the next open that can sign it", flag,
                )
                continue
            conn.execute(f'ALTER TABLE "control" ADD COLUMN {sig_col} TEXT')
            newly_signed.append(flag)
    stamped = conn.execute(
        "UPDATE control SET journal_key_window_opened_at=? "
        "WHERE id=1 AND journal_key_migrated=0 AND journal_key_window_opened_at IS NULL",
        (now_iso(),),
    )
    if getattr(stamped, "rowcount", 0):
        sign_control_flag(conn, "journal_key_window_opened_at")
    for flag in newly_signed:
        try:
            sign_control_flag(conn, flag)
        except KernelError:
            # Thiếu hoặc sai K_journal: không ký được — các lần đọc sau sẽ ra mặc định
            # an toàn (kill=ON) cho tới khi DB được mở lại với K_journal đúng.
            import logging

            logging.getLogger(__name__).warning(
                "[§15] could not self-sign control flag '%s' at migration (K_journal unavailable); "
                "reads will fail closed to safe defaults", flag,
            )


def live_checkpointed_tasks(conn: Any) -> list[str]:
    """[R5/R5.1] Non-terminal tasks owning a checkpoint created BEFORE the dual-accept window opened.

    Those hashes were signed with the pre-R5 capability key and cannot be re-signed (planned_action
    is not stored), so the K_journal cutover waits until such tasks finish. Checkpoints created after
    control.journal_key_window_opened_at are K_journal-signed and must not hold the window open (a
    busy DB would otherwise never cut over). Without a mark every live checkpoint counts.

    Accepted limit (F4): after the cutover, a pre-R5 checkpoint of a task that is already terminal
    but was never finalized can no longer be validated or finalized (CheckpointCorrupt); flows that
    finalize after termination only log it.
    """
    row = conn.execute("SELECT * FROM control WHERE id=1").fetchone()
    # [§15] mark đọc qua verify: chữ ký sai -> None (mọi live checkpoint đều chặn
    # cutover — hướng an toàn), không tin mark bị sửa trực tiếp qua SQLite.
    mark = verified_control_row(row)["journal_key_window_opened_at"] if row else None
    placeholders = ",".join("?" for _ in TERMINAL)
    sql = (
        "SELECT DISTINCT c.task_id FROM checkpoints c JOIN tasks t ON t.task_id = c.task_id "
        f"WHERE t.state NOT IN ({placeholders})"
    )
    params: tuple[Any, ...] = tuple(sorted(TERMINAL))
    if mark:
        sql += " AND c.created_at <= ?"
        params += (mark,)
    return [str(r[0]) for r in conn.execute(sql + " ORDER BY c.task_id", params).fetchall()]


def finalize_journal_migration(kernel: Any, total: int, failed: list[str]) -> dict[str, Any]:
    """Flip the one-way migration flags after a reanchor pass and report the REAL flag state.

    [R6] legacy_migration_done flips after a clean pass over at least one task. [R5] the K_journal
    cutover (journal_key_migrated) flips after a clean pass when no live pre-window checkpoint
    remains, including on an empty DB. ``status`` is COMPLETED only when the cutover flag is set.
    """
    window_open = int(kernel._control()["journal_key_migrated"] or 0) == 0
    blocked = live_checkpointed_tasks(kernel.conn) if window_open else []
    legacy_flag_set = False
    if not failed:
        kernel._begin()
        try:
            # [§15] Giá trị đích tính từ LƯỢC ĐỒ VERIFIED (cờ có chữ ký hợp lệ),
            # ghi vô điều kiện trong transaction này rồi ký lại đúng cờ vừa ghi:
            # one-way giữ nguyên (verified chỉ có thể bằng 1 nếu chữ ký hợp lệ nói
            # vậy), và cờ bị tamper (verified=default 0) không được "giặt" thành 1
            # nếu pass này không thực sự đủ điều kiện flip.
            final_lmd = int(kernel._control()["legacy_migration_done"] or 0)
            if total > 0 and final_lmd == 0:
                final_lmd = 1
                legacy_flag_set = True
            final_jkm = 1 if (window_open and not blocked) else int(
                kernel._control()["journal_key_migrated"] or 0
            )
            kernel.conn.execute(
                "UPDATE control SET legacy_migration_done=?, journal_key_migrated=? WHERE id=1",
                (final_lmd, final_jkm),
            )
            sign_control_flag(kernel.conn, "legacy_migration_done")
            sign_control_flag(kernel.conn, "journal_key_migrated")
            kernel._commit()
        except Exception:
            kernel._rollback()
            raise
    migrated = int(kernel._control()["journal_key_migrated"] or 0) == 1
    return {
        "legacy_migration_done": legacy_flag_set,
        "journal_key_migrated": migrated,
        "journal_key_cutover_blocked_by": blocked,
        "status": "COMPLETED" if not failed and migrated else "PARTIAL",
    }


def _hmac_hex(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def compute_keyed_hash(value: Any, *, domain: str) -> str:
    """[R5] HMAC-SHA256 dưới K_journal hiện hành, tách miền theo ``domain``."""
    return _hmac_hex(journal_secret(), KEYED_HASH_DOMAINS[domain] + json.dumps(value, sort_keys=True).encode())


def matches_previous_secret_hmac(
    value: Any, stored_hash: str, *, domain: str, legacy_keys: Any = ()
) -> bool:
    """stored_hash có khớp một K_journal đã nghỉ (định dạng có miền), hoặc một khóa legacy
    của cửa sổ dual-accept (định dạng trước R5, không miền)?"""
    body_bytes = json.dumps(value, sort_keys=True).encode()
    prefixed = KEYED_HASH_DOMAINS[domain] + body_bytes
    if any(hmac.compare_digest(stored_hash, _hmac_hex(k, prefixed)) for k in previous_journal_secrets()):
        return True
    return any(hmac.compare_digest(stored_hash, _hmac_hex(k, body_bytes)) for k in legacy_keys)


def compute_journal_event_hash(body: dict[str, Any]) -> str:
    return compute_keyed_hash(body, domain="journal")


def classify_journal_hash(body: dict[str, Any], stored_hash: str, legacy_keys: Any = ()) -> tuple[bool, bool]:
    """(is_keyed, via_legacy_capability_key) của một event_hash: khớp K_journal hiện hành,
    K_journal đã nghỉ, hoặc khóa capability trước R5 trong cửa sổ dual-accept (``legacy_keys``)."""
    try:
        current = compute_journal_event_hash(body)
    except KernelError:
        current = None  # thiếu K_journal -> chỉ còn các khóa đã nghỉ
    if current is not None and stored_hash == current:
        return True, False
    if matches_previous_secret_hmac(body, stored_hash, domain="journal"):
        return True, False
    if legacy_keys and matches_previous_secret_hmac(body, stored_hash, domain="journal", legacy_keys=legacy_keys):
        return True, True
    return False, False


def journal_event_hash_with_key(body: dict[str, Any], key: bytes) -> str:
    """event_hash của ``body`` dưới một K_journal cho trước (dùng bởi runtime audit độc lập)."""
    return _hmac_hex(key, KEYED_HASH_DOMAINS["journal"] + json.dumps(body, sort_keys=True).encode())


def journal_event_body(e: Any, payload: Any) -> dict[str, Any]:
    """Canonical body của một journal event dùng cho event_hash (moved từ lease_manager, R6)."""
    return {
        'event_id': e['event_id'], 'task_id': e['task_id'], 'seq': e['seq'], 'type': e['type'],
        'from_state': e['from_state'], 'to_state': e['to_state'], 'actor': e['actor'], 'reason': e['reason'],
        'payload': payload, 'policy_hash': e['policy_hash'], 'prev_event_hash': e['prev_event_hash'],
    }


# [JOURNAL-CHAIN 2026-10-31] Phân loại nguyên nhân một chuỗi không chứng minh được — theo CẤU TRÚC
# (lỗi cấu trúc + tỉ lệ event lệch HMAC), không theo câu chữ:
#   STRUCTURAL_TAMPER   — seq/prev-hash/payload/anchor vỡ: hàng bị chèn/xóa/đảo ngoài kernel API;
#   CONTENT_TAMPER      — một PHẦN event lệch HMAC, phần còn lại khớp khóa hiện hành: nội dung bị sửa
#                         sau khi ký;
#   KEY_DOMAIN_MISMATCH — cấu trúc nguyên vẹn, MỌI event lệch đồng loạt: chuỗi được ký dưới một miền
#                         khóa kernel này không giữ (rotation chưa khai báo, tiến trình khác khóa ghi
#                         cùng DB, hoặc viết lại toàn bộ bởi người giữ khóa khác). Không tự chấp nhận.
INTEGRITY_CLASSES = ("STRUCTURAL_TAMPER", "CONTENT_TAMPER", "KEY_DOMAIN_MISMATCH")
# Sự kiện không đổi state nhưng là ranh giới TRƯỚC side effect (phải HOLD như cạnh resume).
PRE_SIDE_EFFECT_EVENTS = frozenset({"CHECKPOINT_WRITTEN", "ACTION_DISPATCHED"})


def classify_chain_integrity(structural_errors: int, hash_failed: int, hash_checked: int) -> str | None:
    if structural_errors:
        return "STRUCTURAL_TAMPER"
    if hash_failed == 0:
        return None
    if hash_failed < hash_checked:
        return "CONTENT_TAMPER"
    return "KEY_DOMAIN_MISMATCH"


def is_progress_event(event_type: str, to_state: str | None) -> bool:
    """Sự kiện TIẾN TRIỂN công việc (phải có chuỗi chứng minh được): cạnh tới trạng thái ngoài
    SAFE_UNDER_KILL (lease, resume, hoàn tất…) hoặc ranh giới trước side effect. Cạnh an toàn,
    sự kiện ghi nhận kết quả/audit (to_state None) và luồng toàn cục không bị HOLD."""
    if event_type in PRE_SIDE_EFFECT_EVENTS:
        return True
    return to_state is not None and to_state not in SAFE_UNDER_KILL


def all_events_provable_under_previous_secrets(kernel: Any, task_id: str) -> bool:
    """[R6/R5] Mọi event của task phải khớp K_journal hiện hành, một K_journal đã nghỉ, hoặc
    khóa legacy của cửa sổ dual-accept thì reanchor (rotation / migration) mới được cấp."""
    legacy_keys = legacy_capability_journal_keys(kernel)
    if not previous_journal_secrets() and not legacy_keys:
        return False
    for e in kernel.get_events(task_id):
        try:
            payload = json.loads(e['payload_json'])
        except (TypeError, ValueError):
            return False
        if not classify_journal_hash(journal_event_body(e, payload), e['event_hash'], legacy_keys)[0]:
            return False
    return True


_SENSITIVE_KEY_MARKERS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "cookie", "private_key",
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)=\S+"),
)


def _checkpoint_contains_secret(value: Any, path: str = "checkpoint") -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(marker in key_text for marker in _SENSITIVE_KEY_MARKERS):
                return True
            if _checkpoint_contains_secret(child, f"{path}.{key}"):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_checkpoint_contains_secret(child, f"{path}[]") for child in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)
    return False


def _assert_checkpoint_safe(value: Any) -> None:
    if _checkpoint_contains_secret(value):
        raise KernelError("checkpoint contains secret material")


def as_json(value: Any) -> str:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def verify_approval_authority(
    token: Any,
    task_id: str,
    max_skew_seconds: float = 300.0,
) -> dict[str, Any]:
    """Verify an issuer-signed approval token for ``task_id`` fail-closed (GAP-13, R5-B).

    [R5-B] Only Ed25519 tokens signed with the issuer PRIVATE key are accepted, verified with
    SCP_APPROVAL_ISSUER_PUBLIC_KEY. HMAC capability tokens (compact mint / CapabilityToken) and
    operator HMAC signature dicts are rejected: whoever held SCP_CAPABILITY_SECRET could mint
    them, the autonomous checker gate included. Validity is bounded by the token's own exp
    (max 1h); ``max_skew_seconds`` is kept for call compatibility.
    """
    from scp.core.capability_token import InvalidTokenSignatureError
    from scp.security.approval_issuer import (
        ApprovalTokenExpired,
        ApprovalTokenScopeMismatch,
        verify_approval_token,
    )

    if token is None or (isinstance(token, str) and not token.strip()):
        raise InvalidTokenSignatureError("Approval token is missing or empty (GAP-13/FA-04)")
    try:
        return verify_approval_token(token.strip() if isinstance(token, str) else token, task_id)
    except ApprovalTokenExpired as exc:
        raise InvalidTransition(f"Approval token has expired: {exc}") from exc
    except ApprovalTokenScopeMismatch as exc:
        raise InvalidTransition(f"Approval token does not authorize 'approval:grant' for task '{task_id}': {exc}") from exc

