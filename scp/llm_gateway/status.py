"""Truthful LLM gateway status: configured != usable. [C1 wire-or-kill 2026-10-30]

``build_llm_status`` đọc trạng thái RUNTIME thật của các provider (dead-model
registry do chính phản hồi 404/402/429 của provider ghi) — không tự suy đoán,
không bịa PASS. Dùng cho /ready, /health/detailed và nhãn "judge unavailable".

state:
  unconfigured - chưa có provider nào được cấu hình (chế độ local_verified hợp lệ)
  unavailable  - có provider cấu hình nhưng KHÔNG cái nào dùng được (vd model 404)
  degraded     - một phần provider cấu hình đã chết
  ok           - mọi provider cấu hình đều dùng được
Không chứa key/secret; chỉ tên provider, model id, mã lý do.
"""
from __future__ import annotations

from typing import Any


def _chain_state(gateway: Any, task: str) -> dict[str, Any]:
    try:
        chain = gateway._provider_chain(task)
    except Exception as exc:  # pragma: no cover - phòng hộ, fail-closed
        return {"state": "unavailable", "usable": 0, "reason": f"chain_error:{type(exc).__name__}"}
    configured = [p for p in chain if getattr(p, "enabled", False)]
    usable = [p for p in configured if getattr(p, "usable", True)]
    if usable:
        return {"state": "ok", "usable": len(usable), "reason": ""}
    reason = "no_usable_llm_provider" if configured else "no_llm_provider_configured"
    return {"state": "unavailable", "usable": 0, "reason": reason}


def build_llm_status(gateway: Any) -> dict[str, Any]:
    seen: set[tuple[str, str]] = set()
    usable_names: list[str] = []
    unusable: list[dict[str, str]] = []
    warnings: list[dict[str, Any]] = []
    providers: list[Any] = []
    for name in ("openrouter_default",):
        prov = getattr(gateway, name, None)
        if prov is not None:
            providers.append(prov)
    providers.extend(getattr(gateway, "_extra_providers", {}).get("default", []))
    for p in providers:
        if not getattr(p, "enabled", False):
            continue
        ident = (str(getattr(p, "PROVIDER_NAME", "")), str(getattr(p, "base_url", "")))
        if ident in seen:
            continue
        seen.add(ident)
        reason = p.unusable_reason() if hasattr(p, "unusable_reason") else ""
        if reason:
            unusable.append({"provider": ident[0], "model": str(getattr(p, "model", "")), "reason": reason})
        else:
            usable_names.append(ident[0])
        warn = p.decommission_warning() if hasattr(p, "decommission_warning") else None
        if warn:
            warnings.append(warn)
    if not seen:
        state = "unconfigured"
    elif not usable_names:
        state = "unavailable"
    elif unusable:
        state = "degraded"
    else:
        state = "ok"
    return {
        "state": state,
        "usable_providers": usable_names,
        "unusable_providers": unusable,
        "warnings": warnings,
        "judge": _chain_state(gateway, "judge"),
    }
