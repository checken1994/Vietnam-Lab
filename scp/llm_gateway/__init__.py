"""
SCP LLM Gateway — Unified LLM access layer.

Package-bound enforcement order:
  1. egress guard — destination/network authority; always on.
  2. Z2 zero-cost PEP — fresh exact-$0 proof immediately before driver;
     cost wall active iff the deployment opts in with
     SCP_LLM_COST_MODE=free_only (opt-in, not a compile-time mandate).
  3. Z3 free-only router — filters candidates before retry/failover;
     same opt-in policy as Z2.

The Z2/Z3 wrappers are always installed, but authorize_outbound short-circuits
to a passthrough while SCP_LLM_COST_MODE != free_only. When opted in, Z2 stays
authoritative even if Z3 or any caller chooses the wrong model.
"""
from scp.llm_gateway import client as _client
from scp.llm_gateway.egress_policy import install_egress_guard
from scp.llm_gateway.zero_cost_runtime import (
    install_free_only_provider_router,
    install_openai_compatible_provider_pep,
)

install_egress_guard(_client.OpenRouterProvider)
install_openai_compatible_provider_pep(_client.OpenRouterProvider)
install_free_only_provider_router(_client.OpenRouterProvider)

from scp.llm_gateway.discovery import (
    LocalEndpointScanner,
    ModelDiscoveryStore,
    ModelLifecycleScheduler,
    ModelLifecycleState,
    create_model_lifecycle_scheduler,
)
from scp.llm_gateway.prober import ContractProber

LLMGateway = _client.LLMGateway
get_gateway = _client.get_gateway
chat_sync = _client.chat_sync

__all__ = [
    "LLMGateway",
    "get_gateway",
    "chat_sync",
    "ContractProber",
    "ModelLifecycleState",
    "ModelDiscoveryStore",
    "LocalEndpointScanner",
    "ModelLifecycleScheduler",
    "create_model_lifecycle_scheduler",
]
