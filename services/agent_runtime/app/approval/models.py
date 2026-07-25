from datetime import UTC, datetime

from enum import Enum

from pydantic import BaseModel, Field


from services.agent_runtime.app.action.models import (
    ActionPlan,
)



class ApprovalStatus(str, Enum):
    """
    Approval lifecycle status.
    """


    PENDING = "pending"


    APPROVED = "approved"


    REJECTED = "rejected"


    EXPIRED = "expired"




class ApprovalRequest(BaseModel):
    """
    Human approval request.

    Created when an action requires
    human confirmation before execution.
    """


    id: str


    action: ActionPlan


    reason: str = ""


    status: ApprovalStatus = (
        ApprovalStatus.PENDING
    )


    requester: str = (
        "ai_agent"
    )


    created_at: datetime = Field(
        default_factory=lambda:
            datetime.now(UTC)
    )


    updated_at: datetime = Field(
        default_factory=lambda:
            datetime.now(UTC)
    )


    metadata: dict = Field(
        default_factory=dict
    )