"""Brain Layer H2 — DatasetFactory: biến BrainTrace (shadow) thành dataset eval.

Bất biến privacy (DNA #16): BrainTrace KHÔNG BAO GIỜ chứa prompt — dataset
từ trace vì thế chỉ chứa input_hash + nhãn (production_verdict từ record so
sánh shadow) + timestamp; `payload` của sample trace-derived là None. Muốn
re-run được cần dataset có payload (như config/brain/eval_datasets/*.jsonl).

- Deduplicate theo input_hash (giữ bản CỔ NHẤT theo ts — ổn định, tái hiện được).
- Chia train/dev/test theo timestamp (70/15/15, min 1 mỗi phần khi n>=3).
- Version deterministic theo nội dung: sha256 của các input_hash — cùng traces
  ⇒ cùng version (provenance không phụ thuộc thời điểm build).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

TRACE_SOURCE = "shadow_trace"


@dataclass(frozen=True)
class EvalSample:
    """1 mẫu dataset. Với sample trace-derived: payload=None (không có prompt),
    expected chỉ là nhãn đọc từ record (production_verdict nếu có)."""

    sample_id: str
    input_hash: str
    expected: Mapping[str, Any]
    ts: str
    source: str
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Dataset:
    """Dataset eval có version + splits (chỉ-đọc)."""

    name: str
    version: str
    source: str
    samples: tuple[EvalSample, ...]
    splits: Mapping[str, tuple[EvalSample, ...]]
    created_at: str

    def __len__(self) -> int:
        return len(self.samples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "total": len(self.samples),
            "splits": {k: len(v) for k, v in self.splits.items()},
            "created_at": self.created_at,
        }


def _split_by_timestamp(samples: list[EvalSample]) -> dict[str, tuple[EvalSample, ...]]:
    """Chia theo ts (ISO sort được): 70/15/15; n<3 ⇒ tất cả train (không chia
    ảo); n>=3 ⇒ mỗi phần tối thiểu 1."""
    ordered = sorted(samples, key=lambda s: s.ts)
    n = len(ordered)
    if n == 0:
        return {"train": (), "dev": (), "test": ()}
    if n < 3:
        return {"train": tuple(ordered), "dev": (), "test": ()}
    n_dev = max(1, round(n * 0.15))
    n_test = max(1, round(n * 0.15))
    n_train = n - n_dev - n_test
    if n_train < 1:
        n_train = 1
    train = ordered[:n_train]
    dev = ordered[n_train:n_train + n_dev]
    test = ordered[n_train + n_dev:]
    return {"train": tuple(train), "dev": tuple(dev), "test": tuple(test)}


def DatasetFactory(
    trace_path: str | Path,
    *,
    name: str = "trace_dataset",
) -> Dataset:
    """Xây Dataset từ BrainTrace JSONL (shadow). Dòng hỏng/missing input_hash
    bị bỏ (trung thực — không bịa hash). Dedup theo input_hash giữ bản cũ nhất."""
    path = Path(trace_path)
    rows: list[tuple[str, str, Mapping[str, Any]]] = []  # (ts, input_hash, expected)
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            input_hash = str(record.get("input_hash") or "").strip()
            if not input_hash:
                continue
            ts = str(record.get("ts") or "")
            expected: dict[str, Any] = {}
            production_verdict = record.get("production_verdict")
            if isinstance(production_verdict, str) and production_verdict.strip():
                expected["verdict"] = production_verdict.strip().upper()
            rows.append((ts, input_hash, MappingProxyType(expected)))

    # Dedup theo input_hash — sort theo ts rồi giữ lần đầu (cổ nhất).
    rows.sort(key=lambda r: r[0])
    by_hash: dict[str, EvalSample] = {}
    for ts, input_hash, expected in rows:
        if input_hash in by_hash:
            continue
        by_hash[input_hash] = EvalSample(
            sample_id=input_hash,
            input_hash=input_hash,
            expected=expected,
            ts=ts,
            source=TRACE_SOURCE,
            payload=None,
        )

    samples = list(by_hash.values())
    if samples:
        digest = hashlib.sha256(
            "|".join(sorted(by_hash)).encode("utf-8")
        ).hexdigest()[:12]
        version = f"v1.{digest}"
    else:
        version = "v0.empty"

    return Dataset(
        name=name,
        version=version,
        source="brain_trace",
        samples=tuple(samples),
        splits=MappingProxyType(_split_by_timestamp(samples)),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
