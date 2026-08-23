"""
Azure AD (Microsoft Entra ID) Federation integration.

Provides trusted identity validation primitives for MCP Federation Gateway.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class AzureADIdentity:
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
    audience: str | None
    issuer: str | None
    reason: str
    validated_at: datetime


class AzureADFederationValidator:
    """Validate Azure Entra ID federation claims."""

    def __init__(
        self,
        trusted_tenants: Iterable[str],
        expected_audience: str | None = None,
        expected_issuer: str | None = None,
    ):
        self.trusted_tenants = set(trusted_tenants)
        self.expected_audience = expected_audience
        self.expected_issuer = expected_issuer

    def validate(self, claims: dict) -> TokenValidationResult:
        now = datetime.now(timezone.utc)

        tenant_id = claims.get("tid")
        subject = claims.get("sub")
        audience = claims.get("aud")
        issuer = claims.get("iss")

        if not tenant_id:
            return TokenValidationResult(False, subject, None, audience, issuer, "missing tenant id", now)

        if tenant_id not in self.trusted_tenants:
            return TokenValidationResult(False, subject, tenant_id, audience, issuer, "untrusted tenant", now)

        if self.expected_audience and audience != self.expected_audience:
            return TokenValidationResult(False, subject, tenant_id, audience, issuer, "invalid audience", now)

        if self.expected_issuer and issuer != self.expected_issuer:
            return TokenValidationResult(False, subject, tenant_id, audience, issuer, "invalid issuer", now)

        return TokenValidationResult(True, subject, tenant_id, audience, issuer, "identity validated", now)
