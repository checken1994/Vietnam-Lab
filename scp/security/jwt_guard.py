import jwt
import os
import time
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any

# Enterprise Security: JWT secret is loaded LAZILY at request time, not at import time.
# This allows pytest and local dev environments to import the module without a full .env.
# Security is MAINTAINED (fail-closed): any actual JWT operation will raise
# RuntimeError if SCP_JWT_SECRET is missing, blocking the request completely.
# To start SCP: set SCP_JWT_SECRET in .env (see .env.example).


def _get_jwt_secret() -> str:
    secret = os.environ.get("SCP_JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "[SECURITY FATAL] SCP_JWT_SECRET is not set. "
            "Set it in .env before starting SCP. See .env.example for details."
        )
    return secret


JWT_ALGORITHM = "HS256"
security = HTTPBearer()


def create_access_token(data: dict, expires_delta: int = 3600) -> str:
    to_encode = data.copy()
    expire = time.time() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Dict[str, Any]:
    token = credentials.credentials.strip()
    import secrets as _secrets
    for env_key in ("SCP_API_KEY", "SCP_ADMIN_KEY", "SCP_AUTH_TOKEN_SECRET"):
        expected = os.environ.get(env_key, "").strip()
        if expected and _secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            return {"sub": "admin", "role": "admin", "auth_type": "api_key"}

    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(payload: Dict[str, Any] = Security(verify_jwt_token)) -> str:
    user = payload.get("sub")
    if user is None:
        raise HTTPException(status_code=401, detail="Token missing subject (sub)")
    return user
