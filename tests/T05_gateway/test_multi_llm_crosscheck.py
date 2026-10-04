from __future__ import annotations

import asyncio


class FakeProvider:
    """Provider stub mang đúng shape family mới [W3-e2]: family = (base_url,
    model), KHÔNG phải PROVIDER_NAME. Mọi node đều truyền base_url/model tường
    minh để pin hợp đồng thật (production: OpenRouterProvider/EnvCompatProvider
    luôn có cả hai attribute)."""

    def __init__(self, name: str, answer: str, base_url: str, model: str):
        self.PROVIDER_NAME = name
        self.base_url = base_url
        self.model = model
        self.answer = answer
        self.calls = 0
        self.enabled = True

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return self.answer, f"{self.PROVIDER_NAME}:{self.model}"


# [W3-e2] Shape production thật (GA.md B1b / W1 evidence): openai_compat và
# openrouter CÙNG trỏ https://openrouter.ai/api/v1 và CÙNG chạy
# nvidia/nemotron-3-super-120b-a12b:free — hai PROVIDER_NAME khác nhau nhưng
# CÙNG lineage.
_GROQISH_BASE_URL = "https://openrouter.ai/api/v1"
_NEMOTRON = "nvidia/nemotron-3-super-120b-a12b:free"
_NEMOTRON_FAMILY_KEY = f"{_GROQISH_BASE_URL}|{_NEMOTRON}"


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
    first = FakeProvider("family_a", "PASS", "https://a.example/v1", "model-a")
    duplicate_family = FakeProvider("family_a", "PASS", "https://a.example/v1", "model-a")
    second = FakeProvider("family_b", "PASS", "https://b.example/v1", "model-b")

    result = _run(FakeGateway([first, duplicate_family, second]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert result["primary"]["provider"].startswith("family_a:")
    assert result["secondary"]["provider"].startswith("family_b:")
    assert first.calls == 1
    assert duplicate_family.calls == 0
    assert second.calls == 1


def test_cross_verify_distinct_provider_disagreement_fails_closed():
    first = FakeProvider("family_a", "PASS", "https://a.example/v1", "model-a")
    second = FakeProvider("family_b", "FAIL", "https://b.example/v1", "model-b")

    result = _run(FakeGateway([first, second]))

    assert result["consensus"] == "disagree"
    assert result["final"] is None


def test_cross_verify_one_provider_family_is_unavailable_not_consensus():
    first = FakeProvider("family_a", "PASS", "https://a.example/v1", "model-a")
    duplicate_family = FakeProvider("family_a", "PASS", "https://a.example/v1", "model-a")

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

    def __init__(self, name: str, base_url: str, model: str, label: str = "none"):
        super().__init__(name, "", base_url, model)
        self._label = label

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        self.calls += 1
        return None, self._label


def test_cross_verify_dead_family_does_not_block_later_live_families():
    dead = DeadProvider("family_dead", "https://dead.example/v1", "model-dead", "none")
    live_a = FakeProvider("family_live_a", "PASS", "https://a.example/v1", "model-a")
    live_b = FakeProvider("family_live_b", "PASS", "https://b.example/v1", "model-b")
    never_needed = FakeProvider("family_never", "PASS", "https://c.example/v1", "model-c")

    result = _run(FakeGateway([dead, live_a, live_b, never_needed]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
    assert dead.calls == 1
    assert live_a.calls == 1
    assert live_b.calls == 1  # gia nhập consensus → vòng lặp dừng ở đây
    assert never_needed.calls == 0
    # Attempt record giữ nguyên family chết với verdict None — audit không bịa.
    # [W3-e2] family giờ là key (base_url|model), audit trail phải ghi đúng key đó.
    assert [a["family"] for a in result["attempts"]] == [
        "https://dead.example/v1|model-dead",
        "https://a.example/v1|model-a",
        "https://b.example/v1|model-b",
    ]
    assert result["attempts"][0]["verdict"] is None
    assert result["attempts"][0]["provider"] == "none"


def test_cross_verify_erroring_family_does_not_block_later_live_families():
    erroring = DeadProvider("family_err", "https://err.example/v1", "model-err", "error:HTTPStatusError")
    live_a = FakeProvider("family_live_a", "FAIL", "https://a.example/v1", "model-a")
    live_b = FakeProvider("family_live_b", "FAIL", "https://b.example/v1", "model-b")

    result = _run(FakeGateway([erroring, live_a, live_b]))

    assert result["consensus"] == "agree"
    assert result["final"] == "FAIL"
    assert result["attempts"][0]["provider"] == "error:HTTPStatusError"
    assert result["attempts"][0]["verdict"] is None


# ---------------------------------------------------------------------------
# [W3-e2 2026-10-04] root-3: family independence phải keyed theo
# (base_url, model) — KHÔNG phải PROVIDER_NAME.
#
# Thực tế runtime W1: openai_compat + openrouter là 2 PROVIDER_NAME nhưng
# CÙNG base_url (https://openrouter.ai/api/v1) và CÙNG model
# (nvidia/nemotron-3-super-120b-a12b:free) → 2 opinion cùng lineage (DNA #5
# bị vi phạm) → consensus 'agree' giả → q08 nondeterministic. Hợp đồng mới:
#   - Hai provider CÙNG (base_url, model) dù khác tên = 1 family →
#     missing_distinct_providers (fail-closed), không bao giờ 'agree' giả.
#   - Chỉ ≥2 family THẬT (khác (base_url, model)) mới được consensus.
#   - attempts ghi rõ family-key 'base_url|model' cho audit.
# ---------------------------------------------------------------------------


def test_cross_verify_same_base_url_and_model_is_one_family_regardless_of_name():
    """Old-fails/new-passes (root-3): groq + groq3 cùng chạy nemotron trên cùng
    base_url — OLD (keyed theo PROVIDER_NAME): consensus 'agree' từ 2 opinion
    cùng lineage. NEW: 1 family duy nhất → missing_distinct_providers."""
    groq = FakeProvider("openai_compat", "FAIL", _GROQISH_BASE_URL, _NEMOTRON)
    groq3 = FakeProvider("openrouter", "FAIL", _GROQISH_BASE_URL, _NEMOTRON)

    result = _run(FakeGateway([groq, groq3]))

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    # Family thứ hai không được attempt (không phải opinion độc lập).
    assert groq.calls == 1
    assert groq3.calls == 0
    assert result["distinct_families_attempted"] == [_NEMOTRON_FAMILY_KEY]


def test_cross_verify_distinct_models_same_base_url_still_agree():
    """Hai model THẬT khác nhau trên cùng base_url → 2 family độc lập →
    consensus 'agree' vẫn hoạt động (e2 không phá crosscheck khỏe)."""
    nemotron = FakeProvider("openai_compat", "PASS", _GROQISH_BASE_URL, _NEMOTRON)
    other = FakeProvider("openrouter", "PASS", _GROQISH_BASE_URL, "google/gemini-2.5-flash:free")

    result = _run(FakeGateway([nemotron, other]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"


def test_cross_verify_attempts_record_family_key_format():
    """Audit trail: attempts[].family và distinct_families_attempted phải ghi
    family-key dạng 'base_url|model' (normalized), không phải PROVIDER_NAME."""
    groq = FakeProvider("openai_compat", "PASS", _GROQISH_BASE_URL + "/", _NEMOTRON.upper())
    other = FakeProvider("openrouter", "PASS", "https://b.example/v1", "model-b")

    result = _run(FakeGateway([groq, other]))

    # base_url normalized (bỏ trailing '/') + model normalized (lowercase).
    assert result["attempts"][0]["family"] == _NEMOTRON_FAMILY_KEY
    assert result["distinct_families_attempted"] == [
        _NEMOTRON_FAMILY_KEY,
        "https://b.example/v1|model-b",
    ]


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

    def __init__(self, name: str, answer: str, base_url: str, model: str, delay: float):
        super().__init__(name, answer, base_url, model)
        self._delay = delay

    async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
        await asyncio.sleep(self._delay)
        return await super().chat(question, context, system_prompt, prioritize_free)


def test_cross_verify_deadline_fails_closed_when_providers_too_slow(monkeypatch):
    """Old-fail/new-pass: hai family chỉ trả verdict sau 3.0s mỗi cái.
    OLD (không deadline): consensus 'agree' sau ~6s. NEW: budget 0.5s cạn →
    fail-closed (missing_distinct_providers), toàn bộ chạy < ~2.5s."""
    monkeypatch.setenv("SCP_CROSSCHECK_MAX_SECONDS", "0.5")
    slow_a = _SlowVerdictProvider("family_a", "PASS", "https://a.example/v1", "model-a", delay=3.0)
    slow_b = _SlowVerdictProvider("family_b", "PASS", "https://b.example/v1", "model-b", delay=3.0)

    import time as _time

    t0 = _time.monotonic()
    result = _run(FakeGateway([slow_a, slow_b]))
    elapsed = _time.monotonic() - t0

    assert result["consensus"] == "missing_distinct_providers"
    assert result["final"] is None
    assert elapsed < 2.5, f"deadline must bound the crosscheck, took {elapsed:.2f}s"


def test_cross_verify_deadline_attempts_recorded_for_audit(monkeypatch):
    """[W1-c9] Attempt audit TẤT ĐỊNH bằng giả-clock (không phụ thuộc
    wall-clock/scheduler của OS).

    Semantic được pin (chọn rõ, không đoán theo kết quả):
      - Attempt CHỈ được launch khi launch-gate thấy budget còn (deadline chưa
        cạn — bởi đồng hồ lẫn by-construction sau một TimeoutError).
      - Attempt được launch rồi dính deadline → record
        'timeout:crosscheck_deadline' (verdict None) cho audit — không bịa
        consensus.
      - Attempt nằm SAU khi budget cạn → KHÔNG được launch → không có record.

    Kịch bản: family_a được launch trong budget, đốt trọn budget (giả-clock
    tiến qua deadline) rồi hết giờ; family_b phải bị launch-gate chặn.

    Lịch sử: trước W1-c9 test dùng real sleep (budget 0.4s vs delay 3.0s) và
    lệ thuộc biên "deadline vừa cạn" — trên Windows, wait_for của family_a bắn
    trước deadline tuyệt đối một tick (coarse timer ~15.6ms / scheduler), gate
    đọc được `remaining = +ε` → family_b vẫn được launch rồi lập tức hết giờ →
    attempts ['family_a', 'family_b'] → flake (CI run 37202332328). Giờ clock
    giả điều khiển tuyệt đối: nếu ai bỏ launch-gate (check deadline trước
    launch), family_b được launch với remaining âm → wait_for hết giờ ngay →
    record thừa trong attempts → test FAIL (anti-placebo, đã chứng minh bằng
    revert tạm dòng gate trong worktree).
    """
    import scp.runtime.multi_llm_crosscheck as crosscheck_module

    monkeypatch.setenv("SCP_CROSSCHECK_MAX_SECONDS", "15")

    class _FakeClock:
        """Đồng hồ monotonic giả: chỉ tiến khi một attempt 'làm việc'."""

        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = _FakeClock()
    monkeypatch.setattr(crosscheck_module, "_now", clock)

    class _DeadlineBurningProvider(FakeProvider):
        """Chat đốt `burn_seconds` budget (giả-clock) rồi trả TimeoutError —

        hình dạng của một attempt chờ đúng đến hạn crosscheck (production:
        wait_for bắn tại deadline; gateway chat không để lộ builtin
        TimeoutError riêng)."""

        def __init__(self, name: str, base_url: str, model: str, burn_seconds: float):
            super().__init__(name, "PASS", base_url, model)
            self._burn_seconds = burn_seconds

        async def chat(self, question: str, context: str = "", system_prompt: str = "", prioritize_free: bool = False):
            self.calls += 1
            clock.now += self._burn_seconds
            raise asyncio.TimeoutError

    # family_a: launch ở t=0, budget 15s → đốt 15.001s (deadline cạn) → timeout.
    slow_a = _DeadlineBurningProvider("family_a", "https://a.example/v1", "model-a", burn_seconds=15.001)
    # family_b: nếu bị launch nhầm (gate bị bỏ), nó đốt tiếp rồi hết giờ →
    # record thừa trong attempts → test fail.
    slow_b = _DeadlineBurningProvider("family_b", "https://b.example/v1", "model-b", burn_seconds=0.1)

    result = _run(FakeGateway([slow_a, slow_b]))

    assert slow_a.calls == 1, "family_a phải được launch đúng 1 lần trong budget"
    assert slow_b.calls == 0, "family_b KHÔNG được launch sau khi deadline cạn"
    assert result["final"] is None
    assert result["consensus"] == "missing_distinct_providers"
    assert [a["family"] for a in result["attempts"]] == ["https://a.example/v1|model-a"]
    assert result["attempts"][0]["provider"] == "timeout:crosscheck_deadline"
    assert result["attempts"][0]["verdict"] is None


def test_cross_verify_within_deadline_still_reaches_consensus(monkeypatch):
    """Deadline không được phá crosscheck khỏe: provider nhanh (mặc định
    budget) vẫn đạt consensus agree như trước c6."""
    monkeypatch.delenv("SCP_CROSSCHECK_MAX_SECONDS", raising=False)
    fast_a = _SlowVerdictProvider("family_a", "PASS", "https://a.example/v1", "model-a", delay=0.05)
    fast_b = _SlowVerdictProvider("family_b", "PASS", "https://b.example/v1", "model-b", delay=0.05)

    result = _run(FakeGateway([fast_a, fast_b]))

    assert result["consensus"] == "agree"
    assert result["final"] == "PASS"
