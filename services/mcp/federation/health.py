"""Health aware MCP provider discovery.

Filters unhealthy MCP providers before routing tool calls.
"""

from datetime import datetime, timezone

from .models import MCPProvider


class MCPHealthChecker:
    def check(self, provider: MCPProvider) -> bool:
        metadata = provider.metadata or {}
        status = metadata.get("health_status", "healthy")
        return status == "healthy"

    def update_status(self, provider: MCPProvider, status: str) -> MCPProvider:
        provider.metadata["health_status"] = status
        provider.metadata["health_checked_at"] = datetime.now(timezone.utc).isoformat()
        return provider


class HealthAwareDiscovery:
    def __init__(self, registry, health_checker=None):
        self.registry = registry
        self.health_checker = health_checker or MCPHealthChecker()

    def discover(self, capability: str, environment: str | None = None):
        providers = self.registry.find_capability(capability, environment)
        return [p for p in providers if self.health_checker.check(p)]
