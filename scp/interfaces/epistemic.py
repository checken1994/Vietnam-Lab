from __future__ import annotations
from typing import Protocol, runtime_checkable, Any
from pathlib import Path

@runtime_checkable
class IEvidenceStore(Protocol):
    objects_dir: Path
    db: Any

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        ...

    def observe(self, **kwargs: Any) -> dict[str, Any]:
        ...
