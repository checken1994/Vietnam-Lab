"""Admin credential → principal class (esc-auto slice 3).

TẠI SAO module này tồn tại:
  ``verify_admin`` chấp nhận 5 loại credential như nhau, rồi route ghi
  ``actor="human_via_api"``. Nghĩa là một MÁY (loop-scheduler giữ
  ``SCP_SCHEDULER_ADMIN_TOKEN``) approve được dead-man timer và nâng
  ``CapabilityLevel`` mà audit vẫn ghi "người" (c1-escalation-done §7).

Phân loại theo BẰNG CHỨNG phân phối credential trong docker-compose.full.yml
(credential nào được giao cho một service tự động thì là MÁY):
  * SCP_SCHEDULER_ADMIN_TOKEN — loop-scheduler, dashboard            ⇒ machine
  * SCP_AUTH_TOKEN_SECRET     — loop-scheduler, dashboard proxy      ⇒ machine
  * SCP_ADMIN_KEY             — brain-console, dashboard (/auth/token) ⇒ machine
  * JWT admin                 — mint từ SCP_ADMIN_KEY qua /auth/token ⇒ machine
  * SCP_AUTH_PASSWORD         — chỉ backend                          ⇒ operator
Bearer token KHÔNG chứng minh được có người thật; vì vậy nhãn là
``operator:<credential>`` (người vận hành giữ credential không phân phối cho
máy), không bao giờ là "human". Hiến pháp không đổi: CLEAR kill vẫn cần yếu
tố chỉ-người (HumanConfirmationStore) ở pc_controller, module này không đụng.
Credential trùng giá trị giữa nhiều loại ⇒ xếp loại HẠN CHẾ nhất (machine).
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request

OPERATOR = "operator"
MACHINE = "machine"

# Thứ tự = ưu tiên hạn chế: khớp credential máy trước, operator sau cùng.
_NAMED_CREDENTIALS: tuple[tuple[str, str, str], ...] = (
    ("scheduler_token", "scheduler_token", MACHINE),
    ("token", "token_secret", MACHINE),
    ("admin_key", "admin_key", MACHINE),
    ("password", "password", OPERATOR),
)
CREDENTIAL_CLASS: dict[str, str] = {name: cls for _, name, cls in _NAMED_CREDENTIALS}
CREDENTIAL_CLASS.update({"jwt_admin": MACHINE, "unknown": MACHINE})
STATE_ATTR = "scp_admin_credential"


def classify_admin_credential(auth_config: Any, provided: str) -> str:
    """Return the credential NAME that ``provided`` matches (most restrictive first)."""
    for attr, name, _cls in _NAMED_CREDENTIALS:
        value = getattr(auth_config, attr, "") or ""
        if not value:
            continue
        try:
            if secrets.compare_digest(str(provided), str(value)):
                return name
        except (TypeError, ValueError):
            continue
    return "unknown"


def principal_label(credential: str | None) -> str:
    name = credential or "unknown"
    return f"{CREDENTIAL_CLASS.get(name, MACHINE)}:{name}"


def record_credential(request: Any, credential: str) -> None:
    state = getattr(request, "state", None)
    if state is not None:
        try:
            setattr(state, STATE_ATTR, credential)
        except AttributeError:
            return


def request_principal(request: Any) -> str:
    return principal_label(getattr(getattr(request, "state", None), STATE_ATTR, None))


def require_operator_principal(request: Request) -> str:
    """FastAPI dependency (after verify_admin): machine credentials ⇒ 403."""
    credential = getattr(getattr(request, "state", None), STATE_ATTR, None)
    if CREDENTIAL_CLASS.get(credential or "unknown", MACHINE) != OPERATOR:
        raise HTTPException(
            status_code=403,
            detail="Machine credential cannot act as operator; this route is operator audit/veto only",
        )
    return principal_label(credential)


__all__ = [
    "OPERATOR", "MACHINE", "CREDENTIAL_CLASS", "classify_admin_credential",
    "principal_label", "record_credential", "request_principal", "require_operator_principal",
]
