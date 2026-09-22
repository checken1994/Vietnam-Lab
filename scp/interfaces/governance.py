from __future__ import annotations
from typing import Protocol, runtime_checkable, Any
from enum import Enum

class PrivacyDecision(str, Enum):
    ALLOW = "ALLOW"
    REDACT = "REDACT"
    DENY_STORAGE = "DENY_STORAGE"

@runtime_checkable
class IPrivacyWriteGate(Protocol):
    def evaluate_write(self, target: str, payload: Any, metadata: dict[str, Any]) -> Any:
        ...
