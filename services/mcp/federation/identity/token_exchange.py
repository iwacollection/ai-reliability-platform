"""
OIDC federation token exchange foundation.

Flow:
AKS ServiceAccount JWT -> Entra ID federation -> Managed Identity token
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenExchangeRequest:
    subject_token: str
    client_id: str
    tenant_id: str
    audience: str


class AzureTokenExchangeRuntime:
    def exchange(self, request: TokenExchangeRequest) -> dict:
        return {
            "success": True,
            "client_id": request.client_id,
            "tenant_id": request.tenant_id,
            "audience": request.audience,
            "exchange_state": "ready",
        }
