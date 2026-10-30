"""[BUYER-05 2026-10-30] Giảm bề mặt công khai: docs opt-in + /health rút gọn.

TẠI SAO (bằng chứng buyer đợt 2, probe base 05e0e328):
  - ``/docs`` + ``/openapi.json`` (184 paths) mở không auth ở posture
    non-production; stack local mặc định (compose.yml) ghim
    SCP_PRODUCTION_MODE=0 nên "chỉ ẩn khi production" không đóng được bề mặt
    buyer thấy. ⇒ Docs là OPT-IN ở mọi mode: chỉ bật khi ``SCP_ENABLE_DOCS``
    truthy (flag sẵn có, đảo ngược được). ``app.openapi()`` nội bộ vẫn chạy
    (tools/export_openapi_snapshot.py) vì chỉ route HTTP bị gỡ.
  - ``/health`` không auth lộ fingerprint (commit, routes, model_id,
    active_llm_providers, version/release, pid, host, modules). ⇒ Không có
    bằng chứng admin ⇒ chỉ ``status`` + identity liveness tối thiểu. Bản đầy
    đủ chỉ khi ``verify_admin`` canonical (scp/security/auth.py — KHÔNG tạo
    verifier mới) trả True.

Fail-closed: thiếu header ⇒ không gọi verify_admin (healthcheck Docker không
bao giờ bị tính là auth failure); header sai / 401 / 429 / 503 / mọi exception
⇒ bản rút gọn, KHÔNG BAO GIỜ lỗi (healthcheck chỉ cần HTTP 200 + status).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("scp.api_server_parts.public_surface")

_TRUE = frozenset({"1", "true", "yes", "on"})

# Identity tối thiểu cho liveness/port-verification (run_scp_acceptance a01,
# startup-troubleshooter, test_flow_01). KHÔNG chứa commit/pid/host.
PUBLIC_IDENTITY_KEYS: tuple[str, ...] = ("service_name", "mode", "configured_port")


def docs_exposure_enabled(env_get: Callable[..., Any]) -> bool:
    """True chỉ khi SCP_ENABLE_DOCS truthy; mặc định tắt ở MỌI mode."""
    raw = env_get("SCP_ENABLE_DOCS", "0")
    return str(raw if raw is not None else "").strip().lower() in _TRUE


def health_caller_is_admin(request: Any, verify_admin_fn: Callable[..., Any]) -> bool:
    """Bearer admin hợp lệ qua verify_admin canonical ⇒ True; còn lại False."""
    if request is None:
        return False
    try:
        authorization = request.headers.get("authorization", "")
    except (AttributeError, TypeError):
        return False
    if not isinstance(authorization, str) or not authorization.strip():
        return False
    try:
        return verify_admin_fn(authorization=authorization, request=request) is True
    except Exception as exc:  # 401/429/503/HTTPException ⇒ rút gọn, không lỗi
        logger.debug("[BUYER-05] /health admin check denied: %s", type(exc).__name__, exc_info=True)
        return False


def build_public_health_payload(service_identity: dict) -> dict:
    """Payload /health cho caller không phải admin — không fingerprint."""
    identity = service_identity if isinstance(service_identity, dict) else {}
    return {
        "status": "ok",
        "service_identity": {k: identity[k] for k in PUBLIC_IDENTITY_KEYS if k in identity},
        "note": "public minimal health — full identity requires admin Bearer; /health/detailed for full status",
    }


__all__ = [
    "PUBLIC_IDENTITY_KEYS",
    "build_public_health_payload",
    "docs_exposure_enabled",
    "health_caller_is_admin",
]
