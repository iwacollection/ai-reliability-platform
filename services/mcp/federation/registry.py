"""MCP Federation Registry.

Maintains MCP providers and runtime cluster topology.
"""

from typing import Dict, List

from .models import ClusterRegistration, MCPProvider


class MCPRegistry:
    """Runtime registry for MCP providers."""

    def __init__(self) -> None:
        self._providers: Dict[str, MCPProvider] = {}
        self._clusters: Dict[str, ClusterRegistration] = {}

    def register(self, provider: MCPProvider) -> None:
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def register_cluster(self, cluster: ClusterRegistration) -> None:
        self._clusters[cluster.cluster_id] = cluster

    def get_cluster(self, cluster_id: str) -> ClusterRegistration | None:
        return self._clusters.get(cluster_id)

    def list_clusters(self) -> List[ClusterRegistration]:
        return list(self._clusters.values())

    def discover_clusters(self, environment: str | None = None) -> List[ClusterRegistration]:
        clusters = list(self._clusters.values())

        if environment:
            clusters = [
                cluster
                for cluster in clusters
                if cluster.environment == environment
            ]

        return clusters

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
