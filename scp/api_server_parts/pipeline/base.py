# SCP CIRCUIT: M02 — Pipeline Pattern Refactor
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scp.api_server_parts.pipeline.context import AskPipelineContext


class PipelineStage(ABC):
    """Abstract base class for all /ask pipeline stages."""

    name: str = "base_stage"

    @abstractmethod
    async def execute(self, ctx: AskPipelineContext) -> None:
        """Execute stage logic. Mutates ctx or sets ctx.early_response."""
        raise NotImplementedError
