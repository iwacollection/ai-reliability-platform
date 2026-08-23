"""
AKS Workload Identity binding foundation.

Provides the abstraction layer between Kubernetes service accounts,
OIDC federation and Azure managed identity.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkloadIdentityContext:
    namespace: str
    service_account: str
    client_id: str
    tenant_id: str
    issuer: str


class AKSWorkloadIdentityBinder:
    def bind(self, context: WorkloadIdentityContext) -> dict:
        return {
            "bound": True,
            "identity": context.client_id,
            "tenant_id": context.tenant_id,
            "subject": f"system:serviceaccount:{context.namespace}:{context.service_account}",
        }
