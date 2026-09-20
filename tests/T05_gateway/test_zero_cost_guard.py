"""
Architectural Deprecation of zero_cost_guard.
FA-02 dictates that tests cannot be deleted without architectural deprecation.
These tests verify that zero_cost_guard has been completely removed from the system.
"""

def test_paid_unknown_stale_and_data_class_never_reach_send_boundary():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_free_to_paid_catalog_transition_denies_next_request():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_free_only_config_cannot_enable_paid_or_unknown_price():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_pricing_proof_survives_restart():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_runtime_proof_store_override_is_test_scoped_and_data_scoped():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"
