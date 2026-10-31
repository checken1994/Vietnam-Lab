import os

os.environ.setdefault("SCP_API_PROFILE", "full")
os.environ.setdefault("SCP_CAPABILITY_SECRET", "dummy-secret-for-tests-123")
os.environ.setdefault("SCP_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("SCP_TOP_SYSTEMS_EGRESS", "0")

from scp.runtime.question_router import detect_language, route_question


def test_subsystem_runtime_importable():
    """Runtime judge & router: verify language detection and domain classification."""
    assert detect_language("Hà Nội là thủ đô của Việt Nam") == "vi"
    assert detect_language("Paris is the capital of France") == "en"
    
    # Test intent and domain classification
    # [L0-DEKEYWORD 2026-10-31] L0 không còn luật từ khoá ⇒ intent/độ tin của câu ngôn ngữ tự
    # nhiên do lineage L2 quyết (classifier chết ⇒ failsafe 0.5, fail-closed). Smoke test cấp
    # lineage L2 giả trả LOOKUP; mọi assert giữ nguyên.
    class _LookupLineageGW:
        def chat(self, *_a, **_k):
            return "LOOKUP", "stub:route"

    decision = route_question("Paris is the capital of France", gateway=_LookupLineageGW())
    assert decision.via == "l2-llm"
    assert decision.domain == "geography"
    assert decision.lane == "LANE_FACTUAL"
    assert decision.confidence > 0.5
