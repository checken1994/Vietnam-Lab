"""Model lifecycle registry: models that a provider has publicly retired.

[C1 wire-or-kill 2026-10-30] Ghi lại — CÓ NGUỒN CÔNG KHAI — các model id mà
nhà cung cấp đã decommission, để (1) test chặn việc đặt lại một default đã
chết, (2) ``LLMGateway.llm_status()`` cảnh báo trung thực khi cấu hình đang
trỏ vào model đã bị khai tử.

Đây KHÔNG phải nguồn chân lý runtime: nhà cung cấp có thể gia hạn/đổi ngày,
trang doc có thể lệch với API thật (Groq models page còn liệt kê
llama-3.3-70b-versatile là production trong khi trang deprecations ghi shutdown
2026-08-16 và API thật trả 404). Chân lý runtime là phản hồi 404 model-not-found
của chính provider (dead-model registry trong circuit_breaker.py). Danh sách
này chỉ là cảnh báo/guard test — không bao giờ tự ý chặn một call.

Mỗi dòng: (provider_name, model_id, retired_on, source_url). Dữ liệu: ngày
tra cứu 2026-10-30 từ trang công khai của từng nhà cung cấp.
"""
from __future__ import annotations

from typing import NamedTuple


class DecommissionedModel(NamedTuple):
    provider: str
    model: str
    retired_on: str
    source_url: str
    replacement: str


DECOMMISSIONED_MODELS: tuple[DecommissionedModel, ...] = (
    DecommissionedModel(
        "groq", "llama-3.3-70b-versatile", "2026-08-16",
        "https://console.groq.com/docs/deprecations", "openai/gpt-oss-120b",
    ),
    DecommissionedModel(
        "groq", "llama-3.1-8b-instant", "2026-08-16",
        "https://console.groq.com/docs/deprecations", "openai/gpt-oss-20b",
    ),
    DecommissionedModel(
        "gemini_compat", "gemini-2.0-flash", "2026-06-01",
        "https://ai.google.dev/gemini-api/docs/deprecations", "gemini-3.6-flash",
    ),
    DecommissionedModel(
        "deepseek", "deepseek-chat", "2026-07-24",
        "https://api-docs.deepseek.com/updates", "deepseek-flash",
    ),
    DecommissionedModel(
        "deepseek", "deepseek-reasoner", "2026-07-24",
        "https://api-docs.deepseek.com/updates", "deepseek-v4-pro",
    ),
    # OpenRouter dùng slug khác (anthropic/claude-3.5-sonnet) — slug mà SCP từng
    # đặt làm default judge ("anthropic/claude-3-5-sonnet") không tồn tại trên
    # OpenRouter; model gốc đã retired trên Claude API 2025-10-28.
    DecommissionedModel(
        "openrouter", "anthropic/claude-3-5-sonnet", "2025-10-28",
        "https://platform.claude.com/docs/en/about-claude/model-deprecations",
        "anthropic/claude-sonnet-5.5",
    ),
)


def known_decommissioned(provider: str, model: str) -> DecommissionedModel | None:
    """Trả bản ghi nếu (provider, model) nằm trong danh sách khai tử đã biết."""
    p, m = str(provider or "").strip().lower(), str(model or "").strip()
    for entry in DECOMMISSIONED_MODELS:
        if entry.provider == p and entry.model == m:
            return entry
    return None
