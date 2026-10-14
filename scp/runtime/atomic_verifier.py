"""SAFE-Style Atomic Claim Decomposition & Local Deterministic Verifier (Waves W15-W18).

Implements:
1. AtomicClaimSplitter (W15): Splits multi-sentence / multi-clause LLM draft responses
   into self-contained atomic claims with basic coreference entity propagation
   (inspired by Google DeepMind SAFE, arXiv:2403.18802).
2. LocalDeterministicVerifier (W16): Verifies each atomic claim in <10ms with zero
   external LLM calls by routing across:
   - Arithmetic evaluator (`scp.runtime.safe_math.safe_eval_arithmetic`)
   - Unit conversion engine (`scp.data_sources.conversion.ConversionDataSource`)
   - Scientific & Geographic local knowledge (`scp.runtime.domain_data_fork`)
   - Evidence-grounded numeric/entity/negation matcher (`ClaimExtractor`, `ClaimVerifier`, `FactSeparator`)
3. Short-Circuit & Selective Pruning (W17-W18):
   - Repairs deterministic calculation/conversion errors in-place.
   - Short-circuits `RealityJudge` multi-LLM cross_verify when all factual claims are
     deterministically verified locally (`provenance="atomic_deterministic_local"`).
   - Selectively prunes or labels unverified residual clauses while preserving
     verified atomic claims when partial verification succeeds.
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

from scp.knowledge.claim_extractor import Claim, ClaimExtractor, ClaimVerifier
from scp.knowledge.domain_knowledge import FactSeparator
from scp.runtime.safe_math import safe_eval_arithmetic

logger = logging.getLogger("scp.runtime.atomic_verifier")

# Clause boundary splitting: sentence punctuation or explicit coordinating conjunctions
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_CLAUSE_CONJUNCTION_RE = re.compile(
    r"\s*(?:,\s*(?:và|đồng thời|ngoài ra|trong khi đó|and|while|whereas|furthermore|moreover)\s+"
    r"|;\s*)\s*",
    re.IGNORECASE,
)

# Pronouns / anaphora that refer back to the primary subject entity of the preceding clause
_ANAPHORA_PREFIX_RE = re.compile(
    r"^(?:nó|họ|thành phố này|quốc gia này|nước này|hành tinh này|nguyên tố này|hợp chất này"
    r"|it|this city|this country|this planet|this element|this compound)\b",
    re.IGNORECASE,
)

# Simple arithmetic equation detector: e.g. "2 + 2 = 4", "15 * 3 bằng 45", "100 / 4 is 25"
_ARITHMETIC_EQ_RE = re.compile(
    r"(-?\d+(?:[.,]\d+)?(?:\s*[\+\-\*\/\^]\s*-?\d+(?:[.,]\d+)?)+)\s*"
    r"(?:=|bằng|là|equals?|is)\s*(-?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)


@dataclass
class AtomicClaimResult:
    """Verification outcome for a single atomic claim clause."""

    claim_id: str
    clause_text: str
    resolved_text: str
    category: str  # arithmetic | conversion | domain_fact | evidence_grounded | conversational | open_assertion
    status: str  # VERIFIED | CONTRADICTED | UNVERIFIED | CONVERSATIONAL
    confidence: float = 0.0
    source: str = ""
    evidence_detail: str = ""
    repaired_clause: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "clause_text": self.clause_text,
            "resolved_text": self.resolved_text,
            "category": self.category,
            "status": self.status,
            "verified": True if self.status == "VERIFIED" else (False if self.status == "CONTRADICTED" else None),
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "verification_detail": self.evidence_detail,
            "repaired_clause": self.repaired_clause,
        }


@dataclass
class AtomicVerificationSummary:
    """Aggregated outcome across all atomic claims in a draft response."""

    total_claims: int = 0
    verified_count: int = 0
    contradicted_count: int = 0
    unverified_count: int = 0
    conversational_count: int = 0
    all_verified_locally: bool = False
    partially_verified_locally: bool = False
    repaired_answer: str | None = None
    pruned_answer: str = ""
    unverified_residual_text: str = ""
    claims: list[AtomicClaimResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_claims": self.total_claims,
            "verified_count": self.verified_count,
            "refuted_count": self.contradicted_count,
            "unverified_count": self.unverified_count,
            "conversational_count": self.conversational_count,
            "all_verified_locally": self.all_verified_locally,
            "partially_verified_locally": self.partially_verified_locally,
            "repaired_answer": self.repaired_answer,
            "pruned_answer": self.pruned_answer,
            "unverified_residual_text": self.unverified_residual_text,
            "claims_payload": [c.to_dict() for c in self.claims],
        }


def is_atomic_verifier_enabled() -> bool:
    """Feature flag SCP_ATOMIC_VERIFIER (default enabled='1')."""
    raw = os.environ.get("SCP_ATOMIC_VERIFIER", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


class AtomicClaimSplitter:
    """[W15] Decomposes a draft response into self-contained atomic clauses."""

    _SUBJECT_ENTITY_RE = re.compile(
        r"^([A-ZÀ-Ỹ][\wÀ-ỹ]+(?:\s+[A-ZÀ-Ỹ][\wÀ-ỹ]+)*)\s+(?:là|có|thuộc|nằm|cách|is|has|was|lies|boils)",
    )

    def split(self, answer: str, question: str = "") -> list[tuple[str, str]]:
        """Return list of (original_clause, coreference_resolved_clause)."""
        text = str(answer or "").strip()
        if not text or text.startswith("[SCP:") or text.startswith("[unverified"):
            return []

        raw_sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        clauses: list[str] = []
        for sent in raw_sentences:
            sub_clauses = [c.strip() for c in _CLAUSE_CONJUNCTION_RE.split(sent) if c.strip()]
            if len(sub_clauses) > 1 and all(len(c.split()) >= 3 for c in sub_clauses):
                clauses.extend(sub_clauses)
            else:
                clauses.append(sent)

        # Extract fallback primary subject from question if available
        active_entity = ""
        q_ent_match = re.search(
            r"(?:của|về|tại|ở|of|in|for|about)\s+([A-ZÀ-Ỹ][\wÀ-ỹ]+(?:\s+[A-ZÀ-Ỹ][\wÀ-ỹ]+)*)",
            str(question or ""),
        )
        if q_ent_match:
            active_entity = q_ent_match.group(1).strip()

        results: list[tuple[str, str]] = []
        for clause in clauses:
            subj_match = self._SUBJECT_ENTITY_RE.search(clause)
            if subj_match:
                active_entity = subj_match.group(1).strip()
                results.append((clause, clause))
            elif active_entity and _ANAPHORA_PREFIX_RE.search(clause):
                resolved = _ANAPHORA_PREFIX_RE.sub(active_entity, clause, count=1)
                results.append((clause, resolved))
            else:
                results.append((clause, clause))
        return results


class AtomicDeterministicVerifier:
    """[W16-W18] Verifies atomic claims locally and performs deterministic repair & pruning."""

    def __init__(self) -> None:
        self.splitter = AtomicClaimSplitter()
        self.extractor = ClaimExtractor()
        self.claim_verifier = ClaimVerifier()
        self.fact_separator = FactSeparator(claim_extractor=self.extractor)
        self._conversion_source: Any = None

    @property
    def conversion_source(self) -> Any:
        if self._conversion_source is None:
            try:
                from scp.data_sources.conversion import ConversionDataSource
                self._conversion_source = ConversionDataSource()
            except Exception as exc:
                logger.debug("ConversionDataSource unavailable: %s", exc, exc_info=True)
        return self._conversion_source

    def verify_and_prune(
        self,
        question: str,
        draft_answer: str,
        clean_evidence: list[str] | None = None,
        retrieval_res: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Decompose draft_answer into atomic claims, verify locally, repair/prune."""
        clean_evidence = list(clean_evidence or [])
        retrieval_res = retrieval_res or {}
        clauses = self.splitter.split(draft_answer, question)
        if not clauses:
            return AtomicVerificationSummary(pruned_answer=draft_answer).to_dict()

        results: list[AtomicClaimResult] = []
        for idx, (orig_clause, resolved_clause) in enumerate(clauses, start=1):
            claim_id = f"atomic_{idx:03d}"
            res = self._verify_single_clause(
                claim_id=claim_id,
                orig_clause=orig_clause,
                resolved_clause=resolved_clause,
                question=question,
                clean_evidence=clean_evidence,
                retrieval_res=retrieval_res,
            )
            results.append(res)

        factual_claims = [r for r in results if r.category != "conversational"]
        verified = [r for r in factual_claims if r.status == "VERIFIED"]
        contradicted = [r for r in factual_claims if r.status == "CONTRADICTED"]
        unverified = [r for r in factual_claims if r.status == "UNVERIFIED"]
        conversational = [r for r in results if r.category == "conversational"]

        # Build repaired answer if any contradicted claim has a deterministic repair
        any_repaired = any(r.repaired_clause is not None for r in results)
        repaired_clauses: list[str] = []
        pruned_clauses: list[str] = []
        residual_clauses: list[str] = []

        for r in results:
            if r.repaired_clause is not None:
                repaired_clauses.append(r.repaired_clause)
                pruned_clauses.append(r.repaired_clause)
            elif r.status in ("VERIFIED", "CONVERSATIONAL"):
                repaired_clauses.append(r.clause_text)
                pruned_clauses.append(r.clause_text)
            elif r.status == "UNVERIFIED":
                repaired_clauses.append(r.clause_text)
                residual_clauses.append(r.resolved_text)
            # CONTRADICTED without repair is omitted from pruned_clauses

        # Count repaired contradictions as verified after deterministic repair
        effective_contradicted = [r for r in contradicted if r.repaired_clause is None]
        effective_verified_count = len(verified) + sum(
            1 for r in contradicted if r.repaired_clause is not None
        )
        total_factual = len(factual_claims)

        deterministic_categories = {"arithmetic", "conversion", "domain_fact"}
        deterministic_verified_count = sum(
            1 for r in factual_claims
            if r.category in deterministic_categories
            and (r.status == "VERIFIED" or r.repaired_clause is not None)
        )

        all_verified_locally = (
            total_factual > 0
            and deterministic_verified_count == total_factual
            and len(effective_contradicted) == 0
        )
        partially_verified_locally = (
            total_factual > 0
            and deterministic_verified_count > 0
            and not all_verified_locally
            and len(effective_contradicted) == 0
        )

        def _join_clauses(items: list[str]) -> str:
            out: list[str] = []
            for item in items:
                s = item.strip()
                if not s:
                    continue
                if s[-1] not in ".!?":
                    s += "."
                out.append(s)
            return " ".join(out)

        summary = AtomicVerificationSummary(
            total_claims=total_factual,
            verified_count=effective_verified_count,
            contradicted_count=len(effective_contradicted),
            unverified_count=len(unverified),
            conversational_count=len(conversational),
            all_verified_locally=all_verified_locally,
            partially_verified_locally=partially_verified_locally,
            repaired_answer=_join_clauses(repaired_clauses) if any_repaired else None,
            pruned_answer=_join_clauses(pruned_clauses) if pruned_clauses else draft_answer,
            unverified_residual_text=_join_clauses(residual_clauses),
            claims=results,
        )
        return summary.to_dict()

    def _verify_single_clause(
        self,
        *,
        claim_id: str,
        orig_clause: str,
        resolved_clause: str,
        question: str,
        clean_evidence: list[str],
        retrieval_res: dict[str, Any],
    ) -> AtomicClaimResult:
        # 1. Check if purely conversational / polite greeting without factual assertion
        try:
            from scp.runtime.judge_abstain import is_honest_abstain_answer
            if is_honest_abstain_answer(resolved_clause):
                return AtomicClaimResult(
                    claim_id=claim_id,
                    clause_text=orig_clause,
                    resolved_text=resolved_clause,
                    category="conversational",
                    status="CONVERSATIONAL",
                    confidence=1.0,
                    source="local:judge_abstain",
                    evidence_detail="conversational or honest abstain clause",
                )
        except Exception:
            logger.debug("judge_abstain check skipped", exc_info=True)

        # 2. Check deterministic arithmetic equation (e.g. "2 + 2 = 4")
        arith_match = _ARITHMETIC_EQ_RE.search(resolved_clause)
        if arith_match:
            expr_str = arith_match.group(1).replace(",", ".")
            claimed_str = arith_match.group(2).replace(",", ".")
            try:
                expected_val = safe_eval_arithmetic(expr_str)
                claimed_val = float(claimed_str)
                if math.isclose(expected_val, claimed_val, rel_tol=1e-4, abs_tol=1e-4):
                    return AtomicClaimResult(
                        claim_id=claim_id,
                        clause_text=orig_clause,
                        resolved_text=resolved_clause,
                        category="arithmetic",
                        status="VERIFIED",
                        confidence=1.0,
                        source="local:scp/runtime/safe_math.py",
                        evidence_detail=f"{expr_str.strip()} == {expected_val:g}",
                    )
                fmt_val = int(expected_val) if expected_val.is_integer() else round(expected_val, 4)
                repaired = (
                    orig_clause[: arith_match.start(2)]
                    + str(fmt_val)
                    + orig_clause[arith_match.end(2) :]
                )
                return AtomicClaimResult(
                    claim_id=claim_id,
                    clause_text=orig_clause,
                    resolved_text=resolved_clause,
                    category="arithmetic",
                    status="CONTRADICTED",
                    confidence=1.0,
                    source="local:scp/runtime/safe_math.py",
                    evidence_detail=f"arithmetic mismatch: claimed {claimed_val}, expected {expected_val:g}",
                    repaired_clause=repaired,
                )
            except Exception:
                logger.debug("Arithmetic eval skipped for clause: %s", resolved_clause, exc_info=True)

        # 3. Check deterministic unit conversion via ConversionDataSource
        conv = self.conversion_source
        if conv is not None:
            try:
                conv_res = conv.answer_from_question(resolved_clause)
                if not conv_res:
                    eq_conv_m = re.search(
                        r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z°µ]+)\s*(?:=|bằng|is|equals)\s*(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z°µ]+)",
                        resolved_clause,
                    )
                    if eq_conv_m:
                        v1, u1, _v2, u2 = eq_conv_m.groups()
                        conv_res = conv.answer_from_question(f"convert {v1} {u1} to {u2}")
                if not conv_res and question:
                    q_conv = conv.answer_from_question(question)
                    if q_conv and q_conv.get("value") is not None:
                        to_u = str(q_conv.get("to_unit") or "").lower()
                        from_u = str(q_conv.get("from_unit") or "").lower()
                        clause_low = resolved_clause.lower()
                        if (to_u and to_u in clause_low) or (from_u and from_u in clause_low):
                            conv_res = q_conv
                if conv_res and conv_res.get("value") is not None:
                    expected_num = float(conv_res["value"])
                    to_unit = str(conv_res.get("to_unit") or "").lower()
                    numbers = [
                        float(m.replace(",", "."))
                        for m in re.findall(r"-?\d+(?:[.,]\d+)?", resolved_clause)
                    ]
                    if numbers:
                        # The converted target value is typically the last number in the clause
                        claimed_target = numbers[-1]
                        if math.isclose(expected_num, claimed_target, rel_tol=1e-2, abs_tol=1e-2):
                            return AtomicClaimResult(
                                claim_id=claim_id,
                                clause_text=orig_clause,
                                resolved_text=resolved_clause,
                                category="conversion",
                                status="VERIFIED",
                                confidence=1.0,
                                source="local:scp/data_sources/conversion.py",
                                evidence_detail=str(conv_res.get("text") or f"={expected_num} {to_unit}"),
                            )
                        if len(numbers) >= 2:
                            return AtomicClaimResult(
                                claim_id=claim_id,
                                clause_text=orig_clause,
                                resolved_text=resolved_clause,
                                category="conversion",
                                status="CONTRADICTED",
                                confidence=1.0,
                                source="local:scp/data_sources/conversion.py",
                                evidence_detail=f"conversion mismatch: expected {conv_res.get('text')}",
                                repaired_clause=str(conv_res.get("text")),
                            )
            except Exception:
                logger.debug("Conversion check skipped", exc_info=True)

        # 4. Check DomainDataFork (Astronomy, Chemistry, Physics, Geography, Math constants)
        try:
            from scp.runtime.domain_data_fork import resolve_domain_data_lookup
            fork_hit = resolve_domain_data_lookup(resolved_clause)
            if not fork_hit and question:
                q_hit = resolve_domain_data_lookup(question)
                if q_hit and q_hit.get("text"):
                    q_text_low = str(q_hit["text"]).lower()
                    clause_low = resolved_clause.lower()
                    q_tokens = [
                        w for w in re.findall(r"[a-zA-Z0-9_\u00C0-\u024F\u1EA0-\u1EF9]+", q_text_low)
                        if len(w) >= 3 and w not in ("sự", "kiện", "năm", "tại", "của", "cho", "trong", "khoảng", "theo", "được", "nghĩa", "gồm", "với")
                    ]
                    if any(tok in clause_low for tok in q_tokens):
                        fork_hit = q_hit

            if fork_hit and fork_hit.get("text"):
                fork_text = str(fork_hit["text"])
                fork_text_low = fork_text.lower()
                clause_low = resolved_clause.lower()

                # Strip chemical formulas (H2O, CO2, H2SO4, etc.) before comparing standalone numeric values
                _chem_strip = re.sub(r"\b[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)*\b", "", resolved_clause)
                _fork_chem_strip = re.sub(r"\b[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)*\b", "", fork_text)
                clause_nums = [float(n.replace(",", ".")) for n in re.findall(r"-?\d+(?:[.,]\d+)?", _chem_strip)]
                fork_nums = [float(n.replace(",", ".")) for n in re.findall(r"-?\d+(?:[.,]\d+)?", _fork_chem_strip)]

                if clause_nums and fork_nums:
                    has_canonical_match = any(
                        any(math.isclose(cn, fn, rel_tol=1e-2, abs_tol=1e-2) for fn in fork_nums)
                        for cn in clause_nums
                    )
                    if has_canonical_match:
                        return AtomicClaimResult(
                            claim_id=claim_id,
                            clause_text=orig_clause,
                            resolved_text=resolved_clause,
                            category="domain_fact",
                            status="VERIFIED",
                            confidence=0.98,
                            source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                            evidence_detail=fork_text,
                        )
                    # For contradiction, ensure clause specifically pertains to this entity
                    entity_overlap = any(
                        tok in clause_low
                        for tok in re.findall(r"[a-zA-Z0-9_\u00C0-\u024F\u1EA0-\u1EF9]+", fork_text_low)
                        if len(tok) >= 3 and tok not in ("sự", "kiện", "năm", "tại", "của", "cho", "trong", "khoảng", "theo", "được", "nghĩa", "gồm", "với")
                    )
                    if entity_overlap:
                        return AtomicClaimResult(
                            claim_id=claim_id,
                            clause_text=orig_clause,
                            resolved_text=resolved_clause,
                            category="domain_fact",
                            status="CONTRADICTED",
                            confidence=0.98,
                            source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                            evidence_detail=f"domain numeric mismatch against canonical: {fork_text}",
                            repaired_clause=fork_text,
                        )

                # Copula predicate check (e.g. "Thủ đô của X là Y")
                copula_m = re.search(r"([^.,;!?]+?)\b(?:là|is)\s+([^.,;!?]+)", resolved_clause, re.IGNORECASE)
                fork_copula_m = re.search(r"([^.,;!?]+?)\b(?:là|is)\s+([^.,;!?]+)", fork_text, re.IGNORECASE)
                if copula_m and fork_copula_m:
                    claimed_subj = copula_m.group(1).strip().lower()
                    claimed_pred = copula_m.group(2).strip().lower()
                    expected_subj = fork_copula_m.group(1).strip().lower()
                    expected_pred = fork_copula_m.group(2).strip().lower()

                    subj_tokens = [w for w in re.findall(r"[\w\u00C0-\u024F\u1EA0-\u1EF9]+", expected_subj) if len(w) >= 3]
                    subj_matches = any(w in claimed_subj for w in subj_tokens) or (claimed_subj in expected_subj or expected_subj in claimed_subj)

                    if subj_matches:
                        if claimed_pred in expected_pred or expected_pred in claimed_pred:
                            return AtomicClaimResult(
                                claim_id=claim_id,
                                clause_text=orig_clause,
                                resolved_text=resolved_clause,
                                category="domain_fact",
                                status="VERIFIED",
                                confidence=0.98,
                                source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                                evidence_detail=fork_text,
                            )
                        return AtomicClaimResult(
                            claim_id=claim_id,
                            clause_text=orig_clause,
                            resolved_text=resolved_clause,
                            category="domain_fact",
                            status="CONTRADICTED",
                            confidence=0.98,
                            source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                            evidence_detail=f"domain entity mismatch against canonical: {fork_text}",
                            repaired_clause=fork_text,
                        )

                extracted = self.extractor.extract(resolved_clause, question)
                # Wire ClaimVerifier against fork ground truth
                fork_gt: dict[str, Any] = {}
                if isinstance(fork_hit.get("data"), dict):
                    fork_gt.update(fork_hit["data"])
                for fn_idx, f_claim in enumerate(self.extractor.extract(fork_text)):
                    if f_claim.value is not None:
                        fork_gt[f"fork_val_{fn_idx}"] = f_claim.value
                    if f_claim.entity and f_claim.target:
                        fork_gt[f"fork_ent_{fn_idx}"] = f"{f_claim.entity} {f_claim.target}"

                if extracted:
                    fork_verified_claims = self.claim_verifier.verify(extracted, fork_gt)
                    if any(c.verified is False for c in fork_verified_claims):
                        c_bad = next(c for c in fork_verified_claims if c.verified is False)
                        return AtomicClaimResult(
                            claim_id=claim_id,
                            clause_text=orig_clause,
                            resolved_text=resolved_clause,
                            category="domain_fact",
                            status="CONTRADICTED",
                            confidence=0.98,
                            source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                            evidence_detail=f"contradicts domain data: {c_bad.verification_detail}",
                        )
                    if any(c.verified is True for c in fork_verified_claims):
                        return AtomicClaimResult(
                            claim_id=claim_id,
                            clause_text=orig_clause,
                            resolved_text=resolved_clause,
                            category="domain_fact",
                            status="VERIFIED",
                            confidence=0.98,
                            source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                            evidence_detail=fork_text,
                        )

                if extracted and any(self.fact_separator._is_match(c, fork_text) for c in extracted):
                    return AtomicClaimResult(
                        claim_id=claim_id,
                        clause_text=orig_clause,
                        resolved_text=resolved_clause,
                        category="domain_fact",
                        status="VERIFIED",
                        confidence=0.98,
                        source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                        evidence_detail=fork_text,
                    )
                # Direct token/entity match via FactSeparator._is_match
                synthetic = Claim(claim_id=claim_id, claim_type="entity", text=resolved_clause)
                if self.fact_separator._is_match(synthetic, fork_text):
                    return AtomicClaimResult(
                        claim_id=claim_id,
                        clause_text=orig_clause,
                        resolved_text=resolved_clause,
                        category="domain_fact",
                        status="VERIFIED",
                        confidence=0.98,
                        source=str(fork_hit.get("api_url") or "local:domain_data_fork"),
                        evidence_detail=fork_text,
                    )
        except Exception:
            logger.debug("DomainDataFork check skipped", exc_info=True)

        # 5. Check against clean_evidence (RAG contexts, pre-gen web evidence, KB hits)
        all_evidence_snippets: list[str] = list(clean_evidence)
        for kb_hit in retrieval_res.get("kb_hits", []) or []:
            if isinstance(kb_hit, dict) and kb_hit.get("answer"):
                all_evidence_snippets.append(str(kb_hit["answer"]))
        for web_hit in retrieval_res.get("web_search_hits", []) or []:
            if isinstance(web_hit, dict) and web_hit.get("evidence_snippet"):
                all_evidence_snippets.append(str(web_hit["evidence_snippet"]))

        if all_evidence_snippets:
            extracted_claims = self.extractor.extract(resolved_clause, question)
            if not extracted_claims:
                extracted_claims = [Claim(claim_id=claim_id, claim_type="entity", text=resolved_clause)]

            # 5a. Ground-truth evaluation via ClaimVerifier for numeric/date/entity claims
            gt_dict: dict[str, Any] = {}
            for s_idx, snip in enumerate(all_evidence_snippets):
                for sc_idx, sc in enumerate(self.extractor.extract(snip)):
                    if sc.value is not None:
                        gt_dict[f"snip_val_{s_idx}_{sc_idx}"] = sc.value
                    if sc.entity and sc.target:
                        gt_dict[f"snip_ent_{s_idx}_{sc_idx}"] = f"{sc.entity} {sc.target}"

            if isinstance(retrieval_res.get("ground_truth"), dict):
                gt_dict.update(retrieval_res["ground_truth"])

            verified_by_cv = self.claim_verifier.verify(extracted_claims, gt_dict)
            contradicted_cv = [c for c in verified_by_cv if c.verified is False]
            if contradicted_cv:
                c_first = contradicted_cv[0]
                return AtomicClaimResult(
                    claim_id=claim_id,
                    clause_text=orig_clause,
                    resolved_text=resolved_clause,
                    category="evidence_grounded",
                    status="CONTRADICTED",
                    confidence=0.95,
                    source="evidence:claim_verifier",
                    evidence_detail=str(c_first.verification_detail or "contradicts evidence ground truth"),
                )

            verified_cv = [c for c in verified_by_cv if c.verified is True]
            if verified_cv:
                details = "; ".join(c.verification_detail for c in verified_cv if c.verification_detail)
                return AtomicClaimResult(
                    claim_id=claim_id,
                    clause_text=orig_clause,
                    resolved_text=resolved_clause,
                    category="evidence_grounded",
                    status="VERIFIED",
                    confidence=0.96,
                    source="evidence:claim_verifier",
                    evidence_detail=details or all_evidence_snippets[0][:200],
                )

            # 5b. Fallback to FactSeparator token/entity matching
            for snippet in all_evidence_snippets:
                if any(self.fact_separator._is_match(c, snippet) for c in extracted_claims):
                    return AtomicClaimResult(
                        claim_id=claim_id,
                        clause_text=orig_clause,
                        resolved_text=resolved_clause,
                        category="evidence_grounded",
                        status="VERIFIED",
                        confidence=0.94,
                        source="evidence:clean_context_or_kb",
                        evidence_detail=snippet[:200],
                    )

        return AtomicClaimResult(
            claim_id=claim_id,
            clause_text=orig_clause,
            resolved_text=resolved_clause,
            category="open_assertion",
            status="UNVERIFIED",
            confidence=0.0,
            source="none",
            evidence_detail="requires external semantic judge or additional evidence",
        )


__all__ = [
    "AtomicClaimResult",
    "AtomicVerificationSummary",
    "AtomicClaimSplitter",
    "AtomicDeterministicVerifier",
    "is_atomic_verifier_enabled",
]
