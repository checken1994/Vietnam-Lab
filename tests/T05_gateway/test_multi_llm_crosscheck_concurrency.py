from __future__ import annotations

import asyncio

import scp.llm_gateway as llm_gateway
from scp.runtime.multi_llm_crosscheck import cross_verify


class _Breaker:
    def is_open(self) -> bool:
        return False


class _Provider:
    def __init__(self, family: str, verdict: str = "PASS") -> None:
        self.PROVIDER_NAME = family
        self.enabled = True
        self._breaker = _Breaker()
        self.verdict = verdict

    async def chat(self, _question: str, context: str = "", system_prompt: str = ""):
        await asyncio.sleep(0)
        return self.verdict, f"{self.PROVIDER_NAME}:model"


class _ConcurrentGateway:
    """Gateway whose unconstrained rotation is deliberately concurrency-sensitive."""

    def __init__(self, secondary_verdict: str = "PASS", distinct: bool = True) -> None:
        self.counter = 0
        self.openrouter = _Provider("openrouter", "PASS")
        self.other = _Provider("independent", secondary_verdict)
        self.distinct = distinct

    def _provider_chain(self, _task: str):
        return [self.openrouter, self.other] if self.distinct else [self.openrouter]

    async def chat(self, _question: str, context: str = "", system_prompt: str = "", task: str = "default"):
        # Yield before incrementing so concurrent callers interleave. The old
        # implementation used two calls through this global rotation and could
        # pair one verification with the same provider family twice.
        await asyncio.sleep(0)
        providers = self._provider_chain(task)
        provider = providers[self.counter % len(providers)]
        self.counter += 1
        return await provider.chat(_question, context=context, system_prompt=system_prompt)


def _family(label: str) -> str:
    return label.split(":", 1)[0]


def test_cross_verify_keeps_provider_independence_under_parallel_load(monkeypatch):
    gateway = _ConcurrentGateway()
    # Inject gateway directly (cross_verify accepts gateway kwarg) rather than
    # mocking the module-level get_gateway singleton (internal behavior).
    gateway = _ConcurrentGateway()

    async def run_many():
        return await asyncio.gather(
            *[
                cross_verify(
                    f"question-{index}",
                    "answer",
                    context="trusted context",
                    gateway=gateway,
                )
                for index in range(40)
            ]
        )

    results = asyncio.run(run_many())
    assert len(results) == 40
    for result in results:
        assert result["consensus"] == "agree"
        assert result["final"] == "PASS"
        assert _family(result["primary"]["provider"]) != _family(result["secondary"]["provider"])


def test_cross_verify_fails_closed_when_no_distinct_provider_exists(monkeypatch):
    gateway = _ConcurrentGateway(distinct=False)
    # Inject gateway directly (cross_verify accepts gateway kwarg).
    result = asyncio.run(cross_verify("q", "a", context="ctx", gateway=gateway))
    assert result["final"] is None
    assert result["consensus"] == "missing_distinct_providers"
    assert result["secondary"]["verdict"] is None


def test_cross_verify_escalates_distinct_provider_disagreement(monkeypatch):
    gateway = _ConcurrentGateway(secondary_verdict="FAIL")
    # Inject gateway directly (cross_verify accepts gateway kwarg).
    result = asyncio.run(cross_verify("q", "a", context="ctx", gateway=gateway))
    assert result["final"] is None
    assert result["consensus"] == "disagree"
    assert _family(result["primary"]["provider"]) != _family(result["secondary"]["provider"])
