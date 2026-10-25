"""[ARC-01] CORS origin policy tách khỏi ``scp.api_server``.

[SEC-04] Fail-closed trong production: không cấu hình CORS origin tường minh
→ trả về ``[]``. Đọc env qua ``scp.core.settings`` (HC-03 điểm đọc chuẩn);
secret không đi qua đây.
"""
from __future__ import annotations

from scp.core.settings import get


def _compute_cors_origins() -> list[str]:
    is_production = (
        get("SCP_PRODUCTION_MODE", "").strip() == "1"
        or get("SCP_ENV", "").strip().lower() == "production"
    )
    cors_raw = get("SCP_CORS_ORIGINS")
    if cors_raw is not None and cors_raw.strip():
        return [o.strip() for o in cors_raw.split(",") if o.strip()]
    if is_production:
        # [SEC-04] Fail-closed in production: disallow any CORS origins unless explicitly configured
        return []
    # Development mode fallback to localhost
    return ["http://localhost:3000"]
