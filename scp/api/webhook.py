"""
[OPT-41] Webhook API -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â allow external systems to send prompts for analysis.

DNA SCP #6 Evidence: External AI systems need to send prompts to SCP.
DNA SCP #9 No harm: Webhook is read-only (analyze, don't execute).

Endpoints:
  POST /api/analyze    -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â Analyze prompt, return action (allow/block/log)
  POST /api/register   -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â Register a new system for protection
  GET  /api/threats    -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â List recent threats detected
  GET  /api/alerts     -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â List recent alerts

Usage (external system):
    POST /api/analyze
    {
        "prompt": "user input here",
        "system_id": "my-ai-chatbot",
        "context": {"user_id": "123", "session": "abc"}
    }

    Response:
    {
        "action": "allow",  // allow | block | log
        "verdict": "PASS",
        "confidence": 0.95,
        "reason": "Safe prompt",
        "threats": [],
        "elapsed_ms": 123
    }
"""
from __future__ import annotations

from collections import deque
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from scp.core.request_run_ledger import RequestRunLedger, traced_request

logger = logging.getLogger("scp.api.webhook")

router = APIRouter(prefix="/api", tags=["webhook"])
_WEBHOOK_LEDGER = RequestRunLedger()  # P2_WEBHOOK_LEDGER


class AnalyzeRequest(BaseModel):
    """Request from external system to analyze a prompt."""
    prompt: str = Field(..., min_length=1, max_length=8000, description="Prompt to analyze")
    system_id: str = Field("default", max_length=100, description="System identifier")
    ai_answer: str = Field("", max_length=10000, description="Optional AI answer to verify")
    context: dict = Field(default_factory=dict, description="Additional context (user_id, session, etc.)")


class AnalyzeResponse(BaseModel):
    """Response with action decision."""
    action: str  # allow | block | log
    verdict: str  # PASS | FAIL | UNKNOWN | CONFLICT | KILL
    confidence: float
    reason: str
    threats: list[str] = []
    domain: str = "general"
    elapsed_ms: float = 0.0
    scp_answer: str = ""
    metadata: dict = {}
    run_id: str | None = None
    trace_id: str | None = None
    run_status: str | None = None
    ledger_status: str | None = None


class RegisterRequest(BaseModel):
    """Register a system for SCP protection."""
    system_id: str = Field(..., max_length=100)
    system_name: str = Field("", max_length=200)
    webhook_url: str = Field("", max_length=500, description="Callback URL for alerts")


# In-memory registry (production: use DB)
_registered_systems: dict[str, dict] = {}
_threat_history: deque[dict] = deque(maxlen=1000)
_alert_history: deque[dict] = deque(maxlen=1000)


def _require_admin(request: Request | None) -> None:
    """Enforce the canonical admin-auth contract for every webhook endpoint."""
    from scp.api._shared import verify_admin

    if request is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "", 1) if auth_header.startswith("Bearer ") else ""
    if not verify_admin(token=token, request=request):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/analyze", response_model=AnalyzeResponse)
@traced_request(_WEBHOOK_LEDGER, require_write=False, action="webhook_analyze")
async def analyze_prompt(req: AnalyzeRequest, request: Request):
    """Analyze a prompt and return action (allow/block/log).

    This is the MAIN endpoint for external systems.
    External AI -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬Ă¢â‚¬ÂĂ‚Â¢ POST /api/analyze -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬Ă¢â‚¬ÂĂ‚Â¢ get action -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬Ă¢â‚¬ÂĂ‚Â¢ allow/block prompt.
    """
    _require_admin(request)

    start = time.time()

    try:
        # Get judge instance
        from scp.api._shared import get_judge
        judge = get_judge()

        # Call judge (use async if available)
        if hasattr(judge, "judge_with_react_fallback"):
            import asyncio
            verdict = await judge.judge_with_react_fallback(
                question=req.prompt,
                ai_answer=req.ai_answer,
                cycle_count=0,
                source=f"webhook:{req.system_id}",
                v98_context={"ip": request.client.host if request.client else "unknown",
                            "system_id": req.system_id,
                            **req.context},
            )
        else:
            import asyncio
            verdict = await asyncio.to_thread(
                judge.judge,
                question=req.prompt,
                ai_answer=req.ai_answer,
                cycle_count=0,
                source=f"webhook:{req.system_id}",
            )

        # Determine action
        verdict_str = getattr(verdict, "verdict", "UNKNOWN").upper()
        confidence = getattr(verdict, "confidence", 0.0)

        if verdict_str in ("FAIL", "KILL"):
            action = "block"
            reason = f"Blocked: {verdict_str} (confidence={confidence:.2f})"
            # Record threat
            _threat_history.append({
                "system_id": req.system_id,
                "prompt": req.prompt[:200],
                "verdict": verdict_str,
                "confidence": confidence,
                "timestamp": time.time(),
            })
        elif verdict_str == "UNKNOWN" and confidence < 0.3:
            action = "block"
            reason = f"Blocked: uncertain (confidence={confidence:.2f})"
        elif verdict_str == "UNKNOWN":
            action = "log"
            reason = f"Logged: uncertain (confidence={confidence:.2f})"
        else:
            action = "allow"
            reason = f"Allowed: {verdict_str} (confidence={confidence:.2f})"

        elapsed_ms = (time.time() - start) * 1000

        return AnalyzeResponse(
            action=action,
            verdict=verdict_str,
            confidence=confidence,
            reason=reason,
            threats=getattr(verdict, "evidence", {}).get("v102_unified_detection", {}).get("matched_patterns", []),
            domain=getattr(verdict, "domain", "general"),
            elapsed_ms=elapsed_ms,
            scp_answer=getattr(verdict, "final_answer", "")[:500],
            metadata={"system_id": req.system_id},
        )

    except Exception as e:
        logger.error(f"[Webhook] analyze error: {e}")
        raise HTTPException(status_code=500, detail="Analysis failed -Ă¢â‚¬Â-Ă‚Â¢Ă„â€Ă‚Â¢Ă¢â€Â¬Ă‚Â-Ă‚Â¬Ă„â€Ă‚Â¢Ă¢â‚¬ÂĂ‚Â¬-Ă‚Â see server logs") from e


@router.post("/register")
async def register_system(req: RegisterRequest, request: Request):
    """Register a system for SCP protection."""
    _require_admin(request)

    _registered_systems[req.system_id] = {
        "system_name": req.system_name,
        "webhook_url": req.webhook_url,
        "registered_at": time.time(),
    }
    logger.info(f"[Webhook] System registered: {req.system_id} ({req.system_name})")
    return {"status": "registered", "system_id": req.system_id}


@router.get("/threats")
async def list_threats(request: Request, limit: int = 50):
    """List recent threats detected."""
    _require_admin(request)

    return {
        "total": len(_threat_history),
        "threats": list(_threat_history)[-limit:] if limit > 0 else list(_threat_history),
    }


@router.get("/alerts")
async def list_alerts(request: Request, limit: int = 50):
    """List recent alerts."""
    _require_admin(request)

    return {
        "total": len(_alert_history),
        "alerts": list(_alert_history)[-limit:] if limit > 0 else list(_alert_history),
    }


@router.get("/systems")
async def list_systems(request: Request):
    """List registered systems."""
    _require_admin(request)

    return {
        "total": len(_registered_systems),
        "systems": list(_registered_systems.keys()),
    }
