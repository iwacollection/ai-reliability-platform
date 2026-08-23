"""Credential isolation enforcement for MCP Federation."""

from dataclasses import dataclass

from .secret_models import CredentialLease, SecretRequest
from .secret_provider import SecretProvider


@dataclass(frozen=True)
class CredentialDecision:
    allowed: bool
    reason: str
    lease: CredentialLease | None = None


class CredentialIsolationEngine:
    def __init__(self, provider: SecretProvider | None = None):
        self.provider = provider or SecretProvider()

    def request_credential(self, request: SecretRequest) -> CredentialDecision:
        if not request.tenant_id:
            return CredentialDecision(False, "tenant context required")

        lease = self.provider.issue_short_lived_credential(request)
        return CredentialDecision(
            True,
            "short lived credential issued",
            lease,
        )
