"""Tenant isolation primitives for enterprise deployments."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    namespace: str
    allowed_clusters: tuple[str, ...]


class TenantIsolation:
    def validate_cluster(self, context: TenantContext, cluster: str) -> bool:
        return cluster in context.allowed_clusters
