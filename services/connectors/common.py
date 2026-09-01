"""Shared primitives for real external-system connectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class ConnectorError(RuntimeError):
    """Base connector error."""


class AuthenticationError(ConnectorError):
    pass


class AuthorizationError(ConnectorError):
    pass


class RateLimitedError(ConnectorError):
    pass


class NotFoundError(ConnectorError):
    pass


class UpstreamError(ConnectorError):
    pass


@dataclass(frozen=True)
class Evidence:
    """Normalized, provenance-carrying external-system evidence."""

    source: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any]
    observed_at: datetime | None
    retrieved_at: datetime
    provenance: dict[str, Any]
