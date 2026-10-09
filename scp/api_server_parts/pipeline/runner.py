# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from scp.api_server_parts.helpers import AskRequest, AskResponse, get_judge
from scp.api_server_parts.pipeline.base import PipelineStage
from scp.api_server_parts.pipeline.context import AskPipelineContext
from scp.api_server_parts.pipeline.generation_stage import GenerationStage
from scp.api_server_parts.pipeline.ledger_stage import LedgerStage
from scp.api_server_parts.pipeline.lookup_stage import LookupStage
from scp.api_server_parts.pipeline.security_stage import SecurityStage
from scp.api_server_parts.pipeline.verification_stage import VerificationStage

logger = logging.getLogger(__name__)


class AskPipelineRunner:
    """Orchestrates sequential execution of the 5 /ask pipeline stages

    with guaranteed DoS quota slot release on any error or early exit.
    """

    def __init__(self, stages: list[PipelineStage] | None = None) -> None:
        self.stages = stages or [
            SecurityStage(),
            LookupStage(),
            GenerationStage(),
            VerificationStage(),
            LedgerStage(),
        ]

    async def run(
        self, req: AskRequest, request: Request, namespace: dict[str, Any] | None = None
    ) -> AskResponse:
        ctx = AskPipelineContext(req=req, request=request, namespace=namespace or {})
        judge_fn = ctx.resolve("get_judge", get_judge)
        judge = judge_fn() if callable(judge_fn) else None
        ctx.judge = judge
        try:
            for stage in self.stages:
                await stage.execute(ctx)
                if ctx.early_response is not None:
                    return ctx.early_response
            return ctx.final_response  # type: ignore[return-value]
        finally:
            active_judge = ctx.judge or judge
            if ctx.dos_slot_taken and active_judge and hasattr(active_judge, "dos_protection") and active_judge.dos_protection:
                try:
                    active_judge.dos_protection.release_slot()
                except Exception as _dos_release_err:
                    logger.debug(
                        f"[V104.17] DoS slot release error in pipeline runner: {_dos_release_err}",
                        exc_info=True,
                    )
