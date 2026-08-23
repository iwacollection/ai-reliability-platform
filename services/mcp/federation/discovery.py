"""Capability discovery service for MCP Federation."""

from .models import MCPProvider
from .registry import MCPRegistry


class CapabilityDiscovery:
    """Resolves agent requirements to available MCP providers."""

    def __init__(self, registry: MCPRegistry):
        self.registry = registry

    def find_tool_provider(
        self,
        capability: str,
        environment: str | None = None,
    ) -> MCPProvider | None:
        providers = self.registry.discover(
            capability=capability,
            environment=environment,
        )

        if not providers:
            return None

        # Initial strategy: deterministic selection.
        # Later phases can add health score, latency and confidence ranking.
        return providers[0]
