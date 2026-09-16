# SCP CIRCUIT: M11 — STATUS: CLOSED_WITH_KNOWN_GAP (closure: reports/circuit-closures/M11-closure.json)
"""
[Task 7-A] V100 Knowledge endpoints │Ă¢â€Â¬Ă¢â‚¬Â extracted from api_server.py

TÄ‚Â¡Ă‚ÂºĂ‚Â I SAO: api_server.py 2,285 LOC god file. TĂ„â€Ă‚Â¡ch 9 routes /v100/* vĂ„â€Ă‚Â o module
nĂ„â€Ă‚Â y. Backward-compatible │Ă¢â€Â¬Ă¢â‚¬Â public API paths/methods unchanged.

Routes:
  GET  /v100/status               │Ă¢â€Â¬Ă¢â‚¬Â V100 knowledge + timing modules status
  POST /v100/crawl                │Ă¢â€Â¬Ă¢â‚¬Â Trigger scheduled data crawl
  GET  /v100/antibodies/stats     │Ă¢â€Â¬Ă¢â‚¬Â DomainAntibodySystem stats
  POST /v100/antibodies/check     │Ă¢â€Â¬Ă¢â‚¬Â Run antibodies on a question + answer
  GET  /v100/knowledge/stats      │Ă¢â€Â¬Ă¢â‚¬Â DomainKnowledgeStore stats
  GET  /v100/knowledge/search     │Ă¢â€Â¬Ă¢â‚¬Â Search knowledge base
  GET  /v100/h8/stats             │Ă¢â€Â¬Ă¢â‚¬Â H8 RedTeamBridge stats
  GET  /v100/h8/bypasses          │Ă¢â€Â¬Ă¢â‚¬Â Get recent bypasses
  GET  /v100/h8/analyses          │Ă¢â€Â¬Ă¢â‚¬Â Get recent bypass analyses
  GET  /v100/release/evidence      -> Release evidence authority (Wave 1)
  GET  /v100/routing/stats         -> Question-router KPI snapshot (route_stats_snapshot)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

# Import shared deps from api_server (same pattern as api/chat.py)
from scp.api._shared import (
    get_judge,
    verify_admin,
)

from scp.core.request_run_ledger import RequestRunLedger, traced_request

_ADMIN_V100_LEDGER = RequestRunLedger()

router = APIRouter(tags=["v100"])


@router.get("/v100/status", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="v100_status")
async def v100_status():
    """V100 knowledge + timing modules status."""
    judge = get_judge()
    return {
        "v100_modules": judge.get_v100_status(),
        "v98_modules": judge.get_v98_status(),
        "domain_experts": len(judge.domain_experts),
        "slms": len(judge.domain_experts),
    }


@router.post("/v100/crawl")
@traced_request(_ADMIN_V100_LEDGER, require_write=True, action="v100_crawl")
async def v100_crawl(max_per_domain: int = 3, _admin: bool = Depends(verify_admin)):
    """Trigger scheduled data crawl."""
    judge = get_judge()
    result = await judge.run_scheduled_crawl(max_per_domain=max_per_domain)
    return result


@router.get("/v100/antibodies/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="antibody_stats")
async def antibody_stats():
    """DomainAntibodySystem stats."""
    # Antibodies run inline, not stored as instance │Ă¢â€Â¬Ă¢â‚¬Â return static stats
    return {
        "total_antibodies": 38,
        "domains": ["medical", "finance", "legal", "security", "environment", "tech", "general"],
        "antibody_names": [a["name"] for a in __import__("scp.knowledge.antibody_system", fromlist=["ANTIBODIES"]).ANTIBODIES],
    }


@router.post("/v100/antibodies/check")
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="antibody_check")
async def antibody_check(request: Request, _admin: bool = Depends(verify_admin)):
    """Run antibodies on a question + answer."""
    from scp.knowledge.antibody_system import DomainAntibodySystem
    body = await request.json()
    question = body.get("question", "")
    answer = body.get("answer", "")
    domain = body.get("domain", "general")
    system = DomainAntibodySystem()
    results = system.check(question, answer, domain)
    return {
        "total_run": len(results),
        "flagged": sum(1 for r in results if not r.passed),
        "results": [r.to_dict() for r in results],
    }


@router.get("/v100/knowledge/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="knowledge_stats")
async def knowledge_stats():
    """DomainKnowledgeStore stats.

    [B-S1] Trước wire: RealityJudge.domain_knowledge_store trả None cứng →
    endpoint này 503 vĩnh viễn (audit 52-mảnh @9ec8d6b). Sau wire: property
    judge lazy-khởi tạo store thật; response thêm judge_consults (số lần
    judge consult KB — counter ở RealityJudge.knowledge_consult_count).
    """
    judge = get_judge()
    if not judge.domain_knowledge_store:
        raise HTTPException(status_code=503, detail="DomainKnowledgeStore not available")
    data = judge.domain_knowledge_store.stats()
    data["judge_consults"] = getattr(judge, "knowledge_consult_count", 0)
    return data


@router.get("/v100/knowledge/search", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="knowledge_search")
async def knowledge_search(q: str = "", domain: str = "", limit: int = 5):
    """Search knowledge base."""
    judge = get_judge()
    if not judge.domain_knowledge_store:
        raise HTTPException(status_code=503, detail="DomainKnowledgeStore not available")
    results = judge.domain_knowledge_store.search(q, domain, limit)
    return {
        "query": q,
        "results": [
            {
                "question": r.question[:100],
                "answer": r.answer[:100],
                "domain": r.domain,
                "source": r.source,
                "tier": r.source_tier,
                "confidence": r.confidence,
                "collected_at": r.collected_at,
            }
            for r in results
        ],
    }


@router.get("/v100/h8/stats", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="h8_stats")
async def h8_stats():
    """H8 RedTeamBridge stats."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return judge.h8_redteam.stats()


@router.get("/v100/h8/bypasses", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="h8_bypasses")
async def h8_bypasses(limit: int = 20):
    """Get recent bypasses detected by H8."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return {"bypasses": judge.h8_redteam.get_recent_bypasses(limit)}


@router.get("/v100/h8/analyses", dependencies=[Depends(verify_admin)])  # RC-2 FIX: BFLA auth
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="h8_analyses")
async def h8_analyses(limit: int = 20):
    """Get recent bypass analyses (chiÄ‚Â¡Ă‚Â»Ă‚Âu 2 │Ă¢â€Â¬Ă¢â‚¬Â 'tÄ‚Â¡Ă‚ÂºĂ‚Â¡i sao fail?')."""
    judge = get_judge()
    if not judge.h8_redteam:
        raise HTTPException(status_code=503, detail="H8RedTeamBridge not available")
    return {"analyses": judge.h8_redteam.get_recent_analyses(limit)}

@router.get("/v100/release/evidence", dependencies=[Depends(verify_admin)])
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="release_evidence")
async def release_evidence():
    """Release evidence authority endpoint (Wave 1)."""
    from scp.release.evidence_authority import EvidenceAuthority as ReleaseEvidenceAuthority

    repo_root = Path(__file__).resolve().parents[3]
    output_dir = repo_root / os.environ.get("SCP_DATA_DIR", "data")
    output_path = output_dir / "release_evidence.json"

    try:
        auth = ReleaseEvidenceAuthority(repo_root)
        env_sha = os.environ.get("SCP_GIT_SHA", "").strip()
        if env_sha and len(env_sha) == 40:
            tested_sha = auth._resolve_commit(env_sha)
        else:
            tested_sha = auth._resolve_commit("HEAD")

        evidence = auth.generate_evidence(output=output_path, tested_sha=tested_sha)
        return {"evidence": evidence}
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Evidence generation unavailable: {exc}",
        ) from exc


@router.get("/v100/routing/stats", dependencies=[Depends(verify_admin)])
@traced_request(_ADMIN_V100_LEDGER, require_write=False, action="routing_stats")
async def routing_stats():
    """Question-router KPI snapshot (S24 fork + F-2 auto-retrieval + Q07 correction).

    Trả đúng `route_stats_snapshot()` mà `GET /health/detailed` nhúng dưới key
    `question_routing` (api_server._question_routing_stats). Route thuộc nhóm
    `versioned_admin` nên chỉ mount khi SCP_API_PROFILE=full; container production
    chạy core vẫn dùng seam /health/detailed như thiết kế S24 — thêm route ở đây
    không đổi hành vi core.

    Fail-closed: lỗi snapshot -> 503 (không biến failure thành 200 rỗng); auth
    verify_admin ở route-level, không có nhánh bypass.
    """
    from scp.runtime.question_router import route_stats_snapshot

    try:
        return route_stats_snapshot()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Routing stats unavailable: {type(exc).__name__}",
        ) from exc
