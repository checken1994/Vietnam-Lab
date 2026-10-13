"""JWT token handler and revocation authority (SCP-06 / M-09)."""
from __future__ import annotations

from scp.security.jwt_guard import (
    JWT_ALGORITHM,
    create_access_token,
    get_current_user,
    is_token_revoked,
    revoke_token,
    verify_api_key,
    verify_jwt_token,
)

__all__ = [
    "JWT_ALGORITHM",
    "create_access_token",
    "verify_jwt_token",
    "verify_api_key",
    "get_current_user",
    "revoke_token",
    "is_token_revoked",
]
