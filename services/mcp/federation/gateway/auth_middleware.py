"""
MCP Federation Gateway Authentication Middleware

Provides request authentication boundary between callers and MCP Federation.
Flow:
Bearer Token -> Identity Verification -> Security Context -> Policy Enforcement
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AuthenticatedRequestContext:
    principal: str
    tenant_id: str
    roles: list[str]
    workload_identity: str | None = None
    claims: dict[str, Any] | None = None


class MCPAuthenticationMiddleware:
    def __init__(self, token_validator, permission_engine):
        self.token_validator = token_validator
        self.permission_engine = permission_engine

    def authenticate(self, token: str, capability: str) -> AuthenticatedRequestContext:
        result = self.token_validator.verify(token)

        if not result.valid:
            raise PermissionError(result.reason)

        context = AuthenticatedRequestContext(
            principal=result.subject,
            tenant_id=result.tenant_id,
            roles=result.roles,
            workload_identity=result.workload_identity,
            claims=result.claims,
        )

        decision = self.permission_engine.evaluate(
            principal=context.principal,
            roles=context.roles,
            capability=capability,
        )

        if not decision.allowed:
            raise PermissionError(decision.reason)

        return context
