from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_THINK_BLOCK_RE = re.compile(r"(?is)<(think|reasoning)\b[^>]*>.*?</\1>")
_UNCLOSED_THINK_RE = re.compile(r"(?is)<(think|reasoning)\b[^>]*>.*$")


def strip_reasoning_tags(text: str | None) -> str:
    """Remove all <think>...</think> and <reasoning>...</reasoning> blocks (including unclosed blocks).

    Mandatory per scp-reality-verifier: prevents keywords inside reasoning traces
    from causing False Positive PASS/VERIFIED verdicts.
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", str(text))
    cleaned = _UNCLOSED_THINK_RE.sub("", cleaned)
    return cleaned.strip()


class StatefulThinkStreamStripper:
    """Incremental SSE stream processor that separates <think> reasoning tokens from visible output tokens."""

    def __init__(self) -> None:
        self._in_think = False
        self._buffer = ""
        self.reasoning_chunks: list[str] = []
        self.visible_chunks: list[str] = []

    def feed(self, chunk: str) -> str:
        """Feed an SSE text delta and return only the visible (non-thinking) text delta."""
        self._buffer += chunk or ""
        emitted: list[str] = []
        while self._buffer:
            if not self._in_think:
                idx = self._buffer.lower().find("<think>")
                if idx == -1:
                    partial = self._find_partial_prefix(self._buffer, "<think>")
                    safe_len = len(self._buffer) - partial
                    if safe_len > 0:
                        part = self._buffer[:safe_len]
                        emitted.append(part)
                        self.visible_chunks.append(part)
                        self._buffer = self._buffer[safe_len:]
                    break
                if idx > 0:
                    part = self._buffer[:idx]
                    emitted.append(part)
                    self.visible_chunks.append(part)
                self._buffer = self._buffer[idx + len("<think>"):]
                self._in_think = True
            else:
                idx = self._buffer.lower().find("</think>")
                if idx == -1:
                    partial = self._find_partial_prefix(self._buffer, "</think>")
                    safe_len = len(self._buffer) - partial
                    if safe_len > 0:
                        self.reasoning_chunks.append(self._buffer[:safe_len])
                        self._buffer = self._buffer[safe_len:]
                    break
                if idx > 0:
                    self.reasoning_chunks.append(self._buffer[:idx])
                self._buffer = self._buffer[idx + len("</think>"):]
                self._in_think = False
        return "".join(emitted)

    def flush(self) -> str:
        """Flush remaining buffer at stream end (fail-closed if still inside unclosed <think>)."""
        if not self._buffer:
            return ""
        if self._in_think:
            self.reasoning_chunks.append(self._buffer)
            self._buffer = ""
            return ""
        part = self._buffer
        self.visible_chunks.append(part)
        self._buffer = ""
        return part

    @staticmethod
    def _find_partial_prefix(text: str, tag: str) -> int:
        lower = text.lower()
        for k in range(min(len(tag) - 1, len(lower)), 0, -1):
            if lower.endswith(tag[:k]):
                return k
        return 0


@dataclass(frozen=True)
class VerificationResult:
    verdict: str
    verifier_id: str
    evidence_ref: str | None
    checked: tuple[dict[str, Any], ...]
    failures: tuple[str, ...]


class IndependentVerifier:
    """Pure verifier. It never asks a model whether the task is complete."""

    verifier_id = "scp-independent-postcondition-verifier-v1"

    def verify(self, postcondition: dict[str, Any] | None, observation: dict[str, Any] | None) -> VerificationResult:
        from scp.core.postcondition_schema import SchemaValidationError, validate_postcondition_dict
        try:
            validate_postcondition_dict(postcondition)
        except SchemaValidationError as e:
            return VerificationResult("INSUFFICIENT", self.verifier_id, None, tuple(), (f"schema_error:{e}",))

        postcondition = postcondition or {}
        observation = observation or {}
        evidence_ref = observation.get("evidence_ref")
        failures: list[str] = []
        checked: list[dict[str, Any]] = []
        if postcondition.get("evidence_required") and not evidence_ref:
            return VerificationResult("INSUFFICIENT", self.verifier_id, None, tuple(), ("missing_evidence_ref",))
        conditions = postcondition.get("all") or []
        if not isinstance(conditions, list) or not conditions:
            return VerificationResult("INSUFFICIENT", self.verifier_id, evidence_ref, tuple(), ("missing_postconditions",))
        for index, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                failures.append(f"condition_{index}_not_object")
                continue
            kind = condition.get("kind")
            if kind == "url_matches":
                expected = condition.get("value")
                actual = observation.get("url")
                ok = bool(actual and expected and actual == expected)
            elif kind == "text_contains":
                expected = condition.get("value")
                raw_text = observation.get("text") or ""
                actual = strip_reasoning_tags(raw_text)
                ok = bool(expected and expected in actual)
            elif kind == "network_response":
                expected_method = condition.get("method")
                expected_status = condition.get("status")
                responses = observation.get("network_responses") or []
                ok = any(r.get("method") == expected_method and r.get("status") == expected_status for r in responses if isinstance(r, dict))
            elif kind == "artifact_hash":
                ok = observation.get("artifact_hash") == condition.get("value")
            else:
                failures.append(f"condition_{index}_unknown_kind")
                continue
            checked.append({"index": index, "kind": kind, "ok": ok})
            if not ok:
                failures.append(f"condition_{index}_failed")
        if any("unknown_kind" in item for item in failures):
            verdict = "UNKNOWN"
        elif failures:
            verdict = "CONTRADICTED"
        else:
            verdict = "VERIFIED"
        return VerificationResult(verdict, self.verifier_id, evidence_ref, tuple(checked), tuple(failures))

    @staticmethod
    def to_dict(result: VerificationResult) -> dict[str, Any]:
        return asdict(result)

