"""[Key C L2b] One-way receipt-authority mode floor (audit -> enforce), kernel-persisted.

Mode hiệu lực của cổng receipt = max(env ``SCP_RECEIPT_AUTHORITY_MODE``, floor) (receipt_authority
.resolve_mode). Floor nằm trong ``control.receipt_mode_floor``, là cờ control có chữ ký §15 (HMAC
K_journal, miền "control"): chữ ký thiếu/sai -> đọc ra ``enforce`` (CONTROL_FLAG_SAFE_DEFAULTS).

Một chiều:
* API ghi DUY NHẤT là ``raise_receipt_mode_floor`` và chỉ nhận ``enforce``; không có API hạ floor.
* Mỗi lần raise ghi ``__global__ RECEIPT_MODE_FLOOR_RAISED`` (journal chuỗi HMAC, cùng txn). Chữ ký
  §15 chỉ ràng buộc (tên cờ, giá trị) nên chữ ký của 'audit' là tất định và có sẵn trong mọi DB/backup
  cùng K_journal: kẻ ghi SQLite có thể REPLAY nó sau cutover, hoặc DROP cột để lần open sau tự ký lại
  'audit'. Journal anchor chặn cả hai: đã có event raise thì floor đọc ra enforce bất kể hàng control.
  Muốn hạ phải xóa cả event khỏi journal (để lại dấu ở chuỗi ``__global__`` / anchor event_count) —
  hoặc rollback cả file DB: giới hạn này chỉ auditor offline bằng public keyring (L6) bắt được.
"""
from __future__ import annotations

from typing import Any

from scp.security import receipt_authority
from scp.task_kernel_parts.definitions import KernelError
from scp.task_kernel_parts.definitions import RECEIPT_MODE_FLOOR_FLAG as FLOOR_FLAG
from scp.task_kernel_parts.definitions import sign_control_flag

FLOOR_RAISED_EVENT = "RECEIPT_MODE_FLOOR_RAISED"
_GLOBAL_STREAM = "__global__"


def floor_raise_journaled(conn: Any) -> bool:
    """Journal có ghi một lần raise floor chưa? (Chỉ làm floor CHẶT hơn, nên không cần verify HMAC.)"""
    row = conn.execute(
        "SELECT 1 FROM events WHERE task_id=? AND type=? LIMIT 1", (_GLOBAL_STREAM, FLOOR_RAISED_EVENT)
    ).fetchone()
    return row is not None


def read_receipt_mode_floor(kernel: Any) -> str:
    """Floor đã verify: giá trị có chữ ký §15 (sai/thiếu -> enforce), nâng lên enforce nếu journal có raise.

    Commit gate gọi hàm này BÊN TRONG transaction commit (cùng snapshot với lease row).
    """
    floor = kernel._control()[FLOOR_FLAG]
    if floor != receipt_authority.MODE_ENFORCE and floor_raise_journaled(kernel.conn):
        return receipt_authority.MODE_ENFORCE
    return floor


def raise_receipt_mode_floor(
    kernel: Any, mode: Any = receipt_authority.MODE_ENFORCE, *, actor: str = "operator"
) -> dict[str, Any]:
    """Nâng floor lên ``enforce`` (một chiều, idempotent); trả health đọc lại sau commit.

    Mọi ``mode`` khác đúng chuỗi ``"enforce"`` bị từ chối: không API nào hạ floor. Ghi giá trị +
    ký lại ĐÚNG cờ floor (không đụng cờ khác) + journal raise trong CÙNG một transaction.
    """
    if mode != receipt_authority.MODE_ENFORCE:
        raise KernelError(
            "receipt mode floor is one-way (audit -> enforce): it can only be raised to 'enforce', never lowered"
        )
    if not isinstance(actor, str) or not actor.strip():
        raise KernelError("actor is required")
    kernel._begin()
    try:
        control = kernel._control()
        signed_enforce = bool(control["control_flags_sig_valid"].get(FLOOR_FLAG)) and (
            control[FLOOR_FLAG] == receipt_authority.MODE_ENFORCE
        )
        if not signed_enforce:
            kernel.conn.execute(
                "UPDATE control SET receipt_mode_floor=? WHERE id=1", (receipt_authority.MODE_ENFORCE,)
            )
            sign_control_flag(kernel.conn, FLOOR_FLAG)
        if not floor_raise_journaled(kernel.conn):
            kernel._append_event(
                _GLOBAL_STREAM, FLOOR_RAISED_EVENT, None, None, actor, "receipt_mode_floor_raised",
                {"receipt_mode_floor": receipt_authority.MODE_ENFORCE, "already_signed_enforce": signed_enforce},
            )
        kernel._commit()
    except Exception:
        kernel._rollback()
        raise
    return receipt_floor_health(kernel)


STATE_ENFORCED = "ENFORCED"  # signed 'enforce' + raise journaled (cutover xong, nhất quán)
STATE_NOT_ENFORCED = "NOT_ENFORCED"  # signed 'audit', chưa từng raise (GĐ1, nhất quán)
STATE_INCONSISTENT = "INCONSISTENT"  # chữ ký sai/thiếu, hoặc hàng control mâu thuẫn journal (dấu tamper)


def receipt_floor_health(kernel: Any) -> dict[str, Any]:
    """Health của floor: giá trị hiệu lực, chữ ký §15 có hợp lệ không, raise có trong journal không,
    và ``receipt_mode_floor_state`` — chỉ ENFORCED/NOT_ENFORCED khi hàng control và journal NHẤT QUÁN."""
    control = kernel._control()
    sig_valid = bool(control["control_flags_sig_valid"].get(FLOOR_FLAG))
    journaled = floor_raise_journaled(kernel.conn)
    signed_value = control[FLOOR_FLAG]
    floor = read_receipt_mode_floor(kernel)
    if sig_valid and journaled and signed_value == receipt_authority.MODE_ENFORCE:
        state = STATE_ENFORCED
    elif sig_valid and not journaled and signed_value == receipt_authority.MODE_AUDIT:
        state = STATE_NOT_ENFORCED
    else:
        state = STATE_INCONSISTENT
    try:
        effective = receipt_authority.resolve_mode(floor)
    except receipt_authority.ReceiptAuthorityError as exc:
        effective = exc.code
    return {
        "receipt_mode_floor": floor,
        "receipt_mode_floor_signed_value": signed_value,
        "receipt_mode_floor_sig_valid": sig_valid,
        "receipt_mode_floor_raise_journaled": journaled,
        "receipt_mode_floor_state": state,
        "receipt_mode_effective": effective,
    }


__all__ = [
    "FLOOR_FLAG",
    "FLOOR_RAISED_EVENT",
    "STATE_ENFORCED",
    "STATE_INCONSISTENT",
    "STATE_NOT_ENFORCED",
    "floor_raise_journaled",
    "raise_receipt_mode_floor",
    "read_receipt_mode_floor",
    "receipt_floor_health",
]
