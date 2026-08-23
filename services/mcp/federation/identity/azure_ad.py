"""
Azure AD (Microsoft Entra ID) Federation integration foundation.

Provides identity validation primitives used by MCP Federation Gateway.
Production adapters can bind this layer to Azure SDK/MSAL/OIDC validation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class AzureADIdentity:
    """Validated workload/user identity from Azure AD."""

    subject: str
    tenant_id: str
    client_id: str
    roles: tuple[str, ...] = ()
    workload_identity: str | None = None


@dataclass(frozen=True)
class TokenValidationResult:
    valid: bool
    subject: str | None
    tenant_id: str | None
    reason: str
    validated_at: datetime


class AzureADFederationValidator:
    """Validate Azure federated identity before MCP execution."""

    def __init__(self, trusted_tenants: Iterable[str]):
        self.trusted_tenants = set(trusted_tenants)

    def validate(
        self,
        claims: dict,
    ) -> TokenValidationResult:
        tenant_id = claims.get("tid")
        subject = claims.get("sub")

        if not tenant_id:
            return TokenValidationResult(
                False,
                subject,
                None,
                "missing tenant id",
                datetime.now(timezone.utc),
            )

        if tenant_id not in self.trusted_tenants:
            return TokenValidationResult(
                False,
                subject,
                tenant_id,
                "untrusted tenant",
                datetime.now(timezone.utc),
            )

        return TokenValidationResult(
            True,
            subject,
            tenant_id,
            "identity validated",
            datetime.now(timezone.utc),
        )
