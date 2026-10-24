"""Brain Layer P0 — contract ProviderAdapter (async, chuẩn hóa lỗi).

Adapter KHÔNG được tự quyết policy; chỉ chạy task đã được BrainLayer route và
trả `AdapterResult`. Mọi lỗi phải map về BrainProviderError(kind ∈ ERROR_KINDS)
hoặc BrainTimeout để envelope có status chuẩn.
"""
from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from scp.brain_layer.contracts import BrainContext
from scp.brain_layer.errors import BrainProviderError, BrainTimeout


def _empty_mapping() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True)
class AdapterResult:
    """Kết quả chuẩn từ adapter: output (đã là dict) + usage token."""

    output: Mapping[str, Any]
    usage: Mapping[str, Any] = field(default_factory=_empty_mapping)
    model_id: str = ""


def normalize_exception(
    exc: Exception, *, provider_id: str | None = None, model_id: str | None = None
) -> BrainProviderError:
    """Map exception thô → lỗi chuẩn hóa (timeout/rate_limit/auth/bad_request/5xx)."""
    if isinstance(exc, BrainProviderError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return BrainTimeout(
            f"provider timeout: {exc}", provider_id=provider_id, model_id=model_id
        )
    text = str(exc).lower()
    if "rate limit" in text or "429" in text:
        kind = "rate_limit"
    elif "auth" in text or "401" in text or "403" in text:
        kind = "auth"
    elif "400" in text or "bad request" in text:
        kind = "bad_request"
    else:
        kind = "provider_5xx"
    return BrainProviderError(f"provider error ({kind}): {exc}", kind=kind,
                              provider_id=provider_id, model_id=model_id)


class ProviderAdapter(abc.ABC):
    """Contract adapter cho 1 provider. State per-provider, DI bởi caller."""

    provider_id: str = ""

    @abc.abstractmethod
    async def run(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
    ) -> AdapterResult:
        """Chạy task trên provider. Bắt buộc raise lỗi chuẩn hóa khi thất bại."""

    def fail(self, message: str, *, kind: str = "provider_5xx") -> BrainProviderError:
        return BrainProviderError(
            message, kind=kind, provider_id=self.provider_id, model_id=None
        )
