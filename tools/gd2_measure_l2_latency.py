#!/usr/bin/env python3
"""GĐ2 Lane C — đo latency THẬT của L2 (SLM qua LLM Gateway) — CHẠY THỦ CÔNG.

Mục đích (ag-tasks-4.md NV1: "L2 = API thật qua gateway (GLM), latency phải
đo thật — thay con số 60-90ms chưa căn cứ bằng placeholder chờ đo"): script
này là probe đo p50/p95/p99 của một lần classify L2 end-to-end qua gateway
THẬT (task "route", `scp/llm_gateway/client.py:152,316`). Kết quả là căn cứ
để đặt lại ngân sách ``SCP_GD2_L2_TIMEOUT_MS`` (default 250ms) — con số
hiện tại là placeholder cho đến khi có số đo từ script này.

KỶ LUẬT CHẠY (buộc):
- CHỈ hub/người chạy thủ công. KHÔNG tự động chạy trong CI, agent loop,
  hay workflow — script gọi mạng thật qua gateway.
- KHÔNG hardcode key, KHÔNG đọc/ghi file .env: gateway nạp key từ ENV CỦA
  NGƯỜI CHẠY (xuống dòng lệnh của bạn phải có env provider — vd
  OPENROUTER_API_KEY / SCP_LLM_PREFERRED_PROVIDER=glm).
- Không log/in ra raw text probe dưới dạng user data: bảng kết quả chỉ in
  text id + sha256[:16] (cùng kỷ luật log như lineage_slm.py (e)).

Cách chạy (từ repo root, sau khi export key vào env):
    .venv/Scripts/python.exe tools/gd2_measure_l2_latency.py
    .venv/Scripts/python.exe tools/gd2_measure_l2_latency.py --calls 20

Exit codes:
    0 = đo xong, có ≥1 call thành công;
    2 = không có provider nào active (thiếu key trong env) — từ chối đo;
    3 = mọi call đều degraded (không có số liệu latency hợp lệ).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scp.runtime.semantic_router.lineage_slm import (  # noqa: E402
    L2_GATEWAY_TASK,
    SLMLineage,
    input_hash,
    parse_timeout_ms,
    preferred_provider,
)

#: Text probe tổng hợp (không phải dữ liệu người dùng; nằm cố định trong file).
#: 4 dạng ngôn ngữ/loại câu cơ bản — KHÔNG chứa payload injection.
PROBE_TEXTS: tuple[tuple[str, str], ...] = (
    ("greeting_vi", "Chào bạn, hôm nay bạn thế nào?"),
    ("factual_vi", "Thủ đô của Bồ Đào Nha là thành phố nào?"),
    ("math_vi", "Tổng của 128 và 371 bằng bao nhiêu?"),
    ("chat_en", "Hey, tell me a fun fact about otters."),
)

PERCENTILES: tuple[float, ...] = (50.0, 95.0, 99.0)


def _percentile_nearest_rank(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile (rank = ceil(p/100 * n)) — không nội suy."""
    if not sorted_values:
        raise ValueError("empty")
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


async def _measure(lineage: SLMLineage, calls: int) -> list[dict]:
    results: list[dict] = []
    for i in range(calls):
        text_id, text = PROBE_TEXTS[i % len(PROBE_TEXTS)]
        outcome = await lineage.classify(text, request_id=f"measure-{i:03d}")
        results.append(
            {
                "call": i,
                "text_id": text_id,
                "input_sha256": input_hash(text),
                "degraded": outcome.degraded,
                "reason": outcome.reason,
                "label": outcome.verdict.label if outcome.verdict else None,
                "confidence": outcome.verdict.confidence if outcome.verdict else None,
                "elapsed_ms": round(outcome.elapsed_ms, 3),
                "provider": outcome.provider,
            }
        )
        print(
            f"[{i + 1:2d}/{calls}] {text_id:<12} "
            f"{'OK      ' if not outcome.degraded else 'DEGRADED'} "
            f"label={results[-1]['label'] or '-':<8} "
            f"{outcome.elapsed_ms:8.1f} ms  reason={outcome.reason} "
            f"provider={outcome.provider or '-'}"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Đo latency THẬT của L2 semantic router qua LLM Gateway (chạy thủ công)."
    )
    parser.add_argument(
        "--calls", type=int, default=20, help="Số lần gọi (default 20 — theo yêu cầu đo)."
    )
    parser.add_argument(
        "--timeout-ms",
        type=float,
        default=30_000.0,
        help=(
            "Timeout đo (default 30000ms — LỚN hơn ngân sách router để đo được "
            "latency thật thay vì cắt ở DEGRADED; ngân sách router báo riêng ở "
            "budget_ms)."
        ),
    )
    parser.add_argument(
        "--budget-ms",
        type=float,
        default=None,
        help=(
            "Ngân sách router để đối chiếu would-degrade (default: đọc env "
            "SCP_GD2_L2_TIMEOUT_MS, fallback 250)."
        ),
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, help="Ghi báo cáo JSON ra file (tùy chọn)."
    )
    args = parser.parse_args(argv)

    if args.calls < 1:
        parser.error("--calls phải ≥ 1")

    # Import gateway SAU khi parse args — --help không chạm gateway/network.
    from scp.llm_gateway.client import get_gateway  # noqa: WPS433

    gateway = get_gateway()
    active = gateway.get_active_provider_names()
    if not gateway.has_active_providers():
        print(
            "REFUSED: không có provider nào active — key phải có sẵn trong ENV "
            "của người chạy (script không đọc .env, không hardcode key).",
            file=sys.stderr,
        )
        return 2

    budget_ms = args.budget_ms if args.budget_ms is not None else parse_timeout_ms()
    lineage = SLMLineage(timeout_ms=args.timeout_ms)

    print(f"gd2 L2 latency probe — {datetime.now(timezone.utc).isoformat()}")
    print(f"providers_active={active}")
    print(f"preferred_provider={preferred_provider() or '(none)'}")
    print(f"gateway_task={L2_GATEWAY_TASK}  calls={args.calls}")
    print(f"measure_timeout_ms={args.timeout_ms}  router_budget_ms={budget_ms}")
    print("-" * 78)

    results = asyncio.run(_measure(lineage, args.calls))

    ok_latencies = sorted(r["elapsed_ms"] for r in results if not r["degraded"])
    degraded_reasons: dict[str, int] = {}
    for r in results:
        if r["degraded"]:
            degraded_reasons[r["reason"]] = degraded_reasons.get(r["reason"], 0) + 1

    over_budget = sum(1 for ms in ok_latencies if ms > budget_ms)
    stats: dict[str, float] = {}
    if ok_latencies:
        stats = {
            "n_ok": len(ok_latencies),
            "min_ms": round(ok_latencies[0], 3),
            "mean_ms": round(statistics.fmean(ok_latencies), 3),
            **{
                f"p{int(p)}_ms": round(_percentile_nearest_rank(ok_latencies, p), 3)
                for p in PERCENTILES
            },
            "max_ms": round(ok_latencies[-1], 3),
        }

    report = {
        "tool": "tools/gd2_measure_l2_latency.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gateway_task": L2_GATEWAY_TASK,
        "providers_active": active,
        "preferred_provider": preferred_provider(),
        "calls": args.calls,
        "measure_timeout_ms": args.timeout_ms,
        "router_budget_ms": budget_ms,
        "stats_ok": stats,
        "degraded_reasons": degraded_reasons,
        "would_degrade_under_budget": over_budget,
        "would_degrade_ratio_under_budget": (
            round(over_budget / len(ok_latencies), 4) if ok_latencies else None
        ),
        "per_call": results,
    }

    print("-" * 78)
    print(json.dumps({k: v for k, v in report.items() if k != "per_call"}, indent=2))
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"json written: {args.json_out}")
    if not ok_latencies:
        print("NO LATENCY DATA: mọi call đều degraded.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
