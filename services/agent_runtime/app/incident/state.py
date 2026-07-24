from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


class IncidentState(BaseModel):
    """
    Runtime incident state.
    """

    id: UUID = Field(
        default_factory=uuid4
    )

    status: IncidentStatus = (
        IncidentStatus.NEW
    )

    created_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )

    updated_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )

    reason: str | None = None


    def update(
        self,
        status: IncidentStatus,
        reason: str | None = None,
    ):

        self.status = status

        self.reason = reason

        self.updated_at = (
            datetime.now(UTC)
        )