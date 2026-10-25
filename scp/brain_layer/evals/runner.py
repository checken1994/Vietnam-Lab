"""Brain Layer H2 — Eval harness offline (chạy NGOÀI request path).

`EvalRunner` nạp dataset JSONL, chạy từng mẫu qua BrainLayer entry OFFLINE
(`BrainLayer.run_task` — không đọc mode env, không qua canary gate; eval chủ
động chọn challenger cần đo), so output với expected và tính metrics cho
PromotionGate. Baseline (tùy chọn) chạy song song trên cùng dataset để đo
disagreement giữa challenger và baseline — feeds anti-placebo promotion.

Fail-closed: mẫu thiếu expected/expected_verdict tính FAILED (không được mặc
nhiên là pass); dataset rỗng ⇒ metrics bằng 0 (sẽ BLOCK promotion, không HOLD
giả "tốt mặc định"). KHÔNG network trừ khi adapter được DI là adapter thật —
test dùng FakeAdapter.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scp.brain_layer.adapters.base import ProviderAdapter
from scp.brain_layer.brain_layer import BrainLayer
from scp.brain_layer.config import BrainConfig
from scp.brain_layer.contracts import (
    STATUS_OK,
    STATUS_SCHEMA_INVALID,
    BrainContext,
)
from scp.brain_layer.errors import BrainSchemaInvalid
from scp.brain_layer.trace import TraceWriter

#: Keys payload được nhận diện từ dòng dataset khi không có field `payload`.
PAYLOAD_KEYS: tuple[str, ...] = ("question", "final_answer", "context", "claims")


@dataclass(frozen=True)
class EvalReport:
    """Kết quả 1 lần run_eval — metrics đo được, không tự bịa."""

    task: str
    dataset: str
    total: int
    passed: int
    failed: int
    schema_errors: int
    avg_latency_ms: float
    disagreement_count: int
    pass_rate: float = 0.0
    schema_valid_rate: float = 0.0
    disagreement_rate: float = 0.0
    baseline_present: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "dataset": self.dataset,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "schema_errors": self.schema_errors,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "disagreement_count": self.disagreement_count,
            "pass_rate": round(self.pass_rate, 6),
            "schema_valid_rate": round(self.schema_valid_rate, 6),
            "disagreement_rate": round(self.disagreement_rate, 6),
            "baseline_present": self.baseline_present,
            "warnings": list(self.warnings),
        }


def metrics_from_report(report: EvalReport) -> dict[str, float]:
    """Metrics đầu vào chuẩn cho PromotionGate.evaluate()."""
    return {
        "pass_rate": report.pass_rate,
        "schema_valid_rate": report.schema_valid_rate,
        "avg_latency_ms": report.avg_latency_ms,
        "disagreement_rate": report.disagreement_rate,
    }


def _load_dataset_lines(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Nạp JSONL: 1 mẫu/line; dòng hỏng bị BỎ với warning (không crash, không
    bịa mẫu thay thế — total chỉ đếm mẫu thật)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrainSchemaInvalid(f"không đọc được eval dataset '{path}': {exc}") from exc
    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            warnings.append(f"dòng {lineno}: JSON hỏng, bỏ qua ({exc})")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"dòng {lineno}: mẫu không phải object, bỏ qua")
            continue
        lines.append(parsed)
    return lines, warnings


def _payload_of(line: Mapping[str, Any]) -> dict[str, Any]:
    payload = line.get("payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    return {key: line[key] for key in PAYLOAD_KEYS if key in line}


def _expected_of(line: Mapping[str, Any]) -> Mapping[str, Any] | None:
    expected = line.get("expected")
    if isinstance(expected, Mapping):
        return dict(expected)
    if "expected_verdict" in line:
        return {"verdict": line["expected_verdict"]}
    return None


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.strip().upper() == expected.strip().upper()
    return actual == expected


def _output_matches(output: Any, expected: Mapping[str, Any]) -> bool:
    if not isinstance(output, Mapping):
        return False
    return all(
        key in output and _value_matches(output[key], value)
        for key, value in expected.items()
    )


def _outputs_disagree(a: Any, b: Any) -> bool:
    """Disagreement challenger vs baseline: so `verdict` (chuẩn hóa upper) khi
    cả hai có; nếu không có verdict ⇒ so nguyên output mapping."""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return True
    if "verdict" in a and "verdict" in b:
        return (
            str(a["verdict"]).strip().upper() != str(b["verdict"]).strip().upper()
        )
    return dict(a) != dict(b)


class EvalRunner:
    """Chạy bộ eval offline qua BrainLayer. DI thuần: config + adapters +
    trace; baseline_adapters (tùy chọn) dựng layer baseline để đo disagreement."""

    def __init__(
        self,
        config: BrainConfig,
        adapters: Mapping[str, ProviderAdapter],
        trace: TraceWriter | None = None,
        baseline_adapters: Mapping[str, ProviderAdapter] | None = None,
    ) -> None:
        self.layer = BrainLayer(config, adapters=adapters, trace=trace or TraceWriter())
        self.baseline_layer: BrainLayer | None = (
            BrainLayer(
                config, adapters=baseline_adapters, trace=trace or TraceWriter()
            )
            if baseline_adapters
            else None
        )

    async def run_eval_async(self, task_id: str, dataset_path: str | Path) -> EvalReport:
        path = Path(dataset_path)
        lines, warnings = _load_dataset_lines(path)

        total = passed = failed = schema_errors = disagreement = 0
        latencies: list[float] = []
        baseline_used = False

        for index, line in enumerate(lines):
            sample_id = str(line.get("id") or f"{task_id}-{index:03d}")
            expected = _expected_of(line)
            if expected is None:
                # Fail-closed: mẫu không đo được expectation ⇒ FAILED, không
                # được mặc nhiên tính pass.
                total += 1
                failed += 1
                warnings.append(
                    f"mẫu '{sample_id}': thiếu expected/expected_verdict — tính FAILED"
                )
                continue
            payload = _payload_of(line)
            ctx = BrainContext(run_id=f"eval:{path.stem}:{sample_id}", task_id=task_id)
            resp = await self.layer.run_task(task_id, payload, ctx)
            total += 1
            latencies.append(resp.latency_ms)
            if resp.status == STATUS_SCHEMA_INVALID:
                schema_errors += 1
            if resp.status == STATUS_OK and _output_matches(resp.output, expected):
                passed += 1
            else:
                failed += 1

            if self.baseline_layer is not None:
                base_ctx = BrainContext(
                    run_id=f"eval-baseline:{path.stem}:{sample_id}", task_id=task_id
                )
                base_resp = await self.baseline_layer.run_task(task_id, payload, base_ctx)
                if resp.status == STATUS_OK and base_resp.status == STATUS_OK:
                    baseline_used = True
                    if _outputs_disagree(resp.output, base_resp.output):
                        disagreement += 1

        if not total:
            warnings.append("dataset rỗng — metrics bằng 0 (fail-closed)")
        if self.baseline_layer is not None and not baseline_used:
            warnings.append(
                "baseline không trả status=ok ở mẫu nào — disagreement không đo được"
            )

        return EvalReport(
            task=task_id,
            dataset=path.stem,
            total=total,
            passed=passed,
            failed=failed,
            schema_errors=schema_errors,
            avg_latency_ms=(sum(latencies) / len(latencies)) if latencies else 0.0,
            disagreement_count=disagreement,
            pass_rate=(passed / total) if total else 0.0,
            schema_valid_rate=((total - schema_errors) / total) if total else 0.0,
            disagreement_rate=(disagreement / total) if total else 0.0,
            baseline_present=baseline_used,
            warnings=tuple(warnings),
        )

    def run_eval(self, task_id: str, dataset_path: str | Path) -> EvalReport:
        """Wrapper sync cho test/CLI (eval offline, không chạy trong event loop
        của request path)."""
        return asyncio.run(self.run_eval_async(task_id, dataset_path))
