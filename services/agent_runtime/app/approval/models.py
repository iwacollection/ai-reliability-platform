from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.action.models import (
    ActionPlan,
)


class ApprovalStatus(str, Enum):
    """Approval lifecycle status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDecision(BaseModel):
    """
    Persistent audit data for one human approval decision.

    The idempotency key is scoped to the ApprovalRequest. ApprovalManager will
    use it to distinguish a safe retry from a second, conflicting decision.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    status: ApprovalStatus

    operator_id: str = Field(
        min_length=1,
        max_length=128,
    )

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
    )

    reason: str = Field(
        default="",
        max_length=2000,
    )

    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    @field_validator(
        "operator_id",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def normalize_required_identity(
        cls,
        value: Any,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "Approval decision identity must be text"
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Approval decision identity cannot be empty"
            )

        return normalized

    @model_validator(
        mode="after"
    )
    def validate_terminal_status(
        self,
    ):
        if self.status not in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
        }:
            raise ValueError(
                "ApprovalDecision status must be "
                "approved or rejected"
            )

        return self


class ApprovalRequest(BaseModel):
    """
    Human approval request.

    incident_id links an asynchronous request to the Incident that produced
    the remediation action. decision is optional for compatibility with old
    SQLite records and remains None until ApprovalManager performs a terminal
    compare-and-set transition.
    """

    model_config = ConfigDict(
        validate_assignment=True
    )

    id: str

    incident_id: UUID | None = None

    action: ActionPlan

    reason: str = ""

    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        validate_default=True,
    )

    decision: ApprovalDecision | None = None

    requester: str = "ai_agent"

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )
