import json

from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from re import fullmatch
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT = (
    "I_CONFIRM_OOM_PILOT_CANARY_ACTIVATION_CHECKLIST_V1"
)

_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_SHA256_PATTERN = r"[0-9a-f]{64}"


class ProductionPilotCeremonyStatus(str, Enum):
    """Durable lifecycle of one reviewed Pilot activation ceremony."""

    READY = "ready"
    ACTIVATED = "activated"


def _aware_utc(
    value: Any,
    *,
    label: str,
) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"{label} must be an ISO-8601 datetime"
            ) from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{label} must be timezone-aware"
        )
    return value.astimezone(UTC)


class ProductionPilotActivationChecklist(BaseModel):
    """Explicit human attestations required before a one-shot canary."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    executor_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    exact_target_verified: bool
    separate_credentials_verified: bool
    rollback_reviewed: bool
    monitoring_ready: bool
    kill_switch_tested: bool
    budget_available_verified: bool
    runbook_reviewed: bool
    acknowledgement: str = Field(
        min_length=1,
        max_length=128,
    )

    @field_validator(
        "executor_operator_id",
        mode="before",
    )
    @classmethod
    def validate_executor(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production Pilot executor identity is invalid"
            )
        return value

    @model_validator(mode="after")
    def require_complete_attestation(
        self,
    ) -> "ProductionPilotActivationChecklist":
        checks = (
            self.exact_target_verified,
            self.separate_credentials_verified,
            self.rollback_reviewed,
            self.monitoring_ready,
            self.kill_switch_tested,
            self.budget_available_verified,
            self.runbook_reviewed,
        )
        if not all(checks):
            raise ValueError(
                "Every Production Pilot activation check must be confirmed"
            )
        if (
            self.acknowledgement
            != PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Production Pilot activation acknowledgement is invalid"
            )
        return self


class ProductionPilotCeremonyRecord(BaseModel):
    """
    Immutable reviewer evidence for one bounded production Pilot activation.

    READY evidence is recorded while the Kill Switch is ENGAGED and before an
    Action Execution Claim exists. A later stage may atomically bind it to one
    execution by transitioning it to ACTIVATED.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    ceremony_id: UUID
    pilot_id: str = Field(
        min_length=1,
        max_length=128,
    )
    change_ticket: str = Field(
        min_length=1,
        max_length=128,
    )
    runbook_version: str = Field(
        min_length=1,
        max_length=128,
    )
    approval_id: str = Field(
        min_length=1,
        max_length=128,
    )
    incident_id: UUID
    artifact_id: UUID
    contract_id: UUID
    patch_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    reviewer_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    executor_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
    )
    checklist: ProductionPilotActivationChecklist
    kill_switch_state: Literal["engaged"] = "engaged"
    budget_state: Literal["available"] = "available"
    zero_write_verified: Literal[True] = True
    external_call_count: Literal[0] = 0
    evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    status: ProductionPilotCeremonyStatus = (
        ProductionPilotCeremonyStatus.READY
    )
    readiness_checked_at: datetime
    created_at: datetime
    expires_at: datetime
    execution_id: UUID | None = None
    execution_idempotency_key: str | None = Field(
        default=None,
        max_length=128,
    )
    activated_at: datetime | None = None
    activation_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )

    @field_validator(
        "pilot_id",
        "change_ticket",
        "runbook_version",
        "approval_id",
        "reviewer_operator_id",
        "executor_operator_id",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def validate_identifier(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production Pilot ceremony identifier is invalid"
            )
        return value

    @field_validator(
        "patch_sha256",
        "evidence_sha256",
        mode="before",
    )
    @classmethod
    def validate_digest(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or fullmatch(
                _SHA256_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production Pilot ceremony digest is invalid"
            )
        return value

    @field_validator(
        "execution_idempotency_key",
        mode="before",
    )
    @classmethod
    def validate_optional_identifier(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production Pilot execution idempotency key is invalid"
            )
        return value

    @field_validator(
        "readiness_checked_at",
        "created_at",
        "expires_at",
        "activated_at",
        mode="before",
    )
    @classmethod
    def validate_time(
        cls,
        value: Any,
        info,
    ) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(
            value,
            label=info.field_name,
        )

    @model_validator(mode="after")
    def validate_lifecycle(
        self,
    ) -> "ProductionPilotCeremonyRecord":
        if self.reviewer_operator_id == self.executor_operator_id:
            raise ValueError(
                "Production Pilot reviewer and executor must be different"
            )
        if self.checklist.executor_operator_id != self.executor_operator_id:
            raise ValueError(
                "Production Pilot checklist executor binding is invalid"
            )
        if self.expires_at <= self.created_at:
            raise ValueError(
                "Production Pilot ceremony expiry is invalid"
            )
        if self.readiness_checked_at > self.created_at:
            raise ValueError(
                "Production Pilot readiness time is invalid"
            )
        if self.evidence_sha256 != self.expected_evidence_sha256():
            raise ValueError(
                "Production Pilot ceremony evidence digest is invalid"
            )
        if self.status == ProductionPilotCeremonyStatus.READY:
            if any(
                value is not None
                for value in (
                    self.execution_id,
                    self.execution_idempotency_key,
                    self.activated_at,
                    self.activation_sha256,
                )
            ):
                raise ValueError(
                    "READY Production Pilot ceremony cannot be activated"
                )
            return self
        if any(
            value is None
            for value in (
                self.execution_id,
                self.execution_idempotency_key,
                self.activated_at,
                self.activation_sha256,
            )
        ):
            raise ValueError(
                "ACTIVATED Production Pilot ceremony requires execution binding"
            )
        assert self.execution_id is not None
        assert self.execution_idempotency_key is not None
        assert self.activated_at is not None
        assert self.activation_sha256 is not None
        if self.activated_at < self.created_at:
            raise ValueError(
                "Production Pilot activation time is invalid"
            )
        if self.activated_at >= self.expires_at:
            raise ValueError(
                "Production Pilot activation occurred after expiry"
            )
        if (
            self.activation_sha256
            != self.expected_activation_sha256()
        ):
            raise ValueError(
                "Production Pilot activation digest is invalid"
            )
        return self

    def expected_evidence_sha256(self) -> str:
        """Recompute the immutable reviewer-evidence digest."""

        evidence_values = {
            "ceremony_id": str(self.ceremony_id),
            "pilot_id": self.pilot_id,
            "change_ticket": self.change_ticket,
            "runbook_version": self.runbook_version,
            "approval_id": self.approval_id,
            "incident_id": str(self.incident_id),
            "artifact_id": str(self.artifact_id),
            "contract_id": str(self.contract_id),
            "patch_sha256": self.patch_sha256,
            "reviewer_operator_id": self.reviewer_operator_id,
            "executor_operator_id": self.executor_operator_id,
            "idempotency_key": self.idempotency_key,
            "checklist": self.checklist.model_dump(mode="json"),
            "readiness_checked_at": (
                self.readiness_checked_at.isoformat()
            ),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        return sha256(
            json.dumps(
                evidence_values,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

    def activate(
        self,
        *,
        execution_id: UUID | str,
        execution_idempotency_key: str,
        activated_at: datetime,
    ) -> "ProductionPilotCeremonyRecord":
        """Build one validated execution-bound ACTIVATED snapshot."""

        normalized_execution_id = UUID(
            str(execution_id)
        )
        normalized_time = _aware_utc(
            activated_at,
            label="activated_at",
        )
        key = self.validate_optional_identifier(
            execution_idempotency_key
        )
        assert key is not None

        if self.status == ProductionPilotCeremonyStatus.ACTIVATED:
            if (
                self.execution_id == normalized_execution_id
                and self.execution_idempotency_key == key
            ):
                return self
            raise ValueError(
                "Production Pilot ceremony is bound to another execution"
            )

        values = {
            **self.model_dump(),
            "status": ProductionPilotCeremonyStatus.ACTIVATED,
            "execution_id": normalized_execution_id,
            "execution_idempotency_key": key,
            "activated_at": normalized_time,
        }
        values["activation_sha256"] = (
            self._activation_digest(
                execution_id=normalized_execution_id,
                execution_idempotency_key=key,
                activated_at=normalized_time,
            )
        )
        return type(self).model_validate(
            values
        )

    def expected_activation_sha256(self) -> str:
        """Recompute the immutable execution-activation digest."""

        if (
            self.execution_id is None
            or self.execution_idempotency_key is None
            or self.activated_at is None
        ):
            raise ValueError(
                "Production Pilot ceremony is not activated"
            )
        return self._activation_digest(
            execution_id=self.execution_id,
            execution_idempotency_key=(
                self.execution_idempotency_key
            ),
            activated_at=self.activated_at,
        )

    def _activation_digest(
        self,
        *,
        execution_id: UUID,
        execution_idempotency_key: str,
        activated_at: datetime,
    ) -> str:
        values = {
            "ceremony_id": str(self.ceremony_id),
            "evidence_sha256": self.evidence_sha256,
            "pilot_id": self.pilot_id,
            "approval_id": self.approval_id,
            "incident_id": str(self.incident_id),
            "artifact_id": str(self.artifact_id),
            "contract_id": str(self.contract_id),
            "patch_sha256": self.patch_sha256,
            "executor_operator_id": self.executor_operator_id,
            "execution_id": str(execution_id),
            "execution_idempotency_key": (
                execution_idempotency_key
            ),
            "activated_at": activated_at.isoformat(),
        }
        return sha256(
            json.dumps(
                values,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()


__all__ = [
    "PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT",
    "ProductionPilotActivationChecklist",
    "ProductionPilotCeremonyRecord",
    "ProductionPilotCeremonyStatus",
]
