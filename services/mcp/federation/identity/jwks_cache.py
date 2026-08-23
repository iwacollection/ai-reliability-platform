"""
JWKS cache for Azure Entra ID signing keys.

Avoids repeated key discovery calls and provides a controlled refresh point.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class JWKSCacheEntry:
    key_id: str
    public_key: str
    refreshed_at: datetime


class JWKSCache:
    def __init__(self):
        self._keys: dict[str, JWKSCacheEntry] = {}

    def put(self, key_id: str, public_key: str):
        self._keys[key_id] = JWKSCacheEntry(
            key_id=key_id,
            public_key=public_key,
            refreshed_at=datetime.now(timezone.utc),
        )

    def get(self, key_id: str | None):
        if not key_id:
            return None
        entry = self._keys.get(key_id)
        return entry.public_key if entry else None
