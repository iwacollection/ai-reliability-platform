"""
Azure Managed Identity abstraction for MCP federation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedIdentity:
    client_id: str
    tenant_id: str
    resource: str


class ManagedIdentityProvider:
    def get_token_context(self, identity: ManagedIdentity) -> dict:
        return {
            "client_id": identity.client_id,
            "tenant_id": identity.tenant_id,
            "resource": identity.resource,
            "token_exchange_ready": True,
        }
