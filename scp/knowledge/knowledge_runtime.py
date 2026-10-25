"""S06 Knowledge System runtime (M8 / 26-P1) — lifecycle + temporal layers.

[ARC-01 split 2026-10-25] Trước đây file này chứa toàn bộ runtime (1319 LOC >
800 ceiling ARC-01). Tách làm 2 module, import surface KHÔNG ĐỔI:

- scp/knowledge/knowledge_runtime_core.py — KnowledgeStore (SQLite authority)
  + RetrievalIndex (FTS5 accelerator) + errors/helpers/constants chung.
- File này — GoldLifecycle (promotion ladder evidence-gated, CE-S06-01/02)
  + TemporalRevalidation (volatility review schedule, CE-S06-02), và re-export
  tên public của core để caller import từ module này như trước.

Wired to spec/scp_future_cause_effect_matrix.yaml S06 edges. Fail-closed
everywhere: every gate failure raises KnowledgeTransitionRejected (REJECTED).
PASS on the tests only means "no failure observed in the stated scope".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Sequence

from scp.epistemic.evidence_store import EvidenceIntegrityError
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    LifecycleResult as LifecycleResult,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    RetrievalIndex as RetrievalIndex,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    RetrievalIndexMissing as RetrievalIndexMissing,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    SearchHit as SearchHit,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    USABLE_FOR_DECISIONS as USABLE_FOR_DECISIONS,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeNotFound as KnowledgeNotFound,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeObject as KnowledgeObject,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeRuntimeError as KnowledgeRuntimeError,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeStatus as KnowledgeStatus,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeStore as KnowledgeStore,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    KnowledgeTransitionRejected as KnowledgeTransitionRejected,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    _PROMOTION_LADDER as _PROMOTION_LADDER,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    _REVIEWABLE_STATUSES as _REVIEWABLE_STATUSES,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    _as_utc_datetime as _as_utc_datetime,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    _iso as _iso,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    parse_status_value as parse_status_value,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    parse_utc_iso as parse_utc_iso,
)
from scp.knowledge.knowledge_runtime_core import (  # noqa: F401 — compat re-export
    validate_transition as validate_transition,
)

# 3. GOLD LIFECYCLE — evidence-gated promotion ladder
# ============================================================================


class GoldLifecycle:
    """Promotion ladder authority: RAW→CURATED→CORROBORATED→VERIFIED→GOLD.

    Per-step gates (spec/knowledge_promotion.yaml profile factual_general):
      RAW→CURATED           provenance present (evidence_refs) + scope defined
      CURATED→CORROBORATED  >= min_independent_lineages independent lineages
                            + no open material contradiction
      CORROBORATED→VERIFIED direct verification evidence + validity window
      VERIFIED→GOLD         repeated success + no open contradiction
                            + review_after schedule assigned
    Demotion: a material contradiction sends GOLD/VERIFIED to UNDER_REVIEW;
    UNDER_REVIEW can then be DEMOTED. Old GOLD past review_after retires.
    """

    # Evidence kinds that can serve as Reality/verification evidence for the
    # VERIFIED rung (Issue #37: an arbitrary string ref is never authority, and
    # raw provenance like HTTP_RESPONSE is not a verification episode either).
    VERIFICATION_CAPABLE_KINDS = frozenset(
        {"TEST_RESULT", "RUNTIME_OBSERVATION", "HUMAN_ATTESTATION"}
    )

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        evidence_store: Any | None = None,
        lineage_store: Any | None = None,
        min_independent_lineages: int = 2,
        min_success_observations: int = 2,
        gold_review_after_seconds: int = 90 * 86400,
        actor: str = "gold_lifecycle",
    ) -> None:
        self.store = store
        # Canonical authorities (Issue #37): promotion decisions are DERIVED
        # from these runtime stores, never from caller assertions. Without a
        # wired EvidenceStore no ref can be verified, so every promotion is
        # fail-closed rejected (missing authority => HOLD, never promotion).
        self.evidence_store = evidence_store
        self.lineage_store = lineage_store
        self.min_independent_lineages = int(min_independent_lineages)
        self.min_success_observations = int(min_success_observations)
        self.gold_review_after_seconds = int(gold_review_after_seconds)
        self.actor = actor

    # -- authority derivation (Issue #37) --------------------------------------
    def _resolve_evidence_ref(self, ref: str) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve one ref against the canonical EvidenceStore.

        Returns (record, None) when the ref exists AND passes integrity,
        or (None, missing_piece) when it cannot be trusted. A ref that merely
        looks like an evidence id is never authority.
        """
        if self.evidence_store is None:
            return None, (
                f"evidence_authority: no EvidenceStore wired; ref {str(ref)!r} unverifiable"
            )
        try:
            record = self.evidence_store.get(str(ref))
        except KeyError:
            return None, f"evidence_integrity: ref {str(ref)!r} does not exist in EvidenceStore"
        except EvidenceIntegrityError as exc:
            return None, f"evidence_integrity: ref {str(ref)!r} failed integrity ({exc})"
        return record, None

    def _derive_authority(
        self,
        knowledge_id: str,
        *,
        evidence_refs: Sequence[str] | None,
        verification_evidence_refs: Sequence[str] | None,
    ) -> dict[str, Any]:
        """Derive promotion authority from RUNTIME STATE, not caller claims.

        Caller-supplied ``independent_lineages`` / ``success_observations`` are
        advisory at most; the returned dict is the only thing ``_gate`` trusts:
          - every supplied ref must exist and pass EvidenceStore integrity;
          - independent support comes from LineageStore.assess_independent_support
            over the ACTUAL source ids of those evidence records (UNKNOWN => 0);
          - durable verified episodes are the distinct verification-capable
            evidence records recorded by VERIFIED promotions plus refs supplied
            at this call — a scalar caller count can never grant them.
        """
        requested = [str(r) for r in (evidence_refs or []) if str(r).strip()]
        ver_requested = [str(r) for r in (verification_evidence_refs or []) if str(r).strip()]

        resolved: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []
        for ref in requested + ver_requested:
            if ref in resolved:
                continue
            record, missing = self._resolve_evidence_ref(ref)
            if record is None:
                unresolved.append(missing or f"unverifiable ref {ref!r}")
            else:
                resolved[ref] = record

        verification_records = [
            resolved[r]
            for r in dict.fromkeys(ver_requested)
            if r in resolved
            and resolved[r].get("kind") in self.VERIFICATION_CAPABLE_KINDS
        ]
        non_verification_ver_refs = [
            r
            for r in dict.fromkeys(ver_requested)
            if r in resolved and resolved[r].get("kind") not in self.VERIFICATION_CAPABLE_KINDS
        ]

        source_ids = sorted(
            {
                str(rec.get("source_id"))
                for rec in resolved.values()
                if rec.get("source_id")
            }
        )

        derived_lineages: int | None = None
        if self.lineage_store is not None:
            derived_lineages = int(
                self.lineage_store.assess_independent_support(source_ids)[
                    "known_independent_lineages"
                ]
            )

        # Durable verified episodes: distinct verification-capable evidence
        # records that were recorded by VERIFIED promotions of THIS knowledge
        # object, plus verification refs supplied at this call. Each must still
        # resolve and pass integrity (purged/tampered evidence stops counting).
        history_verified_refs: set[str] = set()
        for event in self.store.history(knowledge_id):
            if event.get("decision") != "PROMOTE" or event.get("to_status") != (
                KnowledgeStatus.VERIFIED.value
            ):
                continue
            for ref in event.get("evidence_refs") or []:
                history_verified_refs.add(str(ref))
        durable: set[str] = set()
        for ref in sorted(history_verified_refs | set(ver_requested)):
            record, missing = self._resolve_evidence_ref(ref)
            if record is not None and record.get("kind") in self.VERIFICATION_CAPABLE_KINDS:
                durable.add(ref)
            elif missing:
                unresolved.append(missing)

        return {
            "resolved": resolved,
            "unresolved": unresolved,
            "verification_records": verification_records,
            "non_verification_ver_refs": non_verification_ver_refs,
            "source_ids": source_ids,
            "derived_independent_lineages": derived_lineages,
            "durable_verified_episodes": len(durable),
        }

    # -- promotion -------------------------------------------------------------
    def promote(
        self,
        knowledge_id: str,
        *,
        target: str | KnowledgeStatus | None = None,
        evidence_refs: Sequence[str] | None = None,
        verification_evidence_refs: Sequence[str] | None = None,
        independent_lineages: int | None = None,
        success_observations: int | None = None,
        actor: str | None = None,
        now: str | datetime | None = None,
    ) -> LifecycleResult:
        """Promote ONE rung of the ladder. Rejects (fail-closed) any jump.

        `target` is only needed for re-entry from UNDER_REVIEW; by default the
        next ladder rung is used, so RAW→GOLD direct is structurally impossible.
        """
        obj = self.store.get(knowledge_id)
        current = parse_status_value(obj.status)
        if target is None:
            if current not in _PROMOTION_LADDER or current == KnowledgeStatus.GOLD.value:
                raise KnowledgeTransitionRejected(
                    f"no default promotion rung from status {current}; pass target explicitly",
                    reason_codes=["NO_PROMOTION_RUNG"],
                )
            idx = _PROMOTION_LADDER.index(current)
            target_status = _PROMOTION_LADDER[idx + 1]
        else:
            target_status = parse_status_value(target)

        # Issue #37: resolve EVERYTHING the decision rests on — the caller's
        # refs at this rung AND the provenance already attached to the object
        # (re-checked so tampered/purged provenance fails closed later too).
        caller_refs = [str(r) for r in (evidence_refs or []) if str(r).strip()]
        authority = self._derive_authority(
            knowledge_id,
            evidence_refs=[*obj.evidence_refs, *caller_refs],
            verification_evidence_refs=verification_evidence_refs,
        )
        gate = self._gate(
            obj,
            target_status,
            authority=authority,
            now=now,
        )
        if gate is not None:
            message, reason_codes, missing = gate
            raise KnowledgeTransitionRejected(
                f"promotion {current} -> {target_status} REJECTED: {message} "
                f"(the ladder must be walked rung by rung)",
                reason_codes=reason_codes,
                missing_pieces=missing,
            )

        # Apply step-specific evidence/lineage updates before the transition.
        # Lineage counts come from the DERIVED authority (LineageStore), never
        # from the caller's scalar (Issue #37: caller assertions are advisory).
        for ref in caller_refs:
            if ref not in obj.evidence_refs:
                obj.evidence_refs.append(ref)
        derived_lineages = authority["derived_independent_lineages"]
        if derived_lineages is not None:
            obj.independent_lineages = max(obj.independent_lineages, int(derived_lineages))
        now_dt = _as_utc_datetime(now)

        if target_status == KnowledgeStatus.CURATED.value:
            obj.validity["provenance_refs"] = sorted(set(obj.evidence_refs))
        elif target_status == KnowledgeStatus.VERIFIED.value:
            ver_refs = [str(r) for r in (verification_evidence_refs or []) if str(r).strip()]
            for ref in ver_refs:
                if ref not in obj.evidence_refs:
                    obj.evidence_refs.append(ref)
            obj.validity["last_validated_at"] = _iso(now_dt)
        elif target_status == KnowledgeStatus.GOLD.value:
            ver_refs = [str(r) for r in (verification_evidence_refs or []) if str(r).strip()]
            for ref in ver_refs:
                if ref not in obj.evidence_refs:
                    obj.evidence_refs.append(ref)
            obj.validity["repeated_success_observations"] = int(
                authority["durable_verified_episodes"]
            )
            # GOLD always carries a review deadline (stale gold retires).
            seconds = self._gold_review_after_seconds(obj)
            obj.validity["review_after"] = _iso(now_dt + timedelta(seconds=seconds))
            obj.validity["gold_review_after_seconds"] = seconds

        result = self.store.transition(
            obj,
            target_status,
            decision="PROMOTE",
            reason_codes=[f"GATE_PASSED_{current}_TO_{target_status}"],
            evidence_refs=obj.evidence_refs,
            actor=actor or self.actor,
        )
        return result

    def _gate(
        self,
        obj: KnowledgeObject,
        target_status: str,
        *,
        authority: dict[str, Any],
        now: str | datetime | None,
    ) -> tuple[str, list[str], list[str]] | None:
        """Return (message, reason_codes, missing_pieces) when the gate REJECTS,
        or None when the step may proceed.

        Issue #37: every gate decision is derived from ``authority`` — runtime
        state resolved through the canonical EvidenceStore/LineageStore. Caller
        assertions (independent_lineages / success_observations scalars, raw
        evidence-id strings) can never satisfy a rung by themselves.
        """
        current = parse_status_value(obj.status)

        # Ontology is the first authority: any transition it forbids is dead.
        try:
            validate_transition(current, target_status)
        except ValueError as exc:
            return (str(exc), ["INVALID_TRANSITION"], [])

        missing: list[str] = list(authority["unresolved"])
        reasons: list[str] = ["GATE_REQUIREMENTS_NOT_MET"]

        if target_status == KnowledgeStatus.CURATED.value:
            if not authority["resolved"]:
                missing.append(
                    "provenance_present: at least one verifiable evidence_ref "
                    "required for RAW->CURATED"
                )
            if not obj.scope:
                missing.append("scope_defined: scope required before curation")

        elif target_status == KnowledgeStatus.CORROBORATED.value:
            if len(authority["resolved"]) < 2:
                missing.append(
                    "verified_evidence_count >= 2 across CURATED+CORROBORATED "
                    f"(resolved {len(authority['resolved'])})"
                )
            # DERIVED independent support only: the caller's scalar is ignored.
            derived = authority["derived_independent_lineages"]
            if derived is None:
                missing.append(
                    "independent_support_authority: no LineageStore wired; "
                    "independent support cannot be derived (HOLD, never promote)"
                )
            elif derived < self.min_independent_lineages:
                missing.append(
                    f"derived_independent_lineages >= {self.min_independent_lineages} "
                    f"(got {derived} from sources {authority['source_ids']})"
                )
            if self.store.open_contradiction_count(obj.knowledge_id) > 0:
                missing.append("no OPEN material contradiction may exist")

        elif target_status == KnowledgeStatus.VERIFIED.value:
            if not authority["verification_records"]:
                missing.append(
                    "direct verification evidence required: at least one "
                    f"verification_evidence_ref that exists, passes integrity and has kind "
                    f"in {sorted(self.VERIFICATION_CAPABLE_KINDS)}"
                )
            for ref in authority["non_verification_ver_refs"]:
                missing.append(
                    f"verification_kind: ref {ref!r} resolved but its evidence kind is not "
                    "verification-capable"
                )

        elif target_status == KnowledgeStatus.GOLD.value:
            episodes = int(authority["durable_verified_episodes"])
            if episodes < self.min_success_observations:
                missing.append(
                    f"durable_verified_episodes >= {self.min_success_observations} (got {episodes}); "
                    "a caller-supplied success_observations scalar cannot grant GOLD"
                )
            if self.store.open_contradiction_count(obj.knowledge_id) > 0:
                missing.append("no OPEN material contradiction may exist")
            if not obj.evidence_refs:
                missing.append("GOLD requires evidence_refs")

        if missing:
            return ("requirements not met", reasons, missing)
        return None

    def _gold_review_after_seconds(self, obj: KnowledgeObject) -> int:
        volatility = (obj.validity or {}).get("volatility")
        if volatility:
            try:
                cls = VolatilityClass(parse_volatility_value(volatility))
            except KnowledgeRuntimeError:
                cls = None
            if cls is not None:
                return DEFAULT_REVIEW_AFTER_SECONDS[cls]
        return self.gold_review_after_seconds

    # -- contradiction-driven demotion ------------------------------------------
    def report_contradiction(
        self,
        knowledge_id: str,
        *,
        evidence_ref: str,
        description: str,
        actor: str | None = None,
        now: str | datetime | None = None,
    ) -> tuple[str, LifecycleResult | None]:
        """Record a material contradiction. GOLD/VERIFIED are immediately sent
        to UNDER_REVIEW (CE-S06-02); history is preserved, never overwritten."""
        obj = self.store.get(knowledge_id)
        contradiction_id = self.store.record_contradiction(
            knowledge_id,
            evidence_ref=str(evidence_ref),
            description=description,
            now=now,
        )

        transition: LifecycleResult | None = None
        if parse_status_value(obj.status) in (
            KnowledgeStatus.GOLD.value,
            KnowledgeStatus.VERIFIED.value,
        ):
            transition = self.store.transition(
                obj,
                KnowledgeStatus.UNDER_REVIEW,
                decision="UNDER_REVIEW",
                reason_codes=["MATERIAL_CONTRADICTION", f"CONTRADICTION_{contradiction_id}"],
                evidence_refs=[str(evidence_ref)],
                actor=actor or self.actor,
            )
        return contradiction_id, transition

    def demote(
        self,
        knowledge_id: str,
        *,
        reason: str,
        actor: str | None = None,
    ) -> LifecycleResult:
        """UNDER_REVIEW -> DEMOTED (the downward path after review)."""
        obj = self.store.get(knowledge_id)
        if not reason or not str(reason).strip():
            raise KnowledgeTransitionRejected(
                "demotion requires an explicit reason",
                reason_codes=["DEMOTE_REASON_REQUIRED"],
            )
        return self.store.transition(
            obj,
            KnowledgeStatus.DEMOTED,
            decision="DEMOTE",
            reason_codes=["REVIEW_OUTCOME_DEMOTED", str(reason)],
            actor=actor or self.actor,
        )

    # -- retirement ---------------------------------------------------------------
    def retire(self, knowledge_id: str, *, reason: str, actor: str | None = None) -> LifecycleResult:
        obj = self.store.get(knowledge_id)
        if not reason or not str(reason).strip():
            raise KnowledgeTransitionRejected(
                "retirement requires an explicit reason",
                reason_codes=["RETIRE_REASON_REQUIRED"],
            )
        return self.store.transition(
            obj,
            KnowledgeStatus.RETIRED,
            decision="RETIRE",
            reason_codes=["OBSOLETE_KNOWLEDGE", str(reason)],
            actor=actor or self.actor,
        )

    def retire_expired_gold(self, now: str | datetime | None = None) -> list[LifecycleResult]:
        """RETIRE gold knowledge whose review_after deadline has passed."""
        now_dt = _as_utc_datetime(now)
        out: list[LifecycleResult] = []
        for obj in self.store.list_all(status=KnowledgeStatus.GOLD):
            review_after = (obj.validity or {}).get("review_after")
            if not review_after:
                continue
            if parse_utc_iso(str(review_after)) <= now_dt:
                out.append(
                    self.retire(
                        obj.knowledge_id,
                        reason=f"gold_review_window_expired at {review_after}",
                    )
                )
        return out


# ============================================================================
# 4. TEMPORAL REVALIDATION — volatility-class review schedule
# ============================================================================


class VolatilityClass(str, Enum):
    """How fast the world invalidates a piece of knowledge (mission classes)."""

    STABLE = "STABLE"
    SLOW = "SLOW"
    MEDIUM = "MEDIUM"
    FAST = "FAST"
    VERY_FAST = "VERY_FAST"


DEFAULT_REVIEW_AFTER_SECONDS: dict[VolatilityClass, int] = {
    VolatilityClass.STABLE: 90 * 86400,
    VolatilityClass.SLOW: 30 * 86400,
    VolatilityClass.MEDIUM: 7 * 86400,
    VolatilityClass.FAST: 86400,
    VolatilityClass.VERY_FAST: 6 * 3600,
}


def parse_volatility_value(value: object) -> str:
    return value.value if isinstance(value, VolatilityClass) else str(value).strip().upper()


@dataclass
class RevalidationOutcome:
    knowledge_id: str
    from_status: str
    to_status: str
    reason_code: str
    review_after: str
    event_id: str = ""


class TemporalRevalidation:
    """Schedules and enforces revalidation per volatility class.

    Expired knowledge is flagged UNDER_REVIEW (never silently kept VERIFIED)
    and stale knowledge is excluded from decision-grade retrieval.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        review_after_seconds: dict[VolatilityClass | str, int] | None = None,
        actor: str = "temporal_revalidation",
    ) -> None:
        self.store = store
        self.actor = actor
        self.review_after_seconds: dict[VolatilityClass, int] = dict(DEFAULT_REVIEW_AFTER_SECONDS)
        if review_after_seconds:
            for key, seconds in review_after_seconds.items():
                cls = VolatilityClass(parse_volatility_value(key))
                if int(seconds) <= 0:
                    raise KnowledgeRuntimeError(
                        f"review_after_seconds for {cls.value} must be positive"
                    )
                self.review_after_seconds[cls] = int(seconds)

    # -- scheduling ---------------------------------------------------------------
    def assign(
        self,
        knowledge_id: str,
        volatility_class: VolatilityClass | str,
        *,
        now: str | datetime | None = None,
    ) -> LifecycleResult | None:
        """Attach (or refresh) a volatility-based review schedule to knowledge."""
        cls = VolatilityClass(parse_volatility_value(volatility_class))
        now_dt = _as_utc_datetime(now)
        seconds = self.review_after_seconds[cls]
        review_after = now_dt + timedelta(seconds=seconds)

        def _mutate(obj: KnowledgeObject) -> None:
            obj.validity["volatility"] = cls.value
            obj.validity["review_after_seconds"] = seconds
            obj.validity["last_validated_at"] = _iso(now_dt)
            obj.validity["review_after"] = _iso(review_after)

        obj = self.store.update(knowledge_id, _mutate)
        # Only GOLD status changes flow through lifecycle events here; a
        # schedule assignment alone does not change status, so no event.
        return LifecycleResult(
            knowledge_id=knowledge_id,
            action="SCHEDULE_REVALIDATION",
            from_status=parse_status_value(obj.status),
            to_status=parse_status_value(obj.status),
            reason_codes=[f"VOLATILITY_{cls.value}", f"review_after={_iso(review_after)}"],
            timestamp=_iso(now_dt),
        )

    def review_after_of(self, knowledge_id: str) -> str | None:
        return (self.store.get(knowledge_id).validity or {}).get("review_after")

    def is_stale(self, knowledge_id: str, *, now: str | datetime | None = None) -> bool:
        review_after = self.review_after_of(knowledge_id)
        if not review_after:
            return False
        return parse_utc_iso(str(review_after)) <= _as_utc_datetime(now)

    def expired(self, now: str | datetime | None = None) -> list[KnowledgeObject]:
        """Active knowledge whose review deadline has passed (flag candidates)."""
        now_dt = _as_utc_datetime(now)
        out: list[KnowledgeObject] = []
        for obj in self.store.list_all():
            if parse_status_value(obj.status) not in _REVIEWABLE_STATUSES:
                continue
            review_after = (obj.validity or {}).get("review_after")
            if review_after and parse_utc_iso(str(review_after)) <= now_dt:
                out.append(obj)
        return out

    def sweep(self, now: str | datetime | None = None) -> list[RevalidationOutcome]:
        """Flag every expired active knowledge item for re-check: -> UNDER_REVIEW."""
        outcomes: list[RevalidationOutcome] = []
        for obj in self.expired(now=now):
            review_after = str((obj.validity or {}).get("review_after"))
            result = self.store.transition(
                obj,
                KnowledgeStatus.UNDER_REVIEW,
                decision="UNDER_REVIEW",
                reason_codes=["REVIEW_AFTER_EXPIRED", f"review_after={review_after}"],
                actor=self.actor,
            )
            outcomes.append(
                RevalidationOutcome(
                    knowledge_id=obj.knowledge_id,
                    from_status=result.from_status,
                    to_status=result.to_status,
                    reason_code="REVIEW_AFTER_EXPIRED",
                    review_after=review_after,
                    event_id=result.event_id,
                )
            )
        return outcomes

    # -- decision-safety ------------------------------------------------------------
    def usable_for_decisions(self, obj: KnowledgeObject, *, now: str | datetime | None = None) -> bool:
        """Shared decision-safety rule (same as KnowledgeStore.usable_for_decisions)."""
        return self.store.usable_for_decisions(obj, now=now)
