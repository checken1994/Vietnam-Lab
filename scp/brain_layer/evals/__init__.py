"""Brain Layer H2 — eval subpackage (offline harness, ngoài request path)."""
from __future__ import annotations

from scp.brain_layer.evals.datasets import Dataset, DatasetFactory, EvalSample
from scp.brain_layer.evals.runner import (
    EvalReport,
    EvalRunner,
    metrics_from_report,
)

__all__ = [
    "Dataset",
    "DatasetFactory",
    "EvalReport",
    "EvalRunner",
    "EvalSample",
    "metrics_from_report",
]
