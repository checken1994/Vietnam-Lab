"""Deterministic Mathematical, Unit Conversion, and Boundary Engine for SCP-Bench 500.

Provides hermetic, verified mathematical solving capabilities for Track 3.
Re-exports DeterministicMathSolver and utilities from core runtime scp.runtime.deterministic_math.

Strictly <= 800 LOC ceiling (ARC-01 compliance).
"""
from __future__ import annotations

from scp.runtime.deterministic_math import (
    DeterministicMathSolver,
    fibonacci,
    is_false_premise,
    is_prime,
    prime_factors,
    sum_divisors,
)

__all__ = [
    "DeterministicMathSolver",
    "fibonacci",
    "is_false_premise",
    "is_prime",
    "prime_factors",
    "sum_divisors",
]
