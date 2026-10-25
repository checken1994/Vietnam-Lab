"""[WIRE-BRIDGE 2026-10-25] Bridge lookup tier — tách khỏi question_router.

[ARC-01 2026-10-25] question_router.py chạm trần 800 LOC (811 sau khi wire
bridge vào resolve_lookup_data) — luật repo pin EXACT LOC từng file, tách
file thay vì tăng pin. Module này chứa tier FreeAPIAdapterBridge của lookup
fork; question_router import ngược với alias `_bridge_lookup` (7 guard test
cũ monkeypatch đúng tên module-attribute đó — KHÔNG đổi contract).

stats được truyền vào từ caller (duck-type: chỉ cần `record_fallback(reason)`
và `record_fetch(ok=bool)` của RouteStats) — KHÔNG late-import ngược
question_router để tránh circular import.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("scp.runtime.bridge_lookup")


def bridge_lookup(domain: str, question: str, terms: list[str], stats: Any) -> dict[str, Any] | None:
    """[WIRE-BRIDGE 2026-10-25] Tier FreeAPIAdapterBridge cho lookup fork.

    [OWNER-MANDATE 2026-10-25] Nối catalog free API 2,175+ (live measured
    2026-10-25; catalog varies) vào luồng
    chạy chính của SCP: bridge 4 tầng (WAL cache → catalog API → live
    knowledge → wikipedia) trước đây là dead code (0 caller) — nay được wire
    vào resolve_lookup_data SAU tier dữ liệu local (conversion/weather/
    domain_data_fork) và TRƯỚC weak catalog tier.

    Ask path CHỈ chạy Tier 0 (WAL cache <1.5ms) + Tier 1 (catalog API, term
    gate + fail-closed): include_live_fallbacks=False vì Tier 2/3 của bridge
    trùng lặp với các tier fallback có sẵn phía sau trong chuỗi này (weak
    catalog tier → _wiki_lookup → LLM) — tránh fetch kép wikipedia trên miss
    và giữ nguyên hành vi các tier cũ. Latency budget: max_candidates=3 +
    budget 6s (cấu hình cũ 5×3s=15s worst case — quá chậm cho /ask).

    Fail-closed: bridge None/miss/exception → trả None → chuỗi fallback cũ
    (weak tier → wiki → LLM) chạy nguyên vẹn. KHÔNG fabrication. Egress:
    fetch catalog đi qua scoped grant của chính bridge (_catalog_egress_hosts
    — [OWNER-MANDATE 2026-10-25]); SCP_EGRESS_MODE=deny vẫn chặn tất cả
    (Invariant 3 của EgressPolicy chạy trước Invariant 4).

    Args:
        domain: domain của route decision ("general" nếu rỗng).
        question: câu hỏi /ask gốc.
        terms: salient terms đã extract (dùng làm entity cho bridge).
        stats: RouteStats-like counter (record_fallback / record_fetch).
    """
    try:
        from scp.data_sources.free_api_bridge import get_bridge

        result = get_bridge().fetch_for_domain(
            domain=domain or "general",
            intent="lookup",
            entity=" ".join(terms),
            question=question,
            max_candidates=3,
            budget_seconds=6.0,
            include_live_fallbacks=False,
        )
    except Exception as exc:
        logger.warning(
            "[S24][WIRE-BRIDGE] bridge fetch failed (%s: %s)", type(exc).__name__, exc, exc_info=True
        )
        stats.record_fallback("bridge_error")
        return None
    if not result or not result.get("found"):
        stats.record_fallback(f"bridge_miss:{(domain or 'general')[:30]}")
        return None
    value = str(result.get("value") or "").strip()
    if not value:
        stats.record_fallback("bridge_miss:empty_value")
        return None
    stats.record_fetch(ok=True)
    # Map đúng format caller attempt_lookup_fork kỳ vọng: {'text','api_name',
    # 'api_url','evidence'} (cùng shape với conversion/weather/wiki tier).
    return {
        "text": value,
        "api_name": str(result.get("api_name") or result.get("source") or "FreeAPIBridge"),
        "api_url": str(result.get("api_url") or ""),
        "evidence": value,
    }
