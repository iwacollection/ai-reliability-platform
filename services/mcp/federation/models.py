"""MCP Federation domain models.

Defines MCP providers and their exposed capabilities.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class MCPCapability:
    """A tool capability exposed by an MCP provider."""

    name: str
    description: str
    category: str


@dataclass
class MCPProvider:
    """A registered MCP endpoint.

    Example providers:
    - Azure AKS MCP connector
    - Kubernetes API MCP connector
    - Prometheus/Loki evidence connector
    """

    provider_id: str
    name: str
    environment: str
    provider_type: str
    capabilities: List[MCPCapability] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        return any(item.name == capability for item in self.capabilities)
