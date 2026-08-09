from datetime import UTC, datetime
from enum import Enum
from re import fullmatch
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightArtifact,
    KubernetesPreflightRequest,
)
from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
)


class PreflightArtifactStatus(str, Enum):
    """Durable preparation lifecycle before human approval."""

    PREPARED = "prepared"
    APPROVAL_BOUND = "approval_bound"


_IDEMPOTENCY_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"


def _aware_utc(value: Any, *, label: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"{label} must be an ISO-8601 datetime"
            ) from exc

    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _idempotency_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or fullmatch(_IDEMPOTENCY_PATTERN, value) is None
    ):
        raise ValueError("Preflight idempotency key is invalid")
    return value


class PreflightArtifactRecord(BaseModel):
    """
    Immutable persisted trusted preflight artifact and Approval binding.

    artifact content never changes. The only permitted store transition returns
    a new record that adds one deterministic Approval ID.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    artifact_id: UUID
    incident_id: UUID
    idempotency_key: str
    status: PreflightArtifactStatus = PreflightArtifactStatus.PREPARED
    artifact: KubernetesPreflightArtifact
    approval_id: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def validate_idempotency_key(cls, value: Any) -> str:
        return _idempotency_key(value)

    @field_validator("approval_id", mode="before")
    @classmethod
    def validate_approval_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
        ):
            raise ValueError("Preflight Approval ID is invalid")
        return value

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def validate_time(cls, value: Any, info) -> datetime:
        return _aware_utc(value, label=info.field_name)

    @model_validator(mode="after")
    def validate_binding(self) -> "PreflightArtifactRecord":
        contract = self.artifact.contract
        if self.artifact_id != contract.contract_id:
            raise ValueError("Artifact ID does not match the safety contract")
        if self.incident_id != contract.incident_id:
            raise ValueError("Artifact Incident does not match the safety contract")
        if self.artifact.plan.approved:
            raise ValueError("Persisted preflight Action must be unapproved")
        if self.artifact.plan.metadata.get("safety_contract_id") != str(
            self.artifact_id
        ):
            raise ValueError("Preflight Action is not bound to the artifact")
        if self.updated_at < self.created_at:
            raise ValueError("Preflight Artifact update time is invalid")

        if self.status == PreflightArtifactStatus.PREPARED:
            if self.approval_id is not None:
                raise ValueError("Prepared Artifact cannot have an Approval ID")
        elif self.status == PreflightArtifactStatus.APPROVAL_BOUND:
            if self.approval_id is None:
                raise ValueError("Approval-bound Artifact requires an Approval ID")
        else:
            raise ValueError("Preflight Artifact status is invalid")

        return self

    def bind_approval(
        self,
        approval_id: str,
        *,
        updated_at: datetime,
    ) -> "PreflightArtifactRecord":
        checked_at = _aware_utc(updated_at, label="updated_at")
        if self.status == PreflightArtifactStatus.APPROVAL_BOUND:
            if self.approval_id != approval_id:
                raise ValueError(
                    "Preflight Artifact is already bound to another Approval"
                )
            return self

        return PreflightArtifactRecord(
            artifact_id=self.artifact_id,
            incident_id=self.incident_id,
            idempotency_key=self.idempotency_key,
            status=PreflightArtifactStatus.APPROVAL_BOUND,
            artifact=self.artifact,
            approval_id=approval_id,
            created_at=self.created_at,
            updated_at=checked_at,
        )


class ProductionActionPreparationRequest(BaseModel):
    """Idempotent request to persist preflight and create one Approval."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    preflight: KubernetesPreflightRequest
    idempotency_key: str
    approval_reason: str = Field(
        default=(
            "Production remediation requires human approval after trusted "
            "Kubernetes server dry-run"
        ),
        min_length=1,
        max_length=2000,
    )

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def validate_idempotency_key(cls, value: Any) -> str:
        return _idempotency_key(value)

    @field_validator("approval_reason", mode="before")
    @classmethod
    def validate_reason(cls, value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
        ):
            raise ValueError("Production approval reason is invalid")
        return value


class ProductionActionPreparationResult(BaseModel):
    """Durable result returned for both first execution and exact replay."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    record: PreflightArtifactRecord
    approval: ApprovalRequest
    artifact_created: bool
    approval_created: bool

    @model_validator(mode="after")
    def validate_result_binding(self) -> "ProductionActionPreparationResult":
        if self.record.status != PreflightArtifactStatus.APPROVAL_BOUND:
            raise ValueError("Preparation result requires Approval-bound Artifact")
        if self.record.approval_id != self.approval.id:
            raise ValueError("Preparation result Approval ID does not match Artifact")
        if self.approval.incident_id != self.record.incident_id:
            raise ValueError("Preparation result Incident binding is invalid")
        return self

    @property
    def idempotent_replay(self) -> bool:
        return not self.artifact_created and not self.approval_created


__all__ = [
    "PreflightArtifactRecord",
    "PreflightArtifactStatus",
    "ProductionActionPreparationRequest",
    "ProductionActionPreparationResult",
]
