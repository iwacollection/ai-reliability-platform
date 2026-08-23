"""MCP Federation domain models.

Defines MCP providers, cluster registration and environment topology.
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
    """A registered MCP endpoint."""

    provider_id: str
    name: str
    environment: str
    provider_type: str
    capabilities: List[MCPCapability] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        return any(item.name == capability for item in self.capabilities)


@dataclass
class ClusterRegistration:
    """Registered runtime cluster in federation topology."""

    cluster_id: str
    name: str
    provider: str
    environment: str
    region: str
    endpoint: str | None = None
    capabilities: List[str] = field(default_factory=list)
    healthy: bool = True


@dataclass
class EnvironmentTopology:
    """Environment to cluster mapping used by federation routing."""

    environment: str
    clusters: List[ClusterRegistration] = field(default_factory=list)
