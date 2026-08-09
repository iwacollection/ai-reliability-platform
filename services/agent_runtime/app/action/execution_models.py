from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

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


class ActionExecutionStatus(str, Enum):
    """
    Durable action execution lifecycle.

    RUNNING means an execution claim has been persisted before calling the
    external executor. A RUNNING record found after process restart must not
    be launched again automatically because the original side effect may
    already have happened.

    INDETERMINATE freezes automatic execution until an operator or a dedicated
    reconciler determines the real external state.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class ActionExecutionReconciliationOutcome(
    str,
    Enum,
):
    """
    Operator-confirmed outcome of an INDETERMINATE execution.

    Reconciliation never grants permission to launch the Action again. It
    only records the externally observed outcome of the existing execution.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActionExecutionReconciliationDecision(
    BaseModel
):
    """
    Immutable audit record for one manual execution reconciliation.

    The idempotency key belongs to the reconciliation decision. It is separate
    from both the Approval decision key and the Action execution key.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    outcome: (
        ActionExecutionReconciliationOutcome
    )

    operator_id: str = Field(
        min_length=1,
        max_length=128,
    )

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
    )

    reason: str = Field(
        min_length=1,
        max_length=2000,
    )

    result: dict[str, Any]

    error_type: str | None = Field(
        default=None,
        max_length=256,
    )

    error_message: str | None = Field(
        default=None,
        max_length=4000,
    )

    reconciled_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
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
                "Reconciliation identity must be text"
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Reconciliation identity cannot be empty"
            )

        return normalized

    @field_validator(
        "reason",
        mode="before",
    )
    @classmethod
    def normalize_reason(
        cls,
        value: Any,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "Reconciliation reason must be text"
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Reconciliation reason cannot be empty"
            )

        return normalized

    @field_validator(
        "error_type",
        "error_message",
        mode="before",
    )
    @classmethod
    def normalize_optional_error(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "Reconciliation error detail must be text"
            )

        normalized = value.strip()

        return normalized or None

    @model_validator(
        mode="after"
    )
    def validate_explicit_outcome(
        self,
    ) -> "ActionExecutionReconciliationDecision":
        success = self.result.get(
            "success"
        )

        if (
            self.outcome
            == ActionExecutionReconciliationOutcome.SUCCEEDED
        ):
            if success is not True:
                raise ValueError(
                    "Successful reconciliation requires "
                    "result.success=true"
                )

            if (
                self.error_type is not None
                or self.error_message is not None
            ):
                raise ValueError(
                    "Successful reconciliation cannot "
                    "contain error details"
                )

            return self

        if success is not False:
            raise ValueError(
                "Failed reconciliation requires "
                "result.success=false"
            )

        return self


class ActionExecutionRecord(BaseModel):
    """
    Persistent claim and outcome for one approved remediation action.

    The execution store enforces one record per Approval and one record per
    execution idempotency key. An optional reconciliation decision records how
    an operator resolved an INDETERMINATE external outcome without launching
    the Action again.
    """

    model_config = ConfigDict(
        validate_assignment=True,
    )

    id: UUID = Field(
        default_factory=uuid4,
    )

    approval_id: str = Field(
        max_length=128,
    )

    incident_id: UUID | None = None

    operator_id: str = Field(
        max_length=128,
    )

    idempotency_key: str = Field(
        max_length=128,
    )

    action: ActionPlan

    status: ActionExecutionStatus = (
        ActionExecutionStatus.RUNNING
    )

    result: dict[str, Any] = Field(
        default_factory=dict,
    )

    error_type: str | None = None

    error_message: str | None = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    completed_at: datetime | None = None

    reconciliation: (
        ActionExecutionReconciliationDecision
        | None
    ) = None

    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )

    @field_validator(
        "approval_id",
        "operator_id",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def validate_required_identifier(
        cls,
        value: object,
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(
                "identifier must be a string"
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "identifier must not be empty"
            )

        return normalized

    @model_validator(mode="after")
    def validate_lifecycle(
        self,
    ) -> "ActionExecutionRecord":
        if self.status == ActionExecutionStatus.RUNNING:
            if self.completed_at is not None:
                raise ValueError(
                    "running execution cannot have completed_at"
                )

            if self.reconciliation is not None:
                raise ValueError(
                    "running execution cannot be reconciled"
                )

            return self

        if self.completed_at is None:
            raise ValueError(
                "closed execution must have completed_at"
            )

        if self.status == ActionExecutionStatus.SUCCEEDED:
            if self.result.get("success") is not True:
                raise ValueError(
                    "succeeded execution requires result.success=true"
                )

        if self.reconciliation is not None:
            expected_status = ActionExecutionStatus(
                self.reconciliation.outcome.value
            )

            if self.status != expected_status:
                raise ValueError(
                    "reconciliation outcome does not match "
                    "execution status"
                )

            if self.result != self.reconciliation.result:
                raise ValueError(
                    "reconciliation result does not match "
                    "execution result"
                )

            if (
                self.completed_at
                != self.reconciliation.reconciled_at
            ):
                raise ValueError(
                    "reconciliation timestamp does not match "
                    "execution completion timestamp"
                )

            if (
                expected_status
                == ActionExecutionStatus.SUCCEEDED
                and (
                    self.error_type is not None
                    or self.error_message is not None
                )
            ):
                raise ValueError(
                    "reconciled success cannot retain "
                    "execution error details"
                )

        return self

    @property
    def is_terminal(
        self,
    ) -> bool:
        return self.status in {
            ActionExecutionStatus.SUCCEEDED,
            ActionExecutionStatus.FAILED,
        }

    @property
    def requires_reconciliation(
        self,
    ) -> bool:
        return (
            self.status
            == ActionExecutionStatus.INDETERMINATE
        )

    @property
    def was_reconciled(
        self,
    ) -> bool:
        return self.reconciliation is not None

    @property
    def automatic_replay_allowed(
        self,
    ) -> bool:
        """
        Existing execution claims must never launch the action again.
        """

        return False

    def succeed(
        self,
        result: dict[str, Any],
    ) -> None:
        self._require_running()

        if result.get("success") is not True:
            raise ValueError(
                "successful execution requires result.success=true"
            )

        completed_at = datetime.now(UTC)

        self._apply_transition(
            status=ActionExecutionStatus.SUCCEEDED,
            result=dict(result),
            error_type=None,
            error_message=None,
            completed_at=completed_at,
            updated_at=completed_at,
        )

    def fail(
        self,
        result: dict[str, Any] | None = None,
        *,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._require_running()

        failure_result = dict(result or {})

        if failure_result.get("success") is True:
            raise ValueError(
                "failed execution cannot have result.success=true"
            )

        completed_at = datetime.now(UTC)

        self._apply_transition(
            status=ActionExecutionStatus.FAILED,
            result=failure_result,
            error_type=error_type,
            error_message=error_message,
            completed_at=completed_at,
            updated_at=completed_at,
        )

    def mark_indeterminate(
        self,
        reason: str,
    ) -> None:
        self._require_running()

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ValueError(
                "indeterminate execution requires a reason"
            )

        completed_at = datetime.now(UTC)

        self._apply_transition(
            status=ActionExecutionStatus.INDETERMINATE,
            error_type="IndeterminateExecution",
            error_message=normalized_reason,
            completed_at=completed_at,
            updated_at=completed_at,
        )

    def reconcile(
        self,
        decision: (
            ActionExecutionReconciliationDecision
        ),
    ) -> None:
        """
        Resolve an INDETERMINATE record without replaying the Action.

        Persistence and compare-and-set ownership remain the responsibility of
        ActionExecutionStore. This method only builds a valid domain snapshot.
        """

        if (
            self.status
            != ActionExecutionStatus.INDETERMINATE
        ):
            raise ValueError(
                "only an indeterminate action execution "
                "can be reconciled"
            )

        target_status = ActionExecutionStatus(
            decision.outcome.value
        )
        reconciled_success = (
            target_status
            == ActionExecutionStatus.SUCCEEDED
        )

        self._apply_transition(
            status=target_status,
            result=dict(
                decision.result
            ),
            error_type=(
                None
                if reconciled_success
                else (
                    decision.error_type
                    or "ReconciledExecutionFailure"
                )
            ),
            error_message=(
                None
                if reconciled_success
                else (
                    decision.error_message
                    or decision.reason
                )
            ),
            completed_at=(
                decision.reconciled_at
            ),
            updated_at=(
                decision.reconciled_at
            ),
            reconciliation=decision,
        )

    def _apply_transition(
        self,
        **changes: Any,
    ) -> None:
        """
        Validate a complete state snapshot before committing any field.

        This avoids exposing an invalid intermediate state while Pydantic
        assignment validation is enabled.
        """

        candidate_data = self.model_dump()
        candidate_data.update(changes)

        candidate = type(self).model_validate(
            candidate_data
        )

        for field_name in changes:
            object.__setattr__(
                self,
                field_name,
                getattr(candidate, field_name),
            )

    def _require_running(
        self,
    ) -> None:
        if self.status != ActionExecutionStatus.RUNNING:
            raise ValueError(
                "action execution is not running"
            )
