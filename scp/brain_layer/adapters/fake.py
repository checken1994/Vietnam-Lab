"""Brain Layer P0 — FakeAdapter deterministic (mode=off/shadow, test only).

KHÔNG network, KHÔNG model thật. Output sinh deterministic từ (task, payload)
và tự động phủ đủ các key `output_required` của task để dùng được trong test
schema; có thể override `outputs`/`fail_for` để mô phỏng lỗi và output sai
schema (anti-placebo).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from scp.brain_layer.adapters.base import AdapterResult, ProviderAdapter
from scp.brain_layer.contracts import BrainContext
from scp.brain_layer.errors import BrainProviderError, BrainTimeout

# Verdict placeholder hợp lệ cho task judge khi fill output_required.
_FAKE_VERDICT = "pass"


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


class FakeAdapter(ProviderAdapter):
    """Adapter giả deterministic — ghi lại mọi lần gọi để test assert."""

    def __init__(
        self,
        provider_id: str = "local-fake",
        *,
        outputs: Mapping[str, Mapping[str, Any]] | None = None,
        fail_for: Mapping[str, str] | None = None,
        model_id: str = "fake-model",
    ) -> None:
        self.provider_id = provider_id
        # Giữ nguyên giá trị override (kể cả kiểu sai) — FakeAdapter dùng để
        # mô phỏng cả output dị dạng cho test anti-placebo.
        self._outputs = dict(outputs or {})
        self._fail_for = dict(fail_for or {})
        self._model_id = model_id
        # (task, input_hash) mỗi lần run() được gọi — dùng assert "adapter
        # KHÔNG được gọi khi mode=off".
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def input_hash(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    def _deterministic_output(self, task: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        # Default output: phủ mọi key mà BrainLayer thường yêu cầu + echo hash
        # đầu vào (KHÔNG echo prompt thô).
        return {
            "task": task,
            "answer": f"fake:{task}",
            "verdict": _FAKE_VERDICT,
            "claims": [],
            "target_task": "",
            "explanation": f"fake:{task}",
            "steps": [],
            "patch": "",
            "echo_input_hash": self.input_hash(payload),
        }

    async def run(
        self,
        task: str,
        payload: Mapping[str, Any],
        ctx: BrainContext,
    ) -> AdapterResult:
        self.calls.append((task, self.input_hash(payload)))
        fail_kind = self._fail_for.get(task)
        if fail_kind:
            if fail_kind == "timeout":
                raise BrainTimeout(
                    f"fake timeout for task '{task}'", provider_id=self.provider_id
                )
            raise BrainProviderError(
                f"fake failure '{fail_kind}' for task '{task}'",
                kind=fail_kind,
                provider_id=self.provider_id,
            )
        if task in self._outputs:
            output: Any = self._outputs[task]  # giữ nguyên, kể cả kiểu sai (test)
        else:
            output = self._deterministic_output(task, payload)
        usage = {
            "input_tokens": len(_canonical(payload)) // 4 + 1,
            "output_tokens": len(_canonical(output)) // 4 + 1,
            "provider": "fake",
        }
        return AdapterResult(output=output, usage=usage, model_id=self._model_id)
