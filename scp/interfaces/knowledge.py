"""Knowledge interfaces and protocols."""
from __future__ import annotations
from typing import Protocol, runtime_checkable, Any, List, Dict, Optional

@runtime_checkable
class ILearningDB(Protocol):
    def execute_insert(self, table: str, data: Dict[str, Any]) -> int:
        ...
    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        ...
