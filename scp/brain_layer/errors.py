"""Brain Layer P0 — lỗi chuẩn hóa (fail-closed).

Contract only, mode=off: package này KHÔNG cắm vào /ask, KHÔNG gọi model thật.
Mọi lỗi khai báo ở đây đều an toàn khi tạo (không nhúng giá trị secret).
"""
from __future__ import annotations

# Các kind lỗi provider đã chuẩn hóa (thiết kế §5 — adapter phải map về đây).
ERROR_KINDS: tuple[str, ...] = (
    "timeout",
    "rate_limit",
    "auth",
    "bad_request",
    "provider_5xx",
    "schema_invalid",
)


class BrainLayerError(Exception):
    """Base error cho toàn bộ brain layer."""


class BrainConfigError(BrainLayerError):
    """Registry/brain config vi phạm schema hoặc policy → từ chối khởi động.

    Fail-closed theo thiết kế §3.1: không bao giờ chạy với config nghi ngờ.
    """


class BrainPolicyDenied(BrainLayerError):
    """Policy chặn (mode=off, independence vi phạm, capability thiếu)."""


class BrainSchemaInvalid(BrainLayerError):
    """Task/contract không tồn tại hoặc envelope vi phạm contract."""


class BrainProviderError(BrainLayerError):
    """Lỗi provider đã chuẩn hóa (kind ∈ ERROR_KINDS)."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "provider_5xx",
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        if kind not in ERROR_KINDS:
            kind = "provider_5xx"
        super().__init__(message)
        self.kind = kind
        self.provider_id = provider_id
        self.model_id = model_id


class BrainTimeout(BrainProviderError):
    """Timeout gọi provider (kind='timeout')."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            kind="timeout",
            provider_id=provider_id,
            model_id=model_id,
        )
