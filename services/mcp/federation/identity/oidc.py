"""Azure OIDC federation validation helpers."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class OIDCValidationResult:
    valid: bool
    reason: str
    validated_at: datetime


class OIDCTokenValidator:
    """Foundation for OIDC/JWT validation.

    Production adapters can bind JWKS signature verification here.
    """

    def __init__(self, jwks_uri: str):
        self.jwks_uri = jwks_uri

    def validate_claims(self, claims: dict) -> OIDCValidationResult:
        now = datetime.now(timezone.utc)

        if not claims.get("iss"):
            return OIDCValidationResult(False, "missing issuer", now)

        if not claims.get("aud"):
            return OIDCValidationResult(False, "missing audience", now)

        return OIDCValidationResult(True, "oidc claims accepted", now)
