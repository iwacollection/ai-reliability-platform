"""Secret provider abstraction for MCP Federation.

Agents never receive long-lived cloud credentials. Providers issue scoped
credentials or leases.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .secret_models import CredentialLease, SecretRequest


class SecretProvider:
    def issue_short_lived_credential(
        self, request: SecretRequest
    ) -> CredentialLease:
        return CredentialLease(
            credential_id=str(uuid4()),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            tenant_id=request.tenant_id,
            resource=request.resource,
        )
