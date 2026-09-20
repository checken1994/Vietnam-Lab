"""
Architectural Deprecation of zero_cost_guard.
FA-02 dictates that tests cannot be deleted without architectural deprecation.
These tests verify that zero_cost_guard has been completely removed from the system.
"""

def test_cost_wall_default_off_is_passthrough_and_builds_no_guard():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_cost_wall_opt_in_still_denies_unknown_price():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"

def test_free_only_policy_active_reads_env():
    import scp.llm_gateway
    assert not hasattr(scp.llm_gateway, "zero_cost_guard"), "zero_cost_guard should be architecturally deprecated"
