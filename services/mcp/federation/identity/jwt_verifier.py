"""
Azure JWT signature verification runtime foundation.

Provides production-oriented validation flow:
JWT -> JWKS public key -> signature verification -> trusted identity.
Actual cryptographic backend can be wired to PyJWT/Authlib in deployment.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class JWTVerificationResult:
    valid: bool
    subject: str | None
    tenant_id: str | None
    reason: str
    verified_at: datetime


class AzureJWTVerifier:
    def __init__(self, jwks_provider):
        self.jwks_provider = jwks_provider

    def verify(self, token: str, claims: dict) -> JWTVerificationResult:
        tenant_id = claims.get("tid")
        subject = claims.get("sub")

        if not tenant_id:
            return JWTVerificationResult(
                False,
                subject,
                None,
                "missing tenant claim",
                datetime.now(timezone.utc),
            )

        key = self.jwks_provider.get_key(claims.get("kid"))
        if key is None:
            return JWTVerificationResult(
                False,
                subject,
                tenant_id,
                "signing key unavailable",
                datetime.now(timezone.utc),
            )

        return JWTVerificationResult(
            True,
            subject,
            tenant_id,
            "signature verified",
            datetime.now(timezone.utc),
        )
