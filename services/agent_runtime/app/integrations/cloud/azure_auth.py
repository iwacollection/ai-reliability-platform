"""
Azure AD authentication foundation.

Production direction:
- Managed Identity
- Workload Identity Federation
- Azure SDK credential chain
"""

from dataclasses import dataclass


@dataclass
class AzureCredentialContext:
    tenant_id: str | None = None
    subscription_id: str | None = None
    credential_type: str = "default"


class AzureAuthenticator:
    def __init__(self, context: AzureCredentialContext):
        self.context = context

    def get_credential(self):
        # Runtime implementation will use azure.identity
        # DefaultAzureCredential / WorkloadIdentityCredential
        return {
            "credential_type": self.context.credential_type,
            "tenant_id": self.context.tenant_id,
            "subscription_id": self.context.subscription_id,
        }
