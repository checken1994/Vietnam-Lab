"""Shared OpenRouter catalog refresh with P0 zero-cost pricing proofs.

A model name or hardcoded allowlist is discovery data only. Authorization comes
from a FRESH immutable PricingProof recording prompt AND completion prices.
Refresh fetches the full catalog so a model changing free -> paid immediately
produces a newer paid proof instead of remaining free until an old proof expires.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from scp.contracts.data_class import DataClass
from scp.epistemic.evidence_store import EvidenceStore
from scp.epistemic.evidence_writer import GovernedEvidenceWriter
from scp.governance.privacy import PrivacyWriteGate
from scp.llm_gateway.egress_policy import llm_egress_allowed

from scp.security.url_safety import enforce_egress_policy  # [EE-G1]

logger = logging.getLogger("scp.llm_gateway.free_catalog")

OPENROUTER_CATALOG_URL = "https://openrouter.ai/api/v1/models"
FREE_CATALOG_TIMEOUT_SEC = 5
FREE_CATALOG_REFRESH_SEC = 21600  # 6h background refresh

_lock = threading.Lock()
_fetched = False
_last_ok: float | None = None
_refresh_thread_started = False

_ROOT = Path(__file__).resolve().parents[2]
_FOUNDATION = _ROOT / "data" / "foundation"


def _fetch_catalog_models(timeout: float = FREE_CATALOG_TIMEOUT_SEC) -> list | None:
    """Fetch the full provider catalog; None means forbidden/failure."""
    if not llm_egress_allowed(OPENROUTER_CATALOG_URL):
        logger.info("[free_catalog] external refresh skipped by SCP LLM egress policy")
        return None
    try:
        # [EE-G1] generic egress gate (idempotent): SCP_EGRESS_MODE áp cho cả
        # catalog fetch, cùng lớp với các fetcher chuẩn. EgressDeniedError →
        # except dưới → None ("forbidden/failure" contract giữ nguyên).
        enforce_egress_policy(OPENROUTER_CATALOG_URL)
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(OPENROUTER_CATALOG_URL)
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", [])
            return data if isinstance(data, list) else None
    except Exception:
        logger.warning('_fetch_catalog_models: Exception not handled', exc_info=True)
        return None


def _fetch_free_models(timeout: float = FREE_CATALOG_TIMEOUT_SEC) -> list | None:
    """Backward-compatible helper: fetch catalog and return only exact $0 models."""
    catalog = _fetch_catalog_models(timeout)
    if catalog is None:
        return None
    out = []
    for model in catalog:
        pricing = model.get("pricing", {}) if isinstance(model, dict) else {}
        try:
            if float(pricing.get("prompt")) == 0 and float(pricing.get("completion")) == 0:
                out.append(model)
        except (TypeError, ValueError):
            logger.debug('_fetch_free_models: TypeError, ValueError ignored', exc_info=True)
            continue
    return out


def _text_capable(model: dict) -> bool:
    modalities = model.get("output_modalities")
    if modalities is None:
        modalities = model.get("architecture", {}).get("output_modalities")
    if not isinstance(modalities, list):
        modalities = []
    for mod in modalities:
        mod_str = str(mod).lower()
        if "audio" in mod_str or "video" in mod_str:
            return False
    return True


def _sort_models(models: list) -> list:
    return sorted(
        models,
        key=lambda model: (-int(model.get("context_length", 0) or 0), model.get("id", "")),
    )


def _persist_pricing_proofs(catalog: list) -> bool:
    """Persist one evidence object for the catalog and per-model immutable proofs."""
    canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    catalog_hash = "sha256:" + hashlib.sha256(canonical).hexdigest()
    evidence = EvidenceStore(_FOUNDATION / "epistemic.sqlite", _FOUNDATION / "evidence_objects")
    proofs = PricingProofStore(_FOUNDATION / "zero_cost.sqlite")
    try:
        writer = GovernedEvidenceWriter(
            evidence,
            PrivacyWriteGate(_ROOT / "spec" / "data_policies.yaml"),
        )
        ev = writer.observe(
            kind="HTTP_RESPONSE",
            content=canonical,
            collector_id="openrouter-free-catalog",
            collector_version="p0-z1",
            data_class=DataClass.PUBLIC,
            metadata={"url": OPENROUTER_CATALOG_URL, "catalog_hash": catalog_hash},
        )
        observed = datetime.now(timezone.utc)
        expires = observed + timedelta(seconds=FREE_CATALOG_REFRESH_SEC)
        for model in catalog:
            if not isinstance(model, dict) or not model.get("id"):
                continue
            pricing = model.get("pricing") or {}
            prompt = pricing.get("prompt")
            completion = pricing.get("completion")
            pricing_unknown = prompt is None or completion is None
            # Unknown pricing gets a conservative non-zero sentinel proof so a
            # newer catalog observation can never leave an older free proof
            # authoritative. It is therefore denied at the PEP.
            if pricing_unknown:
                prompt = "1"
                completion = "1"
            try:
                proofs.record(
                    provider="openrouter",
                    model=str(model["id"]),
                    prompt_price=prompt,
                    completion_price=completion,
                    catalog_hash=catalog_hash,
                    evidence_id=ev["evidence_id"],
                    observed_at=observed.isoformat(),
                    expires_at=expires.isoformat(),
                    metadata={"pricing_unknown": pricing_unknown},
                )
            except (TypeError, ValueError):
                # Malformed pricing is fail-closed: store a paid sentinel proof.
                logger.debug('_persist_pricing_proofs: TypeError, ValueError ignored', exc_info=True)
                proofs.record(
                    provider="openrouter",
                    model=str(model["id"]),
                    prompt_price="1",
                    completion_price="1",
                    catalog_hash=catalog_hash,
                    evidence_id=ev["evidence_id"],
                    observed_at=observed.isoformat(),
                    expires_at=expires.isoformat(),
                    metadata={"pricing_unknown": True, "malformed": True},
                )
        return True
    except Exception:
        logger.exception("[free_catalog] pricing proof persistence failed")
        return False
    finally:
        proofs.close()
        evidence.db.close()


def refresh_free_catalog(force: bool = False) -> bool:
    """Refresh discovery allowlist AND authoritative pricing proofs.

    If fetch/proof persistence fails, no new model is authorized by this
    refresh. Existing proofs expire naturally; stale/unknown proof is denied by
    ZeroCostGuard. The hardcoded model-name list is never authorization proof.
    """
    global _fetched, _last_ok
    with _lock:
        if _fetched and not force:
            return bool(_last_ok)
        _fetched = True
    catalog = _fetch_catalog_models()
    if not catalog:
        logger.warning("[free_catalog] catalog fetch failed/empty - no fresh pricing proof")
        return False
    if not _persist_pricing_proofs(catalog):
        return False

    free_models = []
    for model in _sort_models(catalog):
        if not _text_capable(model):
            continue
        pricing = model.get("pricing") or {}
        try:
            if float(pricing.get("prompt")) == 0 and float(pricing.get("completion")) == 0:
                free_models.append(model)
        except (TypeError, ValueError):
            logger.debug('refresh_free_catalog: TypeError, ValueError ignored', exc_info=True)
            continue
    if not free_models:
        logger.warning("[free_catalog] fresh catalog contains no text-capable exact-$0 models")
        return False

    from scp.llm_gateway import client as gateway_client

    gateway_client.OPENROUTER_FREE_MODELS = [model["id"] for model in free_models]
    _last_ok = time.time()
    logger.info("[free_catalog] refreshed %d exact-$0 models with fresh pricing proof", len(free_models))
    return True


def start_background_refresh() -> None:
    global _refresh_thread_started
    with _lock:
        if _refresh_thread_started:
            return
        _refresh_thread_started = True

    def loop() -> None:
        while True:
            time.sleep(FREE_CATALOG_REFRESH_SEC)
            try:
                refresh_free_catalog(force=True)
            except Exception:
                logger.exception("[free_catalog] background refresh failed")

    threading.Thread(target=loop, daemon=True, name="llm-gateway-free-catalog-refresh").start()
