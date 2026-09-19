"""
SCP LLM Gateway — Unified LLM access layer.

Package-bound enforcement order:
  1. egress guard — destination/network authority; always on.
"""
from scp.llm_gateway import client as _client
from scp.llm_gateway.egress_policy import install_egress_guard

install_egress_guard(_client.OpenRouterProvider)

LLMGateway = _client.LLMGateway
get_gateway = _client.get_gateway
chat_sync = _client.chat_sync

__all__ = ["LLMGateway", "get_gateway", "chat_sync"]
