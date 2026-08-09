from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IncidentTimelineEventType(str, Enum):
    """
    Incident lifecycle timeline events.
    """

    CREATED = "created"

    AGENT_STARTED = "agent_started"

    AGENT_COMPLETED = "agent_completed"

    RCA_COMPLETED = "rca_completed"

    ACTION_CREATED = "action_created"

    ACTION_APPROVAL_REQUIRED = (
        "action_approval_required"
    )

    ACTION_EXECUTED = "action_executed"

    VERIFICATION_STARTED = (
        "verification_started"
    )

    VERIFICATION_COMPLETED = (
        "verification_completed"
    )

    RESOLVED = "resolved"

    FAILED = "failed"



class IncidentTimelineEvent(BaseModel):
    """
    Immutable incident timeline record.

    Used for:

    - incident audit
    - AI decision trace
    - remediation history
    - postmortem analysis

    """

    id: UUID = Field(
        default_factory=uuid4
    )


    incident_id: UUID


    event_type: IncidentTimelineEventType


    source: str


    message: str = ""


    metadata: dict = Field(
        default_factory=dict
    )


    created_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )