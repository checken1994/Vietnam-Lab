"""SCP Python SDK - Drop-in Security & Verification Middleware."""
from __future__ import annotations

try:
    from .protect import SCPProtect, VerificationTier
except ImportError:
    from scp_sdk.protect import SCPProtect, VerificationTier

__version__ = "1.0.0"

__all__ = [
    "SCPProtect",
    "VerificationTier",
    "__version__",
]
