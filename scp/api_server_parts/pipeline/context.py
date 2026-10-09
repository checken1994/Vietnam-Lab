# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from scp.api_server_parts.helpers import AskRequest, AskResponse


@dataclass
class AskPipelineContext:
    """Execution context carrying request data, intermediate state, and results

    across the 5 /ask pipeline stages.
    """

    req: AskRequest
    request: Request
    namespace: dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    judge: Any = None

    # Ingress & Security (Stage 1)
    v98_context: dict[str, Any] = field(default_factory=dict)
    client_ip: str = "unknown"
    dos_slot_taken: bool = False
    detector_degraded: bool = False
    detector_notes: list[str] = field(default_factory=list)
    multimodal_block: bool = False
    voice_transcription: str = ""
    img_bytes: bytes | None = None
    early_response: Any = None

    # Lookup & Routing (Stage 2)
    route_decision: Any = None
    is_chatbot_lane: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    pre_gen_evidence: list[str] = field(default_factory=list)
    web_fallback_used: bool = False
    web_fallback: dict[str, Any] = field(default_factory=dict)
    retrieval_res: dict[str, Any] = field(default_factory=dict)
    has_provided_evidence: bool = False
    fact_check_degraded: bool = False
    fact_check_note: str = ""

    # Generation (Stage 3)
    ai_answer: str = ""
    llm_provider: str = ""
    llm_context: str = ""
    system_prompt: str = ""

    # Verification (Stage 4)
    clean_evidence: list[str] = field(default_factory=list)
    injection_blocked: int = 0
    judge_verdict: Any = None
    gov_decision: str = ""
    api_final_answer: str = ""
    api_reasoning: str | None = None
    is_true_security_threat: bool = False
    verified_facts: list[dict[str, Any]] = field(default_factory=list)
    llm_reasoning: str = ""
    confidence_badge: dict[str, Any] | None = None
    slm_trace: list[dict[str, Any]] = field(default_factory=list)
    slm_responses: list[dict[str, Any]] = field(default_factory=list)
    phase_timings: dict[str, Any] = field(default_factory=dict)
    mt_result: Any = None
    api_v100_claims: Any = None
    api_v103_antibodies: Any = None
    api_speculative_mode: Any = None
    api_v98_canary_token: Any = None
    api_v98_guard: Any = None
    api_v98_classification: Any = None
    api_v98_attack_policy: Any = None
    api_v98_counter_executed: Any = None
    api_v98_bypass_recorded: Any = None
    api_falsification_status: Any = None

    # Ledger & Trace (Stage 5)
    trace_id: str = ""
    run_id: str = ""
    elapsed_ms: float = 0.0
    final_response: AskResponse | None = None

    def resolve(self, name: str, default: Any = None) -> Any:
        """Resolve a function or variable dynamically to respect monkeypatching.

        Checks:
          1) context.namespace (caller scope)
          2) scp.api_server_parts._ask_impl module scope
          3) default fallback
        """
        if name in self.namespace:
            return self.namespace[name]
        ask_mod = sys.modules.get("scp.api_server_parts._ask_impl")
        if ask_mod is None:
            try:
                import scp.api_server_parts._ask_impl as ask_mod
            except (ImportError, AttributeError):
                ask_mod = None
        if ask_mod and hasattr(ask_mod, name):
            return getattr(ask_mod, name)
        return default
