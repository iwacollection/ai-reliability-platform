"""MCP Federation Registry.

Maintains available MCP providers and enables capability discovery.
"""

from typing import Dict, List

from .models import MCPProvider


class MCPRegistry:
    """Runtime registry for MCP providers.

    The registry is intentionally independent from transport.
    Future implementations can back this with Redis, PostgreSQL,
    or a Kubernetes custom resource.
    """

    def __init__(self) -> None:
        self._providers: Dict[str, MCPProvider] = {}

    def register(self, provider: MCPProvider) -> None:
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def get_provider(self, provider_id: str) -> MCPProvider | None:
        return self._providers.get(provider_id)

    def discover(self, capability: str, environment: str | None = None) -> List[MCPProvider]:
        providers = [
            provider
            for provider in self._providers.values()
            if provider.supports(capability)
        ]

        if environment:
            providers = [
                provider
                for provider in providers
                if provider.environment == environment
            ]

        return providers
