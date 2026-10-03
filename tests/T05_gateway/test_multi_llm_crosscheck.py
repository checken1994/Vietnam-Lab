from __future__ import annotations

import asyncio


class FakeProvider:
    def __init__(self, name: str, answer: str):
        self.PROVIDER_NAME = name
        self.answer = answer
        self.calls = 0
        self.enabled = True

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return self.answer, f"{self.PROVIDER_NAME}:local-model"


class FakeGateway:
    def __init__(self, providers):
        self.providers = list(providers)

    def provider_candidates(self, task: str):
        assert task == "judge"
        return list(self.providers)


def _run(gateway):
    from scp.runtime.multi_llm_crosscheck import cross_verify

    return asyncio.run(
        cross_verify(
            "Is the supplied answer supported?",
            "Yes.",
            context="The supplied evidence says yes.",
            gateway=gateway,
        )
    )


def test_cross_verify_selects_two_distinct_provider_families_before_calling():
    first = FakeProvider("family_a", "PASS")
    duplicate_family = FakeProvider("family_a", "PASS")
    second = FakeProvider("family_b", "PASS")

    result = _run(FakeGateway([first, duplicate_family, second]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert result["primary"]["provider"].startswith("family_a:")
    assert result["secondary"]["provider"].startswith("family_b:")
    assert first.calls == 1
    assert duplicate_family.calls == 0
    assert second.calls == 1


def test_cross_verify_distinct_provider_disagreement_fails_closed():
    first = FakeProvider("family_a", "PASS")
    second = FakeProvider("family_b", "FAIL")

    result = _run(FakeGateway([first, second]))

    assert result["consensus"] == "disagree"
    assert result["final"] is None


def test_cross_verify_one_provider_family_is_unavailable_not_consensus():
    first = FakeProvider("family_a", "PASS")
    duplicate_family = FakeProvider("family_a", "PASS")

    result = _run(FakeGateway([first, duplicate_family]))

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    assert first.calls == 1
    assert duplicate_family.calls == 0


class DeadProvider(FakeProvider):
    """[S22] Provider enabled nhưng model chết: chat trả (None, label).

    Production shape (bench 2026-09-13): groq 404 model_not_found → chat trả
    (None, "none"); gemini 404 model retired; cerebras/sambanova 402; nvidia
    429. Family được attempt nhưng không đóng góp verdict — vòng lặp PHẢI tiếp
    tục sang family sống kế tiếp thay vì dừng/đếm nhầm.
    """

    def __init__(self, name: str, label: str = "none"):
        super().__init__(name, "")
        self._label = label

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return None, self._label


def test_cross_verify_dead_family_does_not_block_later_live_families():
    dead = DeadProvider("family_dead", "none")
    live_a = FakeProvider("family_live_a", "PASS")
    live_b = FakeProvider("family_live_b", "PASS")
    never_needed = FakeProvider("family_never_needed", "PASS")

    result = _run(FakeGateway([dead, live_a, live_b, never_needed]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert dead.calls == 1
    assert live_a.calls == 1
    assert live_b.calls == 1  # gia nhập consensus → vòng lặp dừng ở đây
    assert never_needed.calls == 0
    # Attempt record giữ nguyên family chết với verdict None — audit không bịa.
    assert [a["family"] for a in result["attempts"]] == [
        "family_dead",
        "family_live_a",
        "family_live_b",
    ]
    assert result["attempts"][0]["verdict"] is None
    assert result["attempts"][0]["provider"] == "none"


def test_cross_verify_erroring_family_does_not_block_later_live_families():
    erroring = DeadProvider("family_err", "error:HTTPStatusError")
    live_a = FakeProvider("family_live_a", "FAIL")
    live_b = FakeProvider("family_live_b", "FAIL")

    result = _run(FakeGateway([erroring, live_a, live_b]))

    assert result["consensus"] == "agree"
    assert result["final"] == "FAIL"
    assert result["attempts"][0]["provider"] == "error:HTTPStatusError"
    assert result["attempts"][0]["verdict"] is None


# ---------------------------------------------------------------------------
# [W1-c6 2026-10-02] cross_verify must have its OWN deadline. Before c6 the
# provider.chat calls were awaited directly with no hedge/deadline (:94-117):
# each chat retries 3x under a 60s httpx timeout, so two slow families could
# hold the judge pipeline (inside the /ask lease window) for minutes. Now the
# whole crosscheck runs under SCP_CROSSCHECK_MAX_SECONDS (default 15s); when
# the budget is gone the consensus fails closed — and the attempt audit still
# records what happened instead of inventing opinions.
# ---------------------------------------------------------------------------


class _SlowVerdictProvider(FakeProvider):
    """Provider chậm 'thật': trả verdict sau delay (mô phỏng free tier)."""

    def __init__(self, name: str, answer: str, delay: float):
        super().__init__(name, answer)
        self._delay = delay

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        await asyncio.sleep(self._delay)
        return await super().chat(question, context, system_prompt, prioritize_free)


def test_cross_verify_deadline_fails_closed_when_providers_too_slow(monkeypatch):
    """Old-fail/new-pass: hai family chỉ trả verdict sau 3.0s mỗi cái.
    OLD (không deadline): consensus 'agree' sau ~6s. NEW: budget 0.5s cạn →
    fail-closed (missing_distinct_providers), toàn bộ chạy < ~2.5s."""
    monkeypatch.setenv("SCP_CROSSCHECK_MAX_SECONDS", "0.5")
    slow_a = _SlowVerdictProvider("family_a", "PASS", delay=3.0)
    slow_b = _SlowVerdictProvider("family_b", "PASS", delay=3.0)

    import time as _time

    t0 = _time.monotonic()
    result = _run(FakeGateway([slow_a, slow_b]))
    elapsed = _time.monotonic() - t0

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    assert elapsed < 2.5, f"deadline must bound the crosscheck, took {elapsed:.2f}s"


def test_cross_verify_deadline_attempts_recorded_for_audit(monkeypatch):
    """Attempt audit phải giữ nguyên family bị deadline với record
    'timeout:crosscheck_deadline' + verdict None — không bịa consensus."""
    monkeypatch.setenv("SCP_CROSSCHECK_MAX_SECONDS", "0.4")
    slow_a = _SlowVerdictProvider("family_a", "PASS", delay=3.0)
    slow_b = _SlowVerdictProvider("family_b", "PASS", delay=3.0)

    result = _run(FakeGateway([slow_a, slow_b]))

    assert result["final"] is None
    assert [a["family"] for a in result["attempts"]] == ["family_a"]
    assert result["attempts"][0]["provider"] == "timeout:crosscheck_deadline"
    assert result["attempts"][0]["verdict"] is None


def test_cross_verify_within_deadline_still_reaches_consensus(monkeypatch):
    """Deadline không được phá crosscheck khỏe: provider nhanh (mặc định
    budget) vẫn đạt consensus agree như trước c6."""
    monkeypatch.delenv("SCP_CROSSCHECK_MAX_SECONDS", raising=False)
    fast_a = _SlowVerdictProvider("family_a", "PASS", delay=0.05)
    fast_b = _SlowVerdictProvider("family_b", "PASS", delay=0.05)

    result = _run(FakeGateway([fast_a, fast_b]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
