"""Azure JWKS provider foundation for JWT signature verification."""

from dataclasses import dataclass


@dataclass(frozen=True)
class JWKSKeySet:
    issuer: str
    keys: tuple[dict, ...]


class AzureJWKSProvider:
    def __init__(self, jwks_uri: str):
        self.jwks_uri = jwks_uri

    def load(self) -> JWKSKeySet:
        """Placeholder for remote JWKS retrieval and cache implementation."""
        return JWKSKeySet(
            issuer=self.jwks_uri,
            keys=(),
        )
