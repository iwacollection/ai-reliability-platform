"""
Header definition for Standard Event Specification (SES).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from common.domain.event.base import DomainModel
from common.domain.event.enums import EventSource


class Header(DomainModel):
    """
    Immutable metadata describing an event.

    Attributes
    ----------
    event_id
        Globally unique event identifier.

    trace_id
        Trace identifier shared across the processing pipeline.

    correlation_id
        Incident identifier generated after event correlation.

    schema_version
        Standard Event Specification version.

    source
        Event source.

    occurred_at
        Time when the event happened.

    received_at
        Time when Gateway received the event.
    """

    event_id: UUID = Field(default_factory=uuid4)

    trace_id: UUID = Field(default_factory=uuid4)

    correlation_id: UUID | None = None

    schema_version: str = "v1"

    source: EventSource

    occurred_at: datetime

    received_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )