from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.agent_runtime.app.action.fingerprint import action_fingerprint
from services.agent_runtime.app.action.models import ActionPlan


class ApprovalStatus(str, Enum):
    """Approval lifecycle status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDecision(BaseModel):
    """Persistent audit data for one human approval decision."""

    model_config = ConfigDict(frozen=True)

    status: ApprovalStatus
    operator_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2000)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("operator_id", "idempotency_key", mode="before")
    @classmethod
    def normalize_required_identity(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Approval decision identity cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_terminal_status(self):
        if self.status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("ApprovalDecision status must be approved or rejected")
        return self


class ApprovalRequest(BaseModel):
    """Human approval request bound to the exact ActionPlan fingerprint."""

    model_config = ConfigDict(validate_assignment=True)

    id: str
    incident_id: UUID | None = None
    action: ActionPlan
    action_fingerprint: str | None = None
    reason: str = ""
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, validate_default=True)
    decision: ApprovalDecision | None = None
    requester: str = "ai_agent"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_action_fingerprint(self):
        expected = action_fingerprint(self.action)
        if self.action_fingerprint is None:
            self.action_fingerprint = expected
        elif self.action_fingerprint != expected:
            raise ValueError("Approval action fingerprint does not match ActionPlan")
        return self
