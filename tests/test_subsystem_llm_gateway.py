import os
os.environ.setdefault("SCP_API_PROFILE", "full")
os.environ.setdefault("SCP_CAPABILITY_SECRET", "dummy-secret-for-tests-123")
os.environ.setdefault("SCP_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("SCP_TOP_SYSTEMS_EGRESS", "0")

from scp.llm_gateway.egress_policy import llm_egress_allowed, llm_egress_allowlist_hosts


def test_subsystem_llm_gateway_importable():
    """LLM Gateway: verify outbound egress policy enforcement and destination filtering."""
    # Standard authorized provider endpoints must be accepted
    assert llm_egress_allowed("https://api.openai.com/v1/chat/completions") is True
    assert llm_egress_allowed("https://openrouter.ai/api/v1/chat/completions") is True
    
    # Arbitrary unauthorized endpoints must be blocked fail-closed
    assert llm_egress_allowed("http://unauthorized-leak-target.com/api") is False
    assert llm_egress_allowed("http://169.254.169.254/latest/meta-data") is False
