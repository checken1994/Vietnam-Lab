r"""Operator-configured local model discovery and lifecycle management (S35).

The scanner only contacts endpoints supplied by the operator through the
constructor or ``SCP_LOCAL_ENDPOINTS``.  It never accepts an endpoint from a
model response, prompt, or web request.  Every HTTP request crosses the
canonical egress gate; configured loopback/on-prem endpoints are the explicit
operator opt-in for internal probing.

Lifecycle contract:

    DISCOVERED -> QUARANTINED -> QUALIFIED -> ACTIVE
                              \-> COOLDOWN -> QUARANTINED (bounded reprobe)

A successful rediscovery reconciles the endpoint's complete model list.  A
model missing from ``/v1/models`` is first marked ``STALE`` and, if it is
still absent on the next successful scan, ``RETIRED``.  A failed endpoint scan
also withdraws models fail-closed (`STALE`, then `RETIRED`) without deleting
state; recovery requires a fresh discovery and probe.  Neither state is
routable.

"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from scp.contracts.time import now_utc_iso, parse_utc_iso
from scp.llm_gateway.prober import ContractProber
from scp.persistence import FoundationDB
from scp.security.url_safety import enforce_egress_policy, validate_url

logger = logging.getLogger("scp.llm_gateway.discovery")


class ModelLifecycleState(str, Enum):
    """Lifecycle states for operator-discovered models."""

    DISCOVERED = "DISCOVERED"
    QUARANTINED = "QUARANTINED"
    QUALIFIED = "QUALIFIED"
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    STALE = "STALE"
    RETIRED = "RETIRED"


_DISCOVERY_MIGRATIONS = [
    (
        "0001_discovery_state",
        [
            """CREATE TABLE IF NOT EXISTS model_lifecycle (
                   endpoint TEXT NOT NULL,
                   model_id TEXT NOT NULL,
                   state TEXT NOT NULL,
                   discovered_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   PRIMARY KEY (endpoint, model_id)
            )"""
        ],
    ),
    (
        "0002_lifecycle_observations",
        [
            "ALTER TABLE model_lifecycle ADD COLUMN last_seen_at TEXT",
            "ALTER TABLE model_lifecycle ADD COLUMN last_probe_at TEXT",
            "ALTER TABLE model_lifecycle ADD COLUMN next_probe_at TEXT",
            "ALTER TABLE model_lifecycle ADD COLUMN probe_failures INTEGER NOT NULL DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS idx_model_lifecycle_endpoint ON model_lifecycle(endpoint)",
        ],
    ),
]

_UNSET = object()


class ModelDiscoveryStore:
    """Persistent lifecycle state backed by the FoundationDB SQLite authority."""

    def __init__(self, db_path: str | Path) -> None:
        self.db = FoundationDB(db_path, _DISCOVERY_MIGRATIONS)

    def upsert_model(
        self,
        endpoint: str,
        model_id: str,
        state: ModelLifecycleState,
        *,
        last_seen_at: str | None | object = _UNSET,
        last_probe_at: str | None | object = _UNSET,
        next_probe_at: str | None | object = _UNSET,
        probe_failures: int | object = _UNSET,
    ) -> None:
        """Insert or update state while preserving unspecified observations."""
        now = now_utc_iso()
        metadata = {
            "last_seen_at": last_seen_at,
            "last_probe_at": last_probe_at,
            "next_probe_at": next_probe_at,
            "probe_failures": probe_failures,
        }
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM model_lifecycle WHERE endpoint = ? AND model_id = ?",
                (endpoint, model_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO model_lifecycle (
                           endpoint, model_id, state, discovered_at, updated_at,
                           last_seen_at, last_probe_at, next_probe_at, probe_failures
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        endpoint,
                        model_id,
                        state.value,
                        now,
                        now,
                        None if last_seen_at is _UNSET else last_seen_at,
                        None if last_probe_at is _UNSET else last_probe_at,
                        None if next_probe_at is _UNSET else next_probe_at,
                        0 if probe_failures is _UNSET else int(probe_failures),
                    ),
                )
                return

            assignments = ["state = ?", "updated_at = ?"]
            params: list[Any] = [state.value, now]
            for column, value in metadata.items():
                if value is not _UNSET:
                    assignments.append(f"{column} = ?")
                    params.append(int(value) if column == "probe_failures" else value)
            params.extend([endpoint, model_id])
            conn.execute(
                "UPDATE model_lifecycle SET "
                + ", ".join(assignments)
                + " WHERE endpoint = ? AND model_id = ?",
                params,
            )

    def update_model(
        self,
        endpoint: str,
        model_id: str,
        *,
        state: ModelLifecycleState | object = _UNSET,
        last_seen_at: str | None | object = _UNSET,
        last_probe_at: str | None | object = _UNSET,
        next_probe_at: str | None | object = _UNSET,
        probe_failures: int | object = _UNSET,
    ) -> None:
        """Atomically update selected lifecycle fields for an existing model."""
        assignments = ["updated_at = ?"]
        params: list[Any] = [now_utc_iso()]
        values: Mapping[str, Any] = {
            "state": state,
            "last_seen_at": last_seen_at,
            "last_probe_at": last_probe_at,
            "next_probe_at": next_probe_at,
            "probe_failures": probe_failures,
        }
        for column, value in values.items():
            if value is not _UNSET:
                assignments.append(f"{column} = ?")
                if column == "probe_failures":
                    params.append(int(value))
                elif isinstance(value, ModelLifecycleState):
                    params.append(value.value)
                else:
                    params.append(value)
        params.extend([endpoint, model_id])
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE model_lifecycle SET "
                + ", ".join(assignments)
                + " WHERE endpoint = ? AND model_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown discovered model: {endpoint}/{model_id}")

    def get_model_state(
        self, endpoint: str, model_id: str
    ) -> ModelLifecycleState | None:
        """Retrieve current lifecycle state for an endpoint and model ID."""
        rows = self.db.query(
            "SELECT state FROM model_lifecycle WHERE endpoint = ? AND model_id = ?",
            (endpoint, model_id),
        )
        if rows:
            return ModelLifecycleState(rows[0]["state"])
        return None

    def get_model(self, endpoint: str, model_id: str) -> dict[str, Any] | None:
        rows = self.db.query(
            """SELECT endpoint, model_id, state, discovered_at, updated_at,
                      last_seen_at, last_probe_at, next_probe_at, probe_failures
               FROM model_lifecycle
               WHERE endpoint = ? AND model_id = ?""",
            (endpoint, model_id),
        )
        return rows[0] if rows else None

    def list_models(self) -> list[dict[str, Any]]:
        """List all tracked models, including durable observation metadata."""
        return self.db.query(
            """SELECT endpoint, model_id, state, discovered_at, updated_at,
                      last_seen_at, last_probe_at, next_probe_at, probe_failures
               FROM model_lifecycle ORDER BY endpoint, model_id"""
        )

    def get_models_by_endpoint(self, endpoint: str) -> list[dict[str, Any]]:
        return self.db.query(
            """SELECT endpoint, model_id, state, discovered_at, updated_at,
                      last_seen_at, last_probe_at, next_probe_at, probe_failures
               FROM model_lifecycle WHERE endpoint = ? ORDER BY model_id""",
            (endpoint,),
        )

    def is_model_active(self, endpoint: str, model_id: str) -> bool:
        """Return whether a discovered endpoint/model is currently routable."""
        row = self.get_model(endpoint, model_id)
        return bool(row and row["state"] == ModelLifecycleState.ACTIVE.value)

    def get_models_by_state(
        self, state: ModelLifecycleState
    ) -> list[dict[str, Any]]:
        return self.db.query(
            """SELECT endpoint, model_id, state, discovered_at, updated_at,
                      last_seen_at, last_probe_at, next_probe_at, probe_failures
               FROM model_lifecycle WHERE state = ? ORDER BY endpoint, model_id""",
            (state.value,),
        )

    def close(self) -> None:
        """Close the underlying durable database connection."""
        self.db.close()


# Lifecycle timing is intentionally bounded.  Operators may shorten values for
# a local deployment, but a malformed/huge value cannot create a busy loop or an
# unbounded retry storm.
DEFAULT_REPROBE_COOLDOWN_SECONDS = 300.0
MAX_REPROBE_COOLDOWN_SECONDS = 86400.0
DEFAULT_MODEL_DISCOVERY_INTERVAL_SECONDS = 300.0
MAX_MODEL_DISCOVERY_INTERVAL_SECONDS = 86400.0


def _bounded_seconds(
    raw: Any,
    *,
    default: float,
    maximum: float,
) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return min(value, maximum)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _operator_endpoint(value: Any) -> str | None:
    """Validate an endpoint supplied by the operator before any transport."""
    if not isinstance(value, str) or not value.strip():
        return None
    endpoint = value.strip().rstrip("/")
    try:
        parsed = validate_url(endpoint, allow_internal=True)
    except (TypeError, ValueError) as exc:
        logger.warning("Ignoring invalid configured model endpoint: %s", exc)
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        logger.warning("Ignoring configured model endpoint with credentials/query/fragment")
        return None
    return endpoint


def _provider_endpoint_variants(endpoint: Any) -> set[str]:
    """Return safe root/API variants for matching provider configuration.

    Discovery stores the operator endpoint root (``https://host``), while an
    OpenAI-compatible provider commonly configures ``https://host/v1``.  Only
    these deterministic variants are considered; no endpoint is derived from
    a prompt, model response, or request body.
    """
    normalized = _operator_endpoint(endpoint)
    if not normalized:
        return set()
    variants = {normalized}
    try:
        parsed = urlparse(normalized)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            root = parsed._replace(path=path[:-3] or "", query="", fragment="").geturl().rstrip("/")
            if root:
                variants.add(root)
    except (TypeError, ValueError):
        return variants
    return variants


def is_provider_model_eligible(
    store: ModelDiscoveryStore | None,
    endpoint: str,
    model_id: str,
    *,
    configured_local_endpoints: list[str] | None = None,
) -> bool:
    """Enforce S35 lifecycle state at the provider routing boundary.

    If discovery has durable rows for an endpoint, only the exact model in
    ``ACTIVE`` state is eligible.  An explicitly configured local endpoint with
    no rows is also denied until discovery/probing establishes ``ACTIVE``.
    Cloud providers that are not in the operator local-endpoint registry keep
    their existing eligibility behavior.
    """
    variants = _provider_endpoint_variants(endpoint)
    model = model_id.strip() if isinstance(model_id, str) else ""
    # Test/dynamic provider implementations may intentionally expose only a
    # model label and rely on their own transport seam.  They are not local
    # discovery entries; preserve their existing routing behavior and let the
    # concrete egress guard authorize/deny the actual destination.
    if not variants:
        return bool(model)
    if not model:
        return False

    durable_rows: list[dict[str, Any]] = []
    if store is not None:
        for candidate in variants:
            durable_rows.extend(store.get_models_by_endpoint(candidate))
    if durable_rows:
        return any(
            row.get("model_id") == model
            and row.get("state") == ModelLifecycleState.ACTIVE.value
            for row in durable_rows
        )

    configured = {
        candidate
        for raw in (configured_local_endpoints or [])
        for candidate in _provider_endpoint_variants(raw)
    }
    return not variants.intersection(configured)


class LocalEndpointScanner:
    """Scan operator-configured OpenAI-compatible endpoints and reconcile state."""

    def __init__(
        self,
        store: ModelDiscoveryStore,
        endpoints: list[str] | None = None,
        timeout: float = 10.0,
        prober_factory: Callable[..., Any] | None = None,
        cooldown_seconds: float | None = None,
        max_cooldown_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.timeout = _bounded_seconds(timeout, default=10.0, maximum=120.0)
        self.prober_factory = prober_factory
        self.cooldown_seconds = _bounded_seconds(
            cooldown_seconds
            if cooldown_seconds is not None
            else os.environ.get("SCP_MODEL_REPROBE_COOLDOWN_SECONDS"),
            default=DEFAULT_REPROBE_COOLDOWN_SECONDS,
            maximum=MAX_REPROBE_COOLDOWN_SECONDS,
        )
        configured_max = _bounded_seconds(
            max_cooldown_seconds
            if max_cooldown_seconds is not None
            else os.environ.get("SCP_MODEL_REPROBE_MAX_SECONDS"),
            default=MAX_REPROBE_COOLDOWN_SECONDS,
            maximum=MAX_REPROBE_COOLDOWN_SECONDS,
        )
        self.max_cooldown_seconds = max(self.cooldown_seconds, configured_max)
        self._clock = clock or _utc_now

        source = (
            os.environ.get("SCP_LOCAL_ENDPOINTS", "").split(",")
            if endpoints is None
            else endpoints
        )
        self.endpoints = self._normalize_endpoints(source)

    @staticmethod
    def _normalize_endpoints(values: Any) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            endpoint = _operator_endpoint(value)
            if endpoint and endpoint not in normalized:
                normalized.append(endpoint)
        return normalized

    @classmethod
    def endpoints_from_environment(cls) -> list[str]:
        return cls._normalize_endpoints(
            os.environ.get("SCP_LOCAL_ENDPOINTS", "").split(",")
        )

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    @staticmethod
    def _validate_request_url(url: str) -> None:
        # Internal/private resolution is allowed only because this URL derives
        # from the operator-configured endpoint list.  The egress gate remains
        # mandatory and is evaluated immediately before each HTTP operation.
        validate_url(url, allow_internal=True)
        enforce_egress_policy(url)

    async def run_discovery(self) -> dict[str, Any]:
        """Scan configured endpoints and reconcile only successful responses."""
        if not self.endpoints:
            return {"successful_endpoints": 0, "failed_endpoints": 0, "models_seen": 0}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = [self._scan_single(client, endpoint) for endpoint in self.endpoints]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        successful_endpoints = 0
        failed_endpoints = 0
        models_seen = 0
        stale_count = 0
        retired_count = 0
        for endpoint, result in zip(self.endpoints, results, strict=True):
            if isinstance(result, Exception):
                failed_endpoints += 1
                logger.warning("Failed to scan endpoint %s: %s", endpoint, result)
                # An endpoint outage is not a deletion signal, but it must not
                # leave an ACTIVE model routable forever.  Withdraw it in two
                # bounded observations while retaining all durable state.
                for row in self.store.get_models_by_endpoint(endpoint):
                    state = ModelLifecycleState(row["state"])
                    if state == ModelLifecycleState.RETIRED:
                        continue
                    new_state = (
                        ModelLifecycleState.RETIRED
                        if state == ModelLifecycleState.STALE
                        else ModelLifecycleState.STALE
                    )
                    self.store.update_model(
                        endpoint,
                        row["model_id"],
                        state=new_state,
                        next_probe_at=None,
                    )
                    if new_state == ModelLifecycleState.STALE:
                        stale_count += 1
                    else:
                        retired_count += 1
                continue

            successful_endpoints += 1
            seen_ids = set(result)
            models_seen += len(seen_ids)
            for model_id in sorted(seen_ids):
                row = self.store.get_model(endpoint, model_id)
                if row is None:
                    self.store.upsert_model(
                        endpoint,
                        model_id,
                        ModelLifecycleState.DISCOVERED,
                        last_seen_at=self._now().isoformat(),
                    )
                    continue
                state = ModelLifecycleState(row["state"])
                if state in {ModelLifecycleState.STALE, ModelLifecycleState.RETIRED}:
                    # Reappearance is never an automatic activation.  Re-enter
                    # the quarantine path and require a fresh probe.
                    self.store.update_model(
                        endpoint,
                        model_id,
                        state=ModelLifecycleState.DISCOVERED,
                        last_seen_at=self._now().isoformat(),
                        next_probe_at=None,
                        probe_failures=0,
                    )
                else:
                    self.store.update_model(
                        endpoint,
                        model_id,
                        last_seen_at=self._now().isoformat(),
                    )

            for row in self.store.get_models_by_endpoint(endpoint):
                model_id = row["model_id"]
                if model_id in seen_ids:
                    continue
                state = ModelLifecycleState(row["state"])
                if state == ModelLifecycleState.RETIRED:
                    continue
                new_state = (
                    ModelLifecycleState.RETIRED
                    if state == ModelLifecycleState.STALE
                    else ModelLifecycleState.STALE
                )
                self.store.update_model(
                    endpoint,
                    model_id,
                    state=new_state,
                    next_probe_at=None,
                )
                if new_state == ModelLifecycleState.STALE:
                    stale_count += 1
                else:
                    retired_count += 1
                logger.warning(
                    "Model %s disappeared from endpoint %s: %s",
                    model_id,
                    endpoint,
                    new_state.value,
                )

        return {
            "successful_endpoints": successful_endpoints,
            "failed_endpoints": failed_endpoints,
            "models_seen": models_seen,
            "stale": stale_count,
            "retired": retired_count,
        }

    async def _scan_single(self, client: httpx.AsyncClient, endpoint: str) -> list[str]:
        base_url = endpoint.rstrip("/")
        url = f"{base_url}/v1/models"
        # Keep the policy check in this same function scope: the static EE-G1
        # gate must be able to prove that this concrete transport is guarded.
        validate_url(url, allow_internal=True)
        enforce_egress_policy(url)
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "data" not in data:
            raise ValueError("malformed /v1/models response")
        models = data["data"]
        if not isinstance(models, list):
            raise ValueError("malformed /v1/models data field")
        model_ids: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                raise ValueError("malformed /v1/models item")
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("malformed /v1/models item id")
            model_ids.append(model_id.strip())
        return model_ids

    async def run_lifecycle(self) -> dict[str, int]:
        """Advance durable state; only a successful probe can reach ACTIVE."""
        counters = {"probed": 0, "qualified": 0, "activated": 0, "cooldown": 0}
        for model in self.store.list_models():
            endpoint = model["endpoint"]
            model_id = model["model_id"]
            state = ModelLifecycleState(model["state"])

            if state in {ModelLifecycleState.STALE, ModelLifecycleState.RETIRED}:
                continue

            if state == ModelLifecycleState.COOLDOWN:
                if not self._cooldown_due(model):
                    continue
                self.store.update_model(
                    endpoint,
                    model_id,
                    state=ModelLifecycleState.QUARANTINED,
                    next_probe_at=None,
                )
                state = ModelLifecycleState.QUARANTINED

            if state == ModelLifecycleState.DISCOVERED:
                self.store.update_model(
                    endpoint,
                    model_id,
                    state=ModelLifecycleState.QUARANTINED,
                )
                state = ModelLifecycleState.QUARANTINED

            if state == ModelLifecycleState.QUARANTINED:
                counters["probed"] += 1
                passed = await self._probe_model(endpoint, model_id)
                row = self.store.get_model(endpoint, model_id) or model
                failures = int(row.get("probe_failures") or 0)
                probe_time = self._now()
                if passed:
                    counters["qualified"] += 1
                    self.store.update_model(
                        endpoint,
                        model_id,
                        state=ModelLifecycleState.QUALIFIED,
                        last_probe_at=probe_time.isoformat(),
                        next_probe_at=None,
                        probe_failures=0,
                    )
                    state = ModelLifecycleState.QUALIFIED
                else:
                    failures += 1
                    delay = min(
                        self.max_cooldown_seconds,
                        self.cooldown_seconds * (2 ** min(failures - 1, 20)),
                    )
                    next_probe = (probe_time + timedelta(seconds=delay)).isoformat()
                    counters["cooldown"] += 1
                    self.store.update_model(
                        endpoint,
                        model_id,
                        state=ModelLifecycleState.COOLDOWN,
                        last_probe_at=probe_time.isoformat(),
                        next_probe_at=next_probe,
                        probe_failures=failures,
                    )
                    continue

            if state == ModelLifecycleState.QUALIFIED:
                # QUALIFIED is only written after a passing probe above (or a
                # durable prior pass from an earlier process).  A failed probe
                # never follows this branch and therefore cannot activate.
                self.store.update_model(
                    endpoint,
                    model_id,
                    state=ModelLifecycleState.ACTIVE,
                )
                counters["activated"] += 1

        return counters

    def _cooldown_due(self, model: Mapping[str, Any]) -> bool:
        raw = model.get("next_probe_at")
        if not raw:
            # Legacy rows have no durable deadline; one bounded reprobe is safe.
            return True
        try:
            return self._now() >= parse_utc_iso(str(raw))
        except ValueError:
            logger.error(
                "Invalid next_probe_at for %s/%s; keeping model in COOLDOWN",
                model.get("endpoint"),
                model.get("model_id"),
            )
            return False

    async def _probe_model(self, endpoint: str, model_id: str) -> bool:
        """Execute the contract probe against an operator-configured endpoint."""
        base_url = endpoint.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        try:
            self._validate_request_url(url)
            if self.prober_factory is not None:
                try:
                    prober = self.prober_factory(
                        endpoint_url=url, model=model_id, timeout=self.timeout
                    )
                except TypeError:
                    # Compatibility with the original two-argument seam.
                    prober = self.prober_factory(url, model_id)
            else:
                prober = ContractProber(
                    endpoint_url=url, model=model_id, timeout=self.timeout
                )
            return bool(await prober.probe_async())
        except Exception as exc:  # fail-closed: probe failure is not ACTIVE
            logger.warning(
                "Probe execution failed for %s at %s: %s. Fail-closed to False.",
                model_id,
                endpoint,
                exc,
            )
            return False

    def close(self) -> None:
        self.store.close()


_PROFILE_LEVELS = {"core": 0, "standard": 1, "full": 2}
_KILL_SWITCH_VALUES = {"0", "false", "off", "disabled"}


def model_lifecycle_kill_switch_off(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    # The S35-specific switch wins; the existing global discovery switch is a
    # second operator-controlled brake and does not alter S23's implementation.
    raw = env.get(
        "SCP_MODEL_DISCOVERY_SCHEDULER",
        env.get("SCP_DISCOVERY_SCHEDULER", "on"),
    )
    return str(raw).strip().lower() in _KILL_SWITCH_VALUES


def model_lifecycle_profile_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    current = str(env.get("SCP_API_PROFILE", "")).strip().lower()
    if not current:
        production = str(env.get("SCP_PRODUCTION_MODE", "")).strip().lower()
        current = "core" if production in {"1", "true", "yes", "on"} else "full"
    minimum = str(
        env.get("SCP_MODEL_DISCOVERY_MIN_PROFILE", "standard")
    ).strip().lower()
    if current not in _PROFILE_LEVELS or minimum not in _PROFILE_LEVELS:
        return False
    return _PROFILE_LEVELS[current] >= _PROFILE_LEVELS[minimum]


class ModelLifecycleScheduler:
    """Production async tick loop for discovery followed by lifecycle advance."""

    def __init__(
        self,
        scanner: LocalEndpointScanner,
        interval_seconds: float | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        close_store: bool = False,
    ) -> None:
        self.scanner = scanner
        self._interval = _bounded_seconds(
            interval_seconds
            if interval_seconds is not None
            else os.environ.get("SCP_MODEL_DISCOVERY_INTERVAL_SECONDS"),
            default=DEFAULT_MODEL_DISCOVERY_INTERVAL_SECONDS,
            maximum=MAX_MODEL_DISCOVERY_INTERVAL_SECONDS,
        )
        self._sleep = sleep
        self._close_store = close_store
        self._store_closed = False
        self._task: asyncio.Task | None = None
        self.last_tick_result: dict[str, Any] = {}

    async def tick(self) -> dict[str, Any]:
        """Run one discovery/lifecycle tick without letting a source kill loop."""
        result: dict[str, Any] = {}
        try:
            result["discovery"] = await self.scanner.run_discovery()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # non-fatal background guard
            logger.warning("[S35-LIFECYCLE] discovery tick failed: %s", exc)
            result["discovery"] = {"error": type(exc).__name__}
        try:
            result["lifecycle"] = await self.scanner.run_lifecycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # non-fatal background guard
            logger.warning("[S35-LIFECYCLE] lifecycle tick failed: %s", exc)
            result["lifecycle"] = {"error": type(exc).__name__}
        result["tick_at"] = now_utc_iso()
        self.last_tick_result = result
        return result

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # loop-level guard
                logger.warning("[S35-LIFECYCLE] tick crashed (non-fatal): %s", exc)
            await self._sleep(self._interval)

    def start(self) -> asyncio.Task | None:
        """Start only with operator endpoints, enabled profile, and no kill switch."""
        if not self.scanner.endpoints:
            logger.info("[S35-LIFECYCLE] not started: no operator-configured endpoints")
            return None
        if model_lifecycle_kill_switch_off():
            logger.info("[S35-LIFECYCLE] disabled by discovery kill switch")
            return None
        if not model_lifecycle_profile_enabled():
            logger.info("[S35-LIFECYCLE] disabled by SCP_API_PROFILE/profile policy")
            return None
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(
            self.run_forever(), name="scp-model-lifecycle-scheduler"
        )
        logger.info(
            "[S35-LIFECYCLE] scheduler started (interval=%.1fs, first tick immediate)",
            self._interval,
        )
        return self._task

    async def stop(self, timeout: float = 5.0) -> None:
        """Cancel/await the task and optionally close the owned durable store."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.CancelledError:
                logger.debug("[S35-LIFECYCLE] scheduler cancellation acknowledged")
            except asyncio.TimeoutError:
                logger.warning("[S35-LIFECYCLE] stop timed out after %.1fs", timeout)
            except Exception as exc:
                logger.warning("[S35-LIFECYCLE] stop failed: %s", exc)
        if self._close_store and not self._store_closed:
            self.scanner.close()
            self._store_closed = True

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def running_task(self) -> asyncio.Task | None:
        return self._task


def create_model_lifecycle_scheduler(
    *, data_dir: str | Path | None = None
) -> ModelLifecycleScheduler | None:
    """Build the production scheduler only when endpoints are operator-configured."""
    endpoints = LocalEndpointScanner.endpoints_from_environment()
    if not endpoints:
        return None
    if model_lifecycle_kill_switch_off() or not model_lifecycle_profile_enabled():
        return None
    root = Path(data_dir or os.environ.get("SCP_DATA_DIR", "data"))
    store = ModelDiscoveryStore(root / "model_discovery.sqlite3")
    scanner = LocalEndpointScanner(store=store, endpoints=endpoints)
    return ModelLifecycleScheduler(scanner, close_store=True)
