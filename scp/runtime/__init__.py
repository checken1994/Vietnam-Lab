from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .atomic_verifier import (
    AtomicClaimSplitter,
    AtomicDeterministicVerifier,
    is_atomic_verifier_enabled,
)
from .engine import SCPV14

# Ensure atomic_verifier is always registered in sys.modules even on FUSE filesystems with cached readdir
_av_path = Path(__file__).resolve().parent / "atomic_verifier.py"
if "scp.runtime.atomic_verifier" not in sys.modules and _av_path.exists():
    _spec = importlib.util.spec_from_file_location("scp.runtime.atomic_verifier", _av_path)
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["scp.runtime.atomic_verifier"] = _mod
        _spec.loader.exec_module(_mod)
        atomic_verifier = _mod

__all__ = [
    "SCPV14",
    "AtomicClaimSplitter",
    "AtomicDeterministicVerifier",
    "is_atomic_verifier_enabled",
]
