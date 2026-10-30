"""
SCP - Redesigned TaskJudge
Replaces the bloated RealityJudge and Multi-SLM engine.

[2026-08-29 Cổng D] Two-tier verification:
  Tier 1 (deterministic, ~1ms): tier1_guard — cấu trúc sai bị chém NGAY,
    không tốn một token LLM. LLM không bao giờ là Single Point of Truth
    cho các lỗi máy đọc được.
  Tier 2 (semantic): chỉ câu hỏi ngữ nghĩa mới xuống LLM, và LLM trả về
    TRI-STATE — None (không quyết định được / hai model bất đồng) →
    UNKNOWN + ESCALATE cho người, thay vì KILL oan (fail-closed đúng nghĩa).
"""
import asyncio
import json
import logging
import re
import threading
import time
from typing import Any

from scp.runtime.judge_abstain import (
    _ABSTAIN_ASSERTION_RE, _ABSTAIN_CLAIM_CUE_RE, _ABSTAIN_CLASS_MAP,
    _ABSTAIN_CONV_ASSERTION_RE, _ABSTAIN_CONV_FRAGMENT_PATTERNS,
    _ABSTAIN_CONV_FRAGMENT_RE, _ABSTAIN_CONV_MARKERS, _ABSTAIN_CONV_WHITELIST_RE,
    _ABSTAIN_DIGIT_RE, _ABSTAIN_FREE_NP_MAX_WORDS, _ABSTAIN_REFUSAL_MARKERS,
    _ABSTAIN_SEG_ACT_VERBS, _ABSTAIN_SEG_ANA_NP, _ABSTAIN_SEG_CONNECTORS,
    _ABSTAIN_SEG_DESCRIPTORS, _ABSTAIN_SEG_POLITENESS, _ABSTAIN_SEG_PREPS,
    _ABSTAIN_SEG_PRONOUNS, _ABSTAIN_SEG_TIME, _ABSTAIN_SEG_TOPICS,
    _ABSTAIN_TOPIC_CHAIN_MAX, _ABSTAIN_TOPIC_MODIFIER_MAX, _ABSTAIN_URL_RE,
    _TIME_SIGNAL_RE, _abstain_entries_at, _abstain_starts_with_act,
    _refusal_segment_is_safe, is_honest_abstain_answer,
    is_refusal_abstain_answer, question_has_time_signal,
)
from scp.runtime.judge_helpers import (
    JudgeThreatMixin,
    ROGUE_AI_LENSES,
    _extract_session_texts,
    _run_crosscheck_sync,
    analyze_session_rogue,
    evaluate_rogue_session,
)
from scp.runtime.judge_llm import _llm_judge
from scp.runtime.judge_llm import judge_unavailable_reason as _judge_unavailable_reason
from scp.security.tier1_guard import check as tier1_check
from scp.verifier import IndependentVerifier

logger = logging.getLogger("scp.judge")

# [W3-e1] Tín hiệu security THẬT ở mức judge: Tier-1 chặn nội dung cố tình
# mang marker nội bộ (tamper/anti-tamper signal). Các failure Tier-1 khác
# (REJECT_EMPTY, REJECT_GROUNDING, ...) và semantic-FAIL trên câu benign là
# VERIFICATION-FAIL — governance phải là ESCALATE (abstain trung thực), không
# phải KILL (mức dành cho nội dung nguy hiểm thật; đường security-wide vẫn
# được _ask_impl._is_true_security_threat / security lane enforcing độc lập).
_SECURITY_TIER1_TAGS = frozenset({"REJECT_INTERNAL_MARKER"})

_KB_STOPWORDS = frozenset({
    "của", "và", "là", "các", "những", "một", "trong", "cho", "với", "về",
    "được", "có", "này", "đó", "thì", "mà", "ở", "tại", "từ", "ra", "vào",
    "gì", "nào", "sao", "thế", "đâu", "ai", "khi", "bao", "nhiêu", "con",
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "to", "for",
    "with", "on", "at", "by", "from", "about", "what", "which", "who",
    "whom", "whose", "where", "when", "why", "how", "and", "or", "it",
})

_FACTUAL_QUERY_INDICATORS = (
    "thủ đô", "tổng thống", "thủ tướng", "chủ tịch", "chính phủ",
    "dân số", "diện tích", "thời tiết", "nhiệt độ", "chứng khoán",
    "cổ phiếu", "tỷ giá", "lãi suất", "gdp", "lạm phát", "chiến tranh",
    "lịch sử", "công thức", "định lý", "bằng mấy", "mấy độ",
    "capital", "president", "prime minister", "population", "weather",
    "temperature", "stock", "inflation", "how much", "how many",
    "when was", "where is", "sinh năm", "mất năm", "thành lập",
    "quốc gia", "đất nước", "thành phố", "tỉnh nào", "nước nào",
)


def _has_factual_query(question: str) -> bool:
    q = str(question or "").strip().lower()
    if not q:
        return False
    if any(ind in q for ind in _FACTUAL_QUERY_INDICATORS):
        return True
    if re.search(r"\d+\s*[\+\-\*\/\^]\s*\d+", q):
        return True
    if re.search(r"\bai\s+là\s+(?!bạn\b|scp\b|mày\b|em\b|tôi\b|mình\b)", q):
        return True
    if re.search(r"\bwho\s+is\s+(?!you\b|scp\b|this\b|me\b)", q):
        return True
    if "ở đâu" in q and not any(k in q for k in ("bạn đang ở đâu", "bạn ở đâu", "mày ở đâu", "scp ở đâu", "tôi ở đâu")):
        return True
    if "bao nhiêu" in q and not any(k in q for k in ("tuổi", "bạn bao nhiêu", "mấy tuổi")):
        return True
    if "khi nào" in q and not any(k in q for k in ("bạn sinh", "tạo ra", "ra đời")):
        return True
    m = re.search(r"(\w+)\s+là\s+gì\b", q)
    if m and m.group(1) not in ("bạn", "scp", "mày", "em", "tôi", "mình", "đây", "này", "đó"):
        return True
    return False


def _resolve_lane(question: str, kwargs: dict[str, Any]) -> str:
    lane = kwargs.get("lane") or (kwargs.get("v98_context") or {}).get("lane")
    l0 = None
    if not lane:
        try:
            from scp.runtime.question_router import classify_l0
            l0 = classify_l0(question)
            lane = str(l0.lane) if l0 and getattr(l0, "lane", None) else "LANE_FACTUAL"
        except Exception:
            lane = "LANE_FACTUAL"
    lane_str = str(lane)
    if lane_str != "LANE_SECURITY":
        if l0 is None:
            try:
                from scp.runtime.question_router import classify_l0
                l0 = classify_l0(question)
            except Exception:
                l0 = None
        if l0 and getattr(l0, "lane", None) == "LANE_SECURITY":
            return "LANE_SECURITY"
    if lane_str in ("LANE_CHATBOT", "LANE_CONVERSATIONAL") and _has_factual_query(question):
        return "LANE_FACTUAL"
    return lane_str


def _kb_record_supports_query_and_answer(
    question: str, ai_answer: str, kb_refs: list[dict[str, Any]]
) -> bool:
    if not kb_refs or not question or not ai_answer:
        return False
    q_tokens = {w for w in re.findall(r"\w+", question.lower()) if len(w) > 1 and w not in _KB_STOPWORDS}
    if not q_tokens:
        return False
    ans_tokens = {w for w in re.findall(r"\w+", ai_answer.lower()) if len(w) > 1 and w not in _KB_STOPWORDS}
    for r in kb_refs:
        kb_q = f"{r.get('question', '')} {r.get('entity', '')}".lower()
        kb_q_tokens = {w for w in re.findall(r"\w+", kb_q) if len(w) > 1 and w not in _KB_STOPWORDS}
        common_q = q_tokens & kb_q_tokens
        if not common_q:
            continue
        if len(common_q) < 2 and (len(common_q) / len(q_tokens) < 0.5):
            continue
        kb_ans = f"{r.get('answer', '')} {r.get('fact', '')}".lower()
        kb_ans_tokens = {w for w in re.findall(r"\w+", kb_ans) if len(w) > 1 and w not in _KB_STOPWORDS}
        if not kb_ans_tokens:
            continue
        common_ans = kb_ans_tokens & ans_tokens
        if not common_ans:
            continue
        key_fact_tokens = kb_ans_tokens - q_tokens
        if key_fact_tokens and not (key_fact_tokens & ans_tokens):
            continue
        return True
    return False


class RealityJudge:
    """
    Unified Task Judge. Delegates strictly to IndependentVerifier + Tier-1
    deterministic guard + tri-state LLM semantic cascade.

    [ARC-02 fix 2026-10-25] KHÔNG còn kế thừa JudgeThreatMixin (contract
    test_arc02_zero_mixin_inheritance_across_codebase: zero class trong scp/
    được phép kế thừa class có tên *Mixin*). Threat/intel/v100 operations
    chuyển sang COMPOSITION: instance ``JudgeThreatMixin`` riêng làm
    collaborator, RealityJudge expose method ủy quyền — API bên ngoài
    (admin_v98/v100, lifespan, startup_optimizer) giữ nguyên.
    """

    def __init__(self, *args, **kwargs):
        self.verifier = IndependentVerifier()
        self.judged_count = 0
        self.fail_count = 0
        # [B-S1] Số lần judge consult KB thành công (search không raise, kể cả 0 hit).
        self.knowledge_consult_count = 0
        # [ARC-02] Composition thay inheritance — collaborator giữ lazy-init
        # state (simulator/detector/synthesizer/crawler) trên chính nó.
        self._threat_ops = JudgeThreatMixin()

    _THREAT_OP_ATTRS = ("_intel_crawler", "_threat_simulator", "_antibody_synthesizer", "unified_detector", "data_dir")

    def _threat_ops_synced(self) -> "JudgeThreatMixin":
        ops = self._threat_ops
        for attr in self._THREAT_OP_ATTRS:
            val = getattr(self, attr, None)
            if val is not None:
                setattr(ops, attr, val)
        return ops

    async def run_threat_simulation(self, count: int = 50, *args, **kwargs) -> dict[str, Any]:
        return await self._threat_ops_synced().run_threat_simulation(count, *args, **kwargs)

    async def run_threat_intel_crawl(self, *args, **kwargs) -> list[dict[str, Any]]:
        return await self._threat_ops_synced().run_threat_intel_crawl(*args, **kwargs)

    def get_v100_status(self) -> dict[str, Any]:
        return self._threat_ops_synced().get_v100_status()

    async def run_scheduled_crawl(self, *args, **kwargs) -> list[dict[str, Any]]:
        return await self._threat_ops_synced().run_scheduled_crawl(*args, **kwargs)

    async def schedule_v100_background_jobs(self) -> None:
        await self._threat_ops_synced().schedule_v100_background_jobs()

    async def schedule_background_jobs(self) -> None:
        await self._threat_ops_synced().schedule_background_jobs()

    @property
    def domain_experts(self):
        # [knowledge-experts G-1] Khóa = id của domain_registry (đúng thứ classify_top1
        # trả về), lấy từ bảng bind tường minh — không còn quét os.listdir với
        # "cái nạp sau cùng thắng" (history→Holiday, geography→City, biology→AnimalFacts).
        if not hasattr(self, "_experts"):
            from scp.runtime.expert_registry import build_domain_experts
            self._experts, self._expert_binding_refusals = build_domain_experts()
        return self._experts

    def _lazy_subsystem(self, attr: str, mod_name: str, cls_name: str):
        if not hasattr(self, attr) or getattr(self, attr) is None:
            try:
                import importlib
                mod = importlib.import_module(mod_name)
                setattr(self, attr, getattr(mod, cls_name)())
            except Exception as exc:
                logger.warning("%s unavailable (%s: %s)", cls_name, type(exc).__name__, exc, exc_info=True)
                setattr(self, attr, None)
        return getattr(self, attr)

    @property
    def falsification(self):
        """Lazy FalsificationEngine instance. Fail-closed on error."""
        return self._lazy_subsystem("_falsification", "scp.meta.falsification_engine", "FalsificationEngine")

    @property
    def error_store(self):
        """Lazy ErrorStore instance. Fail-closed on error."""
        return self._lazy_subsystem("_error_store", "scp.brain.error_store", "ErrorStore")

    @property
    def governance(self):
        """Lazy Governance instance. Fail-closed on error."""
        return self._lazy_subsystem("_governance", "scp.meta.governance_v97", "Governance")

    @property
    def counter_response(self):
        """Lazy CounterResponseEngine instance. Fail-closed on error."""
        return self._lazy_subsystem("_counter_response", "scp.security.counter_response", "CounterResponseEngine")

    @property
    def canary_monitor(self):
        """Lazy CanaryTokenMonitor instance. Fail-closed on error."""
        return self._lazy_subsystem("_canary_monitor", "scp.security.canary_monitor", "CanaryTokenMonitor")

    @property
    def attack_memory(self):
        """Lazy AttackPatternMemory instance. Fail-closed on error."""
        return self._lazy_subsystem("_attack_memory", "scp.security.attack_memory", "AttackPatternMemory")

    def get_v98_status(self) -> dict[str, Any]:
        """Return operational status of V98 modules."""
        return {
            "counter_response": self.counter_response is not None,
            "canary_monitor": self.canary_monitor is not None,
            "error_store": self.error_store is not None,
            "attack_memory": self.attack_memory is not None,
            "falsification": self.falsification is not None,
            "governance": self.governance is not None,
        }

    @property
    def domain_knowledge_store(self):
        """[B-S1] Lazy DomainKnowledgeStore."""
        if not hasattr(self, "_kb_store"):
            try:
                from scp.knowledge.domain_store import DomainKnowledgeStore
                self._kb_store = DomainKnowledgeStore()
            except Exception as exc:
                logger.warning(
                    "[B-S1] DomainKnowledgeStore unavailable (%s: %s) — judge continues without KB",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                self._kb_store = None
        return self._kb_store

    def _consult_knowledge(self, question: str, limit: int = 3) -> list[dict[str, Any]]:
        """[B-S1] Consult DomainKnowledgeStore — kết quả CHỈ là evidence bổ trợ."""
        store = self.domain_knowledge_store
        if store is None:
            return []
        try:
            records = store.search(question, limit=limit)
        except Exception as exc:
            logger.warning(
                "[B-S1] KB consult failed (%s: %s) — verdict proceeds without KB",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return []
        self.knowledge_consult_count += 1
        return [
            {
                "question": r.question,
                "answer": r.answer,
                "domain": r.domain,
                "source": r.source,
                "tier": int(r.source_tier),
                "confidence": float(r.confidence),
            }
            for r in records
        ]

    def _inject_kb_refs(self, context: str, kb_refs: list[dict[str, Any]]) -> str:
        """[B-S1] Ghép KB refs vào context như evidence bổ trợ cho cascade."""
        if not kb_refs:
            return context
        lines = "".join(
            f"\n- ({r.get('domain', 'kb')}/{r.get('source', 'local')} tier={r.get('tier', 1)} conf={float(r.get('confidence', 0.8)):.2f}) "
            f"Q: {r.get('question', r.get('entity', ''))} A: {r.get('answer', r.get('fact', ''))}"
            for r in kb_refs
        )
        return context + "\n[KNOWLEDGE BASE REF]" + lines

    @property
    def h8_redteam(self): return None

    def judge(self, question: str, ai_answer: str = "", cycle_count: int = 0, context: str = "", **kwargs) -> dict[str, Any]:
        """Synchronous judge interface."""
        is_structurally_pass = bool(ai_answer.strip())

        is_pass = False
        escalated = False
        failures = []
        answer_is_abstain = is_honest_abstain_answer(ai_answer)
        consensus_missing = False
        single_provider_degraded = False

        tier1 = tier1_check(question, ai_answer, context)
        slm_responses_list = []
        kb_refs: list[dict[str, Any]] = []
        if not tier1.passed:
            failures.extend(tier1.failures)
        elif is_structurally_pass and ai_answer:
            try:
                from scp.data_sources.domain_classifier import classify_top1
                domain = classify_top1(question)
                expert = self.domain_experts.get(domain)
                if expert:
                    resp = expert.predict(question)
                    if getattr(resp, "answer", None):
                        context += f"\n[SYSTEM EXPERT DATA] For {domain}: {resp.answer}"
                        slm_responses_list.append(resp.__dict__)
            except Exception as e:
                # silent-by-design: failure is already logged via getLogger('scp.judge').debug in the handler body
                import logging as _logging
                _logging.getLogger("scp.judge").debug("Expert injection failed: %s", e, exc_info=True)

            kb_refs = self._consult_knowledge(question)
            context = self._inject_kb_refs(context, kb_refs)

            import os as _os
            if _os.environ.get("SCP_MULTI_LLM_CROSSCHECK", "1") == "1":
                try:
                    cross = _run_crosscheck_sync(question, ai_answer, context)
                    semantic = cross["final"]
                    if cross["consensus"] == "disagree":
                        failures.append("multi_llm_disagreement")
                    consensus_missing = (
                        str(cross.get("consensus", "")) == "missing_distinct_providers"
                    )
                    if semantic is None and consensus_missing and cross.get("primary", {}).get("verdict") == "PASS":
                        lane = _resolve_lane(question, kwargs)
                        if lane != "LANE_SECURITY" and (
                            lane in ("LANE_CHATBOT", "LANE_CONVERSATIONAL")
                            or _kb_record_supports_query_and_answer(question, ai_answer, kb_refs)
                        ):
                            semantic = "PASS"
                            single_provider_degraded = True
                except Exception as _cc_err:
                    logger.warning(
                        "[SEC-R2-01] multi-LLM crosscheck failed (%s: %s) — marking DEGRADED/UNCERTAIN fail-closed",
                        type(_cc_err).__name__,
                        _cc_err,
                        exc_info=True,
                    )
                    failures.append("crosscheck_fallback_degraded")
                    semantic = _llm_judge(question, ai_answer, context)
            else:
                lane = _resolve_lane(question, kwargs)
                if lane == "LANE_SECURITY":
                    failures.append("security_crosscheck_required")
                    semantic = "FAIL"
                    single_provider_degraded = False
                else:
                    semantic = _llm_judge(question, ai_answer, context)
                    single_provider_degraded = True

            is_degraded = ("crosscheck_fallback_degraded" in failures) or single_provider_degraded
            if semantic is None:
                escalated = True
            elif semantic == "PASS" or semantic is True:
                if "crosscheck_fallback_degraded" in failures:
                    is_pass = False
                else:
                    is_pass = True
            else:
                failures.append("semantic_judge_fail")

        self.judged_count += 1
        if not is_pass and not escalated:
            self.fail_count += 1

        if escalated:
            return self._format_escalated(
                ai_answer=ai_answer,
                failures=failures,
                slm_responses_list=slm_responses_list,
                kb_refs=kb_refs,
                consensus_missing=consensus_missing,
                answer_is_abstain=answer_is_abstain,
                cycle_count=cycle_count,
                is_structurally_pass=is_structurally_pass,
            )

        return self._format_result(
            is_pass=is_pass,
            is_structurally_pass=is_structurally_pass,
            single_provider_degraded=single_provider_degraded,
            consensus_missing=consensus_missing,
            answer_is_abstain=answer_is_abstain,
            failures=failures,
            kb_refs=kb_refs,
            ai_answer=ai_answer,
            slm_responses_list=slm_responses_list,
            cycle_count=cycle_count,
        )

    async def judge_async(self, question: str, ai_answer: str = "", cycle_count: int = 0, context: str = "", **kwargs) -> dict[str, Any]:
        """Asynchronous judge interface."""
        from scp.runtime.judge_llm import _llm_judge_async

        if not ai_answer:
            is_structurally_pass = False
        else:
            is_structurally_pass = bool(ai_answer.strip())

        is_pass = False
        escalated = False
        failures = []
        answer_is_abstain = is_honest_abstain_answer(ai_answer)
        consensus_missing = False
        single_provider_degraded = False

        tier1 = tier1_check(question, ai_answer, context)
        slm_responses_list = []
        kb_refs: list[dict[str, Any]] = []
        if not tier1.passed:
            failures.extend(tier1.failures)
        elif is_structurally_pass and ai_answer:
            try:
                from scp.data_sources.domain_classifier import classify_top1
                domain = classify_top1(question)
                expert = self.domain_experts.get(domain)
                if expert:
                    resp = expert.predict(question)
                    if getattr(resp, "answer", None):
                        context += f"\n[SYSTEM EXPERT DATA] For {domain}: {resp.answer}"
                        slm_responses_list.append(resp.__dict__)
            except Exception as e:
                # silent-by-design: failure is already logged via getLogger('scp.judge').debug in the handler body
                import logging as _logging
                _logging.getLogger("scp.judge").debug("Expert injection failed: %s", e, exc_info=True)

            kb_refs = self._consult_knowledge(question)
            context = self._inject_kb_refs(context, kb_refs)

            import os as _os
            if _os.environ.get("SCP_MULTI_LLM_CROSSCHECK", "1") == "1":
                try:
                    from scp.runtime.multi_llm_crosscheck import cross_verify
                    cross = await cross_verify(question, ai_answer, context)
                    semantic = cross["final"]
                    if cross["consensus"] == "disagree":
                        failures.append("multi_llm_disagreement")
                    consensus_missing = (
                        str(cross.get("consensus", "")) == "missing_distinct_providers"
                    )
                    if semantic is None and consensus_missing and cross.get("primary", {}).get("verdict") == "PASS":
                        lane = _resolve_lane(question, kwargs)
                        if lane != "LANE_SECURITY" and (
                            lane in ("LANE_CHATBOT", "LANE_CONVERSATIONAL")
                            or _kb_record_supports_query_and_answer(question, ai_answer, kb_refs)
                        ):
                            semantic = "PASS"
                            single_provider_degraded = True
                except Exception as _cc_err:
                    logger.warning(
                        "[SEC-R2-01] async multi-LLM crosscheck failed (%s: %s) — marking DEGRADED/UNCERTAIN fail-closed",
                        type(_cc_err).__name__,
                        _cc_err,
                        exc_info=True,
                    )
                    failures.append("crosscheck_fallback_degraded")
                    semantic = await _llm_judge_async(question, ai_answer, context)
            else:
                lane = _resolve_lane(question, kwargs)
                if lane == "LANE_SECURITY":
                    failures.append("security_crosscheck_required")
                    semantic = "FAIL"
                    single_provider_degraded = False
                else:
                    semantic = await _llm_judge_async(question, ai_answer, context)
                    single_provider_degraded = True

            is_degraded = ("crosscheck_fallback_degraded" in failures) or single_provider_degraded
            if semantic is None:
                escalated = True
            elif semantic == "PASS" or semantic is True:
                if "crosscheck_fallback_degraded" in failures:
                    is_pass = False
                else:
                    is_pass = True
            else:
                failures.append("semantic_judge_fail")

        self.judged_count += 1
        if not is_pass and not escalated:
            self.fail_count += 1

        if escalated:
            return self._format_escalated(
                ai_answer=ai_answer,
                failures=failures,
                slm_responses_list=slm_responses_list,
                kb_refs=kb_refs,
                consensus_missing=consensus_missing,
                answer_is_abstain=answer_is_abstain,
                cycle_count=cycle_count,
                is_structurally_pass=is_structurally_pass,
            )

        return self._format_result(
            is_pass=is_pass,
            is_structurally_pass=is_structurally_pass,
            single_provider_degraded=single_provider_degraded,
            consensus_missing=consensus_missing,
            answer_is_abstain=answer_is_abstain,
            failures=failures,
            kb_refs=kb_refs,
            ai_answer=ai_answer,
            slm_responses_list=slm_responses_list,
            cycle_count=cycle_count,
        )

    @staticmethod
    def _calibrate_confidence(
        is_pass: bool,
        escalated: bool,
        is_structurally_pass: bool,
        failures: list[str],
        kb_refs: list[dict[str, Any]],
        single_provider_degraded: bool = False,
    ) -> tuple[float, float, float]:
        """Dynamically compute calibrated confidence based on verified evidence rather than constants."""
        if "crosscheck_fallback_degraded" in failures:
            det = 0.5 if is_structurally_pass else 0.0
            sem = 0.35
            conf = round(0.30 * det + 0.70 * sem, 4)
            return conf, round(det, 4), round(sem, 4)

        if escalated or not is_pass:
            det = 1.0 if is_structurally_pass else 0.0
            return 0.0, det, 0.0

        if single_provider_degraded:
            det = 1.0 if is_structurally_pass else 0.0
            sem = 0.50
            if kb_refs:
                avg_kb = sum(float(r.get("confidence", 0.8)) for r in kb_refs) / len(kb_refs)
                sem = min(0.65, sem + 0.10 * avg_kb)
            conf = round(0.30 * det + 0.70 * sem, 4)
            return conf, round(det, 4), round(sem, 4)

        det = 1.0 if is_structurally_pass else 0.0
        sem = 0.95

        if kb_refs:
            avg_kb = sum(float(r.get("confidence", 0.8)) for r in kb_refs) / len(kb_refs)
            sem = min(0.99, sem + 0.03 * avg_kb)

        other_failures = [f for f in failures if f != "crosscheck_fallback_degraded"]
        sem = max(0.1, min(1.0, sem - 0.05 * len(other_failures)))

        conf = round(0.30 * det + 0.70 * sem, 4)
        return conf, round(det, 4), round(sem, 4)

    @staticmethod
    def _format_escalated(
        ai_answer: str, failures: list[str], slm_responses_list: list[dict[str, Any]],
        kb_refs: list[dict[str, Any]], consensus_missing: bool, answer_is_abstain: bool,
        cycle_count: int, is_structurally_pass: bool,
    ) -> dict[str, Any]:
        _esc_benign = not _SECURITY_TIER1_TAGS.intersection(failures)
        _abstain_reasons: list[str] = []
        if _esc_benign and "crosscheck_fallback_degraded" not in failures and "multi_llm_disagreement" not in failures:
            if answer_is_abstain:
                _abstain_reasons.append("answer_without_verifiable_claim")
            if consensus_missing:
                _abstain_reasons.append("crosscheck_consensus_missing")

        det_conf = 1.0 if is_structurally_pass else 0.0
        base_evidence = {
            "governance_decision": "ESCALATE",
            "knowledge": kb_refs,
            "cross_model_agreement": False,
            "single_provider_degraded": False,
        }
        if _abstain_reasons:
            base_evidence["abstain_reasons"] = _abstain_reasons
            verdict = "ABSTAIN"
            reasoning = f"Honest abstain — no independent verification available ({', '.join(_abstain_reasons)}); not a FAIL, escalate for audit"
        else:
            verdict = "UNKNOWN"
            reasoning = "Semantic judge unavailable or model disagreement — escalated to human"

        # [C1] Nhãn trung thực: judge không chạy được vì không provider nào dùng được
        # (vd model mặc định 404) khác với "hai model bất đồng".
        _judge_down = _judge_unavailable_reason()
        if _judge_down:
            base_evidence["judge_unavailable_reason"] = _judge_down
            failures = failures + ["judge_llm_unavailable"]
            if verdict == "UNKNOWN":
                reasoning = f"Semantic judge could not run: no usable LLM provider ({_judge_down}) — escalated, not a verdict on the answer"

        return {
            "verdict": verdict,
            "confidence": 0.0,
            "deterministic_confidence": det_conf,
            "semantic_confidence": 0.0,
            "cross_model_agreement": False,
            "degraded": False,
            "single_provider_degraded": False,
            "reasoning": reasoning,
            "cycle_count": cycle_count,
            "failures": failures + ["semantic_judge_unavailable"],
            "final_answer": ai_answer,
            "slm_responses": slm_responses_list,
            "evidence": base_evidence,
        }

    @classmethod
    def _format_result(
        cls,
        is_pass: bool,
        is_structurally_pass: bool,
        single_provider_degraded: bool,
        consensus_missing: bool,
        answer_is_abstain: bool,
        failures: list[str],
        kb_refs: list[dict[str, Any]],
        ai_answer: str,
        slm_responses_list: list[dict[str, Any]],
        cycle_count: int,
    ) -> dict[str, Any]:
        conf, det_conf, sem_conf = cls._calibrate_confidence(
            is_pass=is_pass,
            escalated=False,
            is_structurally_pass=is_structurally_pass,
            failures=failures,
            kb_refs=kb_refs,
            single_provider_degraded=single_provider_degraded,
        )

        is_degraded = ("crosscheck_fallback_degraded" in failures) or single_provider_degraded
        if "crosscheck_fallback_degraded" in failures:
            verdict_val, gov_val, cross_agreement_val = "DEGRADED", "DEGRADED", False
        elif is_pass and single_provider_degraded:
            verdict_val, gov_val, cross_agreement_val = "PASS", "UPHOLD", False
        elif is_pass:
            verdict_val, gov_val = "PASS", "UPHOLD"
            cross_agreement_val = not consensus_missing
        elif _SECURITY_TIER1_TAGS.intersection(failures):
            verdict_val, gov_val, cross_agreement_val = "FAIL", "KILL", False
        elif answer_is_abstain:
            verdict_val, gov_val, cross_agreement_val = "ABSTAIN", "ESCALATE", False
        else:
            verdict_val, gov_val, cross_agreement_val = "FAIL", "ESCALATE", False

        if is_pass and single_provider_degraded:
            reasoning_val = (
                "Single-provider verification (single_provider_degraded; "
                "cross_model_agreement=False); confidence calibrated down"
            )
        elif verdict_val == "ABSTAIN":
            reasoning_val = (
                "Answer is an honest abstention without a verifiable factual "
                "claim — classified ABSTAIN (not FAIL); governance ESCALATE for audit"
            )
        elif gov_val == "ESCALATE" and not is_pass:
            reasoning_val = (
                "Verification failed on a benign request — answer withheld as "
                f"unverified (failures: {', '.join(failures) or 'n/a'}); escalate, not KILL"
            )
        else:
            reasoning_val = "Delegated to IndependentVerifier and LLM Semantic Judge"

        return {
            "verdict": verdict_val,
            "confidence": conf,
            "deterministic_confidence": det_conf,
            "semantic_confidence": sem_conf,
            "cross_model_agreement": cross_agreement_val,
            "degraded": is_degraded,
            "single_provider_degraded": single_provider_degraded,
            "reasoning": reasoning_val,
            "cycle_count": cycle_count,
            "failures": failures,
            "final_answer": ai_answer,
            "slm_responses": slm_responses_list,
            "evidence": {
                "governance_decision": gov_val,
                "knowledge": kb_refs,
                "abstain_reasons": (
                    ["answer_without_verifiable_claim"] if verdict_val == "ABSTAIN" else []
                ),
                "cross_model_agreement": cross_agreement_val,
                "single_provider_degraded": single_provider_degraded,
            },
        }

    async def judge_with_react_fallback(self, *args, **kwargs) -> dict[str, Any]:
        return await self.judge_async(*args, **kwargs)

    def get_stats(self) -> dict:
        return {"total_judged": self.judged_count, "total_failed": self.fail_count}

    def analyze_session_rogue(
        self,
        session_logs: list[dict[str, Any]] | None = None,
        model_responses: list[dict[str, Any]] | None = None,
        *args,
        **kwargs,
    ) -> dict[str, Any]:
        """Assess session across 9 rogue AI behavior lenses."""
        return evaluate_rogue_session(session_logs, model_responses)


__all__ = [
    "RealityJudge", "analyze_session_rogue", "evaluate_rogue_session", "ROGUE_AI_LENSES",
    "_ABSTAIN_ASSERTION_RE", "_ABSTAIN_CLAIM_CUE_RE", "_ABSTAIN_CLASS_MAP",
    "_ABSTAIN_CONV_ASSERTION_RE", "_ABSTAIN_CONV_FRAGMENT_PATTERNS",
    "_ABSTAIN_CONV_FRAGMENT_RE", "_ABSTAIN_CONV_MARKERS", "_ABSTAIN_CONV_WHITELIST_RE",
    "_ABSTAIN_DIGIT_RE", "_ABSTAIN_FREE_NP_MAX_WORDS", "_ABSTAIN_REFUSAL_MARKERS",
    "_ABSTAIN_SEG_ACT_VERBS", "_ABSTAIN_SEG_ANA_NP", "_ABSTAIN_SEG_CONNECTORS",
    "_ABSTAIN_SEG_DESCRIPTORS", "_ABSTAIN_SEG_POLITENESS", "_ABSTAIN_SEG_PREPS",
    "_ABSTAIN_SEG_PRONOUNS", "_ABSTAIN_SEG_TIME", "_ABSTAIN_SEG_TOPICS",
    "_ABSTAIN_TOPIC_CHAIN_MAX", "_ABSTAIN_TOPIC_MODIFIER_MAX", "_ABSTAIN_URL_RE",
    "_SECURITY_TIER1_TAGS", "_TIME_SIGNAL_RE", "_abstain_entries_at",
    "_abstain_starts_with_act", "_refusal_segment_is_safe", "_run_crosscheck_sync",
    "is_honest_abstain_answer", "is_refusal_abstain_answer", "question_has_time_signal",
    "_kb_record_supports_query_and_answer", "_resolve_lane", "_has_factual_query",
]
