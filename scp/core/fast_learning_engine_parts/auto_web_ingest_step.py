"""[WIRE-INGESTOR 2026-10-25] Bước ingest web tự động — writer cho kho CanonicalFTS.

[OWNER-MANDATE 2026-10-25 · Wave 2b] Nối AutoWebIngestor (Wave W23) vào luồng
học chính. Trước bước này kho ``kb_fts`` có 3 điểm ĐỌC trong /ask
(domain_data_fork, domain_data_extended, atomic_verifier) nhưng 0 điểm GHI
trong product code — nghịch lý "kho có người đọc, không có người ghi".

Điểm nối (xem start_fast_learning_thread.py): ``learning_loop()`` gọi
``_post_cycle_ingest(engine)`` SAU khi 1 chu kỳ ``fast_learning_cycle`` hoàn
tất (GIỮA 2 chu kỳ) — KHÔNG đụng vào ``fast_learning_cycle()``, không chặn
RSS/LLM chủ; mọi exception bị nuốt ở cấp step (log + continue).

Tầng bảo vệ — tất cả dùng CƠ CHẾ CÓ SẴN của ingestor, không tự chế:
  * Tier-1 SSRF/allowlist: chỉ URL https thuộc TRUSTED_INGESTION_DOMAINS.
    NYT/BBC/VNExpress KHÔNG nằm trong allowlist nên KHÔNG thể seed bằng RSS
    items — đúng thiết kế fail-closed, patch này KHÔNG nới gate nào.
  * Egress: mọi fetch đi qua ``safe_urlopen`` → ``enforce_egress_policy``
    (SCP_EGRESS_MODE unset/invalid = DENY fail-closed; không fetch = không egress).
  * Tier-2: quarantine staging + prompt-injection filter (REJECTED_POISONED).
  * Tier-3: cross-verify ≥2 host độc lập (hoặc Tier-1 axiomatic authority)
    + HMAC VerifierReceipt (SCP_CAPABILITY_SECRET — fail-closed N-01) rồi mới
    upsert vào CanonicalFTSWarehouse; /ask đọc kèm TTL fail-closed.
  * Kill switch: ``scp.security.kill_switch.is_engaged()`` — authority hợp
    nhất (file flag + env SCP_KILL_SWITCH; kênh kernel bỏ qua tường minh vì
    learning thread không giữ TaskKernel — documented skip, file+env vẫn phủ).
  * Env gate ``SCP_AUTO_WEB_INGEST`` (default "1"): bật mặc định vì (a) mọi
    lớp bảo vệ trên đã tồn tại và được test pin; (b) thread học đã tự opt-in
    (SCP_FAST_LEARNING_THREAD=1) nên step chỉ chạy khi thread chính chạy;
    (c) budget cap chặt. Tắt: "0"/"false"/"off"/"no".
  * Budget: ``SCP_AUTO_WEB_INGEST_BUDGET`` (default 3, clamp 1..5 URL/chu kỳ);
    giá trị hỏng → default (fail-safe, không crash).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Sequence

logger = logging.getLogger("scp.core.auto_web_ingest_step")

ENV_GATE = "SCP_AUTO_WEB_INGEST"
ENV_BUDGET = "SCP_AUTO_WEB_INGEST_BUDGET"
DEFAULT_BUDGET = 3
MAX_BUDGET = 5
DEFAULT_TTL_SECONDS = 3600
_GATE_OFF_VALUES = {"0", "false", "off", "no"}

# [WIRE-INGESTOR 2026-10-25] Seed catalog — CHỈ dùng domain đã có trong
# TRUSTED_INGESTION_DOMAINS (Tier-1 allowlist của ingestor). Mỗi chu kỳ chỉ
# chạy tối đa ``budget`` seed đầu tiên. usgs là Tier-1 axiomatic authority
# (promote 1 nguồn theo thiết kế W23); worldbank/frankfurter là nguồn thường
# → staging sẽ đợi nguồn thứ hai độc lập khớp số liệu (WAITING_SECOND_
# INDEPENDENT_SOURCE) hoặc cross-verify QUA CÁC CHU KỲ nhờ quarantine staging
# bền (staging_id keyed theo content hash, INSERT OR REPLACE idempotent).
DEFAULT_INGEST_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "topic_key": "usgs_earthquake_feed_hour",
        "domain": "Geology",
        "title": "USGS Earthquake Summary Feed (hourly)",
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.csv",
        "keywords": "earthquake seismic usgs geology magnitude",
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    },
    {
        "topic_key": "worldbank_macro_population",
        "domain": "Macro",
        "title": "World Bank World Population Indicator",
        "url": "https://api.worldbank.org/v2/country/WLD/indicator/SP.POP.TOTL?format=json&per_page=3",
        "keywords": "population macro worldbank indicator",
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    },
    {
        "topic_key": "frankfurter_fx_eur_usd",
        "domain": "Finance",
        "title": "ECB Reference FX Rates EUR/USD",
        "url": "https://api.frankfurter.app/latest?from=EUR&to=USD",
        "keywords": "exchange rate fx eur usd ecb",
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    },
    {
        "topic_key": "open_meteo_hanoi_forecast",
        "domain": "Weather",
        "title": "Open-Meteo Forecast Hanoi",
        "url": "https://api.open-meteo.com/v1/forecast?latitude=21.03&longitude=105.85&current_weather=true",
        "keywords": "weather forecast hanoi temperature",
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    },
    {
        "topic_key": "coingecko_btc_price",
        "domain": "Finance",
        "title": "CoinGecko Bitcoin Price",
        "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        "keywords": "bitcoin price crypto finance",
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    },
)


def _gate_enabled() -> bool:
    """[WIRE-INGESTOR 2026-10-25] Env gate opt-out: chỉ giá trị tắt rõ ràng
    mới tắt; unset → bật (mọi lớp bảo vệ đã fail-closed bên dưới)."""
    raw = str(os.environ.get(ENV_GATE, "1") or "1").strip().lower()
    return raw not in _GATE_OFF_VALUES


def _resolve_budget() -> int:
    """[WIRE-INGESTOR 2026-10-25] Budget URL mỗi chu kỳ: default 3, clamp
    1..MAX_BUDGET; parse lỗi → default (fail-safe, không bao giờ raise)."""
    raw = os.environ.get(ENV_BUDGET)
    if raw is None or not str(raw).strip():
        return DEFAULT_BUDGET
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_BUDGET
    return max(1, min(MAX_BUDGET, value))


def run_auto_web_ingest_step(
    engine: Any = None,
    *,
    ingestor_factory: Callable[[], Any] | None = None,
    seeds: Sequence[dict[str, Any]] | None = None,
    budget: int | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """[WIRE-INGESTOR 2026-10-25] Chạy 1 lần ingest giữa 2 chu kỳ học.

    KHÔNG BAO GIỜ raise — mọi exception được nuốt vào ``result["errors"]``
    (thread học phải sống kể cả khi ingest hỏng). Kill switch / gate off /
    budget 0 → skip tường minh qua ``result["gated"]``.

    Args:
        engine: FastLearningEngine instance (reserved — step không đụng state
            của engine, giữ cho signature hook ổn định).
        ingestor_factory: seam test (fixture factory trả AutoWebIngestor với
            warehouse/fetcher tmp — KHÔNG dùng mock); None → AutoWebIngestor
            chuẩn với warehouse singleton ``get_canonical_fts_warehouse()``.
        seeds: override seed catalog (test); None → DEFAULT_INGEST_SEEDS.
        budget: override budget (test); None → env/default.
        now_epoch: pass-through cho reconcile (test TTL).
    """
    result: dict[str, Any] = {
        "ran": False,
        "gated": None,
        "staged": [],
        "promoted": [],
        "errors": [],
    }

    # 1) Kill switch — authority hợp nhất, fail-closed (kể cả khi probe lỗi).
    try:
        from scp.security.kill_switch import is_engaged

        if is_engaged():
            result["gated"] = "kill_switch_engaged"
            logger.info("[WIRE-INGESTOR] skip: kill switch engaged")
            return result
    except Exception as exc:
        result["errors"].append(f"kill_switch_probe:{type(exc).__name__}:{exc}")
        logger.warning("[WIRE-INGESTOR] kill switch probe failed — fail-closed skip: %s", exc)
        return result

    # 2) Env gate opt-out.
    if not _gate_enabled():
        result["gated"] = "env_gate_off"
        return result

    # 3) Budget cap.
    eff_budget = _resolve_budget() if budget is None else max(1, min(MAX_BUDGET, int(budget)))
    selected = list(seeds if seeds is not None else DEFAULT_INGEST_SEEDS)[:eff_budget]
    if not selected:
        result["gated"] = "no_seeds_selected"
        return result

    # 4) Ingestor (Tier 1+2+3 của nó tự enforce allowlist/egress/poison/HMAC).
    try:
        from scp.data_sources.auto_web_ingestor import AutoWebIngestor

        factory = ingestor_factory or AutoWebIngestor
        ingestor = factory()
    except Exception as exc:
        result["errors"].append(f"ingestor_init:{type(exc).__name__}:{exc}")
        logger.warning("[WIRE-INGESTOR] ingestor init failed (non-fatal): %s", exc)
        return result

    for seed in selected:
        topic_key = str(seed.get("topic_key") or "").strip()
        try:
            stage = ingestor.stage_from_url(
                topic_key=topic_key,
                domain=str(seed.get("domain") or "General"),
                title=str(seed.get("title") or topic_key),
                url=str(seed.get("url") or ""),
                ttl_seconds=int(seed.get("ttl_seconds") or DEFAULT_TTL_SECONDS),
            )
            result["staged"].append(
                {
                    "topic_key": topic_key,
                    "status": str(stage.get("status")),
                    "ok": bool(stage.get("ok")),
                }
            )
            promo = ingestor.reconcile_and_promote(
                topic_key,
                keywords=str(seed.get("keywords") or ""),
                now_epoch=now_epoch,
            )
            if promo.get("promoted"):
                result["promoted"].append(
                    {
                        "topic_key": topic_key,
                        "doc_id": str(promo.get("doc_id")),
                        "epistemic_tier": str(promo.get("epistemic_tier")),
                        "corroborated_hosts": list(promo.get("corroborated_hosts") or []),
                    }
                )
        except Exception as exc:
            # Cách ly từng seed: 1 seed hỏng không chặn các seed còn lại.
            result["errors"].append(f"seed:{topic_key or '?'}:{type(exc).__name__}:{exc}")
            logger.warning("[WIRE-INGESTOR] seed %s failed (isolated): %s", topic_key, exc)
            continue

    result["ran"] = True
    logger.info(
        "[WIRE-INGESTOR] cycle ingest done: staged=%d promoted=%d errors=%d",
        len(result["staged"]),
        len(result["promoted"]),
        len(result["errors"]),
    )
    return result
