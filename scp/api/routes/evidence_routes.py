"""Read-only autonomous evidence ticket route (esc-auto slice 2).

``GET /ask/evidence/{ticket_id}`` lets the asker see how SCP resolved its own
ESCALATE: PENDING, RESOLVED_CITED (verbatim receipt-verified source, never the
withheld AI draft) or UNKNOWN_WITH_REASON (true reason code). Same auth as
``/ask`` (signed JWT). Ticket ids are random 128-bit capabilities; the view
carries no question text and no draft.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

from scp.security.jwt_guard import verify_jwt_token

router = APIRouter(tags=["evidence"])


@router.get("/ask/evidence/{ticket_id}")
async def evidence_ticket(
    ticket_id: str = Path(min_length=8, max_length=64, pattern=r"^ev-[A-Za-z0-9_-]+$"),
    current_user: Any = Depends(verify_jwt_token),  # noqa: B008 — FastAPI DI idiom
) -> dict[str, Any]:
    from scp.core.evidence_resolution import get_evidence_queue

    queue = get_evidence_queue()
    if queue is None:
        raise HTTPException(503, "Evidence resolver is unavailable")
    view = queue.get(ticket_id)
    if view is None:
        raise HTTPException(404, "Evidence ticket not found")
    return view
