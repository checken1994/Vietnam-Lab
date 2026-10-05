"""Cross-provider semantic verification.

A semantic consensus is only accepted when two distinct provider families
produce parseable verdicts for the same prompt. [W3-e2] A family is keyed by
its ``(base_url, model)`` lineage — two providers with different names but the
same endpoint and model are ONE family (same weights, same blind spots), never
two independent opinions (DNA #5).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Any

logger = logging.getLogger("scp.runtime.multi_llm_crosscheck")

# [W1-c6 2026-10-02] Deadline RIÊNG cho multi-LLM crosscheck. Trước c6,
# cross_verify gọi provider.chat TRỰC TIẾP không hedge/không deadline
# (:94-117): mỗi chat tự retry transient 3 lần × httpx timeout 60s, hai
# family có thể giữ judge pipeline vài phút — nằm TRONG lease window của
# /ask (cùng gốc withheld lifecycle_authority_lost q05/q11). Default 15s
# tổng: hai verdict PASS/FAIL ngắn thường về trong 1-5s/provider; hết
# budget → fail-closed (consensus missing) đúng hợp đồng hiện hành — không
# bao giờ tự phát minh consensus từ thiếu bằng chứng.
CROSSCHECK_DEFAULT_MAX_SECONDS = 15.0


def _crosscheck_max_seconds() -> float:
    """Total budget (giây) cho cross_verify từ SCP_CROSSCHECK_MAX_SECONDS.

    Fail-closed parse: giá trị lỗi/0/âm/non-finite → default.
    """
    raw = os.environ.get("SCP_CROSSCHECK_MAX_SECONDS")
    if raw is None:
        return CROSSCHECK_DEFAULT_MAX_SECONDS
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return CROSSCHECK_DEFAULT_MAX_SECONDS
    if not math.isfinite(value) or value <= 0:
        return CROSSCHECK_DEFAULT_MAX_SECONDS
    return value


def _now() -> float:
    """[W1-c9] Monotonic clock seam cho cross_verify.

    Toàn bộ phép đo thời gian trong cross_verify phải đi qua hàm này (không
    gọi time.monotonic() trực tiếp) để test có thể monkeypatch một đồng hồ giả
    và pin biên deadline một cách tất định, không phụ thuộc độ phân giải
    timer/scheduler của từng OS (Windows coarse timer ~15.6ms).
    """
    return time.monotonic()


def _candidate_providers(gateway: Any) -> list[Any]:
    """Return enabled judge candidates without inventing provider diversity."""
    public = getattr(gateway, "provider_candidates", None)
    if callable(public):
        candidates = list(public("judge") or [])
    else:
        internal = getattr(gateway, "_provider_chain", None)
        candidates = list(internal("judge") or []) if callable(internal) else []

    enabled: list[Any] = []
    for provider in candidates:
        try:
            if bool(getattr(provider, "enabled", False)):
                enabled.append(provider)
        except Exception as exc:
            logger.warning(
                "[MULTI-LLM] provider readiness failed (%s): %s",
                getattr(provider, "PROVIDER_NAME", "unknown"),
                type(exc).__name__,
            exc_info=True)
    return enabled


def _missing_opinion() -> dict[str, Any]:
    """Explicit unresolved opinion; never expose a shape that implies success."""
    return {"family": "", "provider": "none", "verdict": None}


def _family_key(provider: Any) -> str:
    """[W3-e2 2026-10-04] root-3: family identity = (base_url, model).

    TẠI SAO: trước e2 family keyed theo PROVIDER_NAME → openai_compat +
    openrouter là 2 "family" mặc dù CÙNG base_url (thực tế runtime W1: cả hai
    trỏ https://openrouter.ai/api/v1) và CÙNG model (nvidia/
    nemotron-3-super-120b-a12b:free) → consensus 'agree' từ 2 opinion CÙNG
    lineage — vi phạm DNA #5 (independent lineage) và gây nondeterminism q08.
    Hai provider khác tên chạy cùng model trên cùng endpoint là CÙNG lineage
    (cùng weights, cùng blind spots) → phải là 1 family.

    Key format: '<base_url>|<model>' (normalized: trim, lowercase, bỏ trailing
    '/'). Provider không khai báo base_url/model (stub legacy) → key chung
    'unknown|unknown' → fail-closed (không bao giờ đủ 2 family, không bao giờ
    tự phát minh tính độc lập từ tên).

    Residual (đã biết, ghi rõ): OpenRouterProvider có FREE fallback per-task —
    family key quyết định TRƯỚC khi biết model nào thật sự serve (primary có
    thể fail rơi xuống fallback). Key pre-flight theo primary model là honesty
    tối đa có thể có trước khi gọi; không giải được bằng pre-flight metadata.
    """
    base_url = str(getattr(provider, "base_url", "") or "").strip().lower().rstrip("/")
    model = str(getattr(provider, "model", "") or "").strip().lower()
    return f"{base_url or 'unknown'}|{model or 'unknown'}"


async def cross_verify(
    question: str,
    ai_answer: str,
    context: str = "",
    verdict_tier1: bool = True,
    *,
    gateway: Any | None = None,
) -> dict[str, Any]:
    """Verify one answer using two genuinely distinct provider families.

    Returns ``final=PASS|FAIL`` only when two different provider families
    return parseable and equal verdicts. Missing diversity, provider failure,
    ambiguous output, or disagreement all fail closed with ``final=None``.

    ``gateway`` is injectable so the independence rule can be tested without
    external network access; production callers continue to use get_gateway().
    ``verdict_tier1`` is retained for backward compatibility.
    """
    del verdict_tier1

    from scp.runtime.judge_llm import _judge_system_prompt, _parse_verdict

    if gateway is None:
        from scp.llm_gateway import get_gateway

        gateway = get_gateway()

    prompt = (
        f"System Identity: The AI assistant being evaluated is named SCP (Self-Correcting Process), an intelligent AI assistant.\n"
        f"Question: {question}\nContext: {context}\nAI Answer: {ai_answer}\n"
        "Evaluate if the AI Answer correctly answers the Question based ONLY on "
        "the Context (if provided), the System Identity, or general knowledge. Output only PASS or FAIL."
    )
    # [W8-e1 2026-10-05] Cùng system prompt với cascade chính: gắn ngày hiện
    # tại (seam _current_date) — 2 family crosscheck trước đây chỉ dedupe cùng
    # (base_url, model) nhưng vẫn có thể cùng stale cutoff = consensus "agree"
    # trên claim time-sensitive stale (root-cause q08 W7-battery run C).
    system = _judge_system_prompt()

    seen_families: set[str] = set()
    attempts: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    # [W1-c6] Deadline tổng cho toàn bộ crosscheck: mỗi provider.chat chỉ
    # được dùng phần budget còn lại; quá hạn → attempt record
    # 'timeout:crosscheck_deadline' (verdict None) và, khi budget cạn,
    # fail-closed như thiếu opinion (không bao giờ chờ vô hạn).
    #
    # [W1-c9] Launch-gate TRƯỚC MỖI attempt: attempt nào bắt đầu sau khi
    # budget đã cạn thì KHÔNG được launch — `attempts` chỉ chứa attempt thực
    # sự chạy trong budget. Budget coi như cạn theo hai đường:
    #   (1) đồng hồ: `_now() >= deadline`;
    #   (2) by-construction: attempt trước kết thúc bằng TimeoutError —
    #       wait_for của attempt đó nhận đúng toàn bộ `remaining`, nên khi nó
    #       hết giờ thì budget đã tiêu trọn, bất kể độ phân giải timer.
    # Đường (2) là fix cho Windows CI run 37202332328: wait_for bắn trước
    # deadline tuyệt đối một tick (coarse timer/scheduler), `_now()` đọc được
    # `remaining = +ε` → attempt kế bị launch với budget ~0 rồi lập tức hết giờ
    # và bị ghi vào attempts (attempt ma, audit không tất định theo OS).
    deadline = _now() + _crosscheck_max_seconds()
    deadline_spent = False
    for provider in _candidate_providers(gateway):
        # [W3-e2] family = (base_url, model) — xem _family_key. PROVIDER_NAME
        # không còn được treated là evidence of independence.
        family = _family_key(provider)
        if family in seen_families:
            continue
        # Mark before the request: a second instance of the same family is not
        # an independent opinion, even when the first instance errors.
        seen_families.add(family)

        remaining = deadline - _now()
        if deadline_spent or remaining <= 0:
            logger.warning(
                "[MULTI-LLM] crosscheck deadline (%.1fs budget) exhausted with "
                "%d valid opinion(s) — fail-closed",
                _crosscheck_max_seconds(),
                len(valid),
            )
            break

        try:
            content, provider_label = await asyncio.wait_for(
                provider.chat(
                    prompt,
                    system_prompt=system,
                ),
                timeout=remaining,
            )
            verdict = _parse_verdict(content)
            attempt = {
                "family": family,
                "provider": provider_label,
                "verdict": verdict,
            }
        except asyncio.TimeoutError:
            logger.warning(
                "[MULTI-LLM] crosscheck attempt for family '%s' exceeded the "
                "remaining crosscheck deadline (%.1fs)",
                family,
                remaining,
            )
            # [W1-c9] Timeout này tiêu trọn `remaining` của attempt (wait_for
            # nhận đúng `remaining` làm hạn) → budget cạn by-construction:
            # không launch attempt kế tiếp dù coarse timer còn đọc remaining
            # dương một tick.
            deadline_spent = True
            attempt = {
                "family": family,
                "provider": "timeout:crosscheck_deadline",
                "verdict": None,
            }
        except Exception as exc:
            # per-attempt error is logged below, recorded in the attempt record ('provider': error:<Exc>) and returned to the caller
            logger.debug("LLM crosscheck attempt failed for family '%s': %s", family, exc, exc_info=True)
            attempt = {
                "family": family,
                "provider": f"error:{type(exc).__name__}",
                "verdict": None,
            }

        attempts.append(attempt)
        if attempt["verdict"] in {"PASS", "FAIL"}:
            valid.append(attempt)
            if len(valid) == 2:
                break

    primary = valid[0] if valid else _missing_opinion()
    secondary = valid[1] if len(valid) > 1 else _missing_opinion()

    if len(valid) < 2:
        consensus = "missing_distinct_providers"
        final = None
    elif primary["verdict"] == secondary["verdict"]:
        consensus = "agree"
        final = primary["verdict"]
    else:
        consensus = "disagree"
        final = None

    logger.info(
        "[MULTI-LLM] primary(%s)=%s secondary(%s)=%s consensus=%s final=%s",
        primary["provider"],
        primary["verdict"],
        secondary["provider"],
        secondary["verdict"],
        consensus,
        final,
    )
    return {
        "consensus": consensus,
        "primary": primary,
        "secondary": secondary,
        "final": final,
        "attempts": attempts,
        "distinct_families_attempted": [item["family"] for item in attempts],
    }
