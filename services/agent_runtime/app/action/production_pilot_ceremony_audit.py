from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    ProductionPilotCeremonyRecord,
    ProductionPilotCeremonyStatus,
)


class ProductionPilotCeremonyRecoveryState(str, Enum):
    """Bounded operator recovery state derived from durable records."""

    READY_FOR_FIRST_RESUME = "ready_for_first_resume"
    EXPIRED_BEFORE_EXECUTION = "expired_before_execution"
    CLAIM_NOT_ACTIVATED = "claim_not_activated"
    ACTIVATED_OUTCOME_UNCONFIRMED = "activated_outcome_unconfirmed"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_INDETERMINATE = "execution_indeterminate"
    INCONSISTENT = "inconsistent"


class ProductionPilotCeremonyOperatorGuidance(str, Enum):
    """Allowlisted operator actions safe to expose through the API."""

    KEEP_KILL_SWITCH_ENGAGED = (
        "keep_kill_switch_engaged_until_runbook_complete"
    )
    USE_ONE_AUTHENTICATED_RESUME = "use_one_authenticated_resume"
    ENGAGE_KILL_SWITCH = "engage_kill_switch"
    DO_NOT_RESUME_EXPIRED_CEREMONY = "do_not_resume_expired_ceremony"
    CREATE_NEW_PREPARATION = "create_new_preflight_approval_and_ceremony"
    DO_NOT_RETRY_RESUME = "do_not_retry_resume"
    INSPECT_DEPLOYMENT_READ_ONLY = "inspect_deployment_state_read_only"
    RECONCILE_EXISTING_EXECUTION = "reconcile_existing_action_execution"
    VERIFY_AFTER_CONFIRMED_SUCCESS = (
        "start_verification_only_after_confirmed_success"
    )
    OBSERVE_EXACTLY_ONCE_VERIFICATION = "observe_exactly_once_verification"
    REVIEW_PERSISTED_FAILURE = "review_persisted_failure"


class ProductionPilotCeremonyAuditSnapshot(BaseModel):
    """
    Bounded, read-only Ceremony activation and recovery evidence.

    The snapshot intentionally has no canonical patch, workload UID,
    resourceVersion, idempotency key, headers, credential references, or
    credentials. Guidance values are fixed codes rather than persisted error
    text so external failures cannot leak through this audit boundary.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    ceremony_id: UUID
    pilot_id: str
    change_ticket: str
    runbook_version: str
    status: ProductionPilotCeremonyStatus
    approval_id: str
    incident_id: UUID
    artifact_id: UUID
    contract_id: UUID
    safety_patch_sha256: str
    reviewer_operator_id: str
    executor_operator_id: str
    execution_id: UUID | None
    evidence_sha256: str
    activation_sha256: str | None
    readiness_checked_at: datetime
    created_at: datetime
    expires_at: datetime
    activated_at: datetime | None
    expired: bool
    execution_status: ActionExecutionStatus | None
    binding_consistent: bool
    clock_consistent: bool
    recovery_state: ProductionPilotCeremonyRecoveryState
    manual_reconciliation_required: bool
    automatic_resume_allowed: Literal[False] = False
    recorded_kill_switch_state: Literal["engaged"] = "engaged"
    recorded_budget_state: Literal["available"] = "available"
    ceremony_zero_write_verified: Literal[True] = True
    ceremony_external_call_count: Literal[0] = 0
    operator_guidance: tuple[
        ProductionPilotCeremonyOperatorGuidance,
        ...,
    ]


_MANUAL_RECOVERY_GUIDANCE = (
    "engage_kill_switch",
    "do_not_retry_resume",
    "inspect_deployment_state_read_only",
    "reconcile_existing_action_execution",
    "start_verification_only_after_confirmed_success",
)


def build_production_pilot_ceremony_audit(
    *,
    ceremony: ProductionPilotCeremonyRecord,
    execution: ActionExecutionRecord | None,
    checked_at: datetime,
) -> ProductionPilotCeremonyAuditSnapshot:
    """Derive one fail-closed public snapshot without mutating any store."""

    if not isinstance(
        ceremony,
        ProductionPilotCeremonyRecord,
    ):
        raise TypeError(
            "Production Pilot Ceremony audit record is invalid"
        )
    if (
        execution is not None
        and not isinstance(
            execution,
            ActionExecutionRecord,
        )
    ):
        raise TypeError(
            "Production Pilot Ceremony audit execution is invalid"
        )
    normalized_checked_at = _aware_utc(
        checked_at
    )
    expired = normalized_checked_at >= ceremony.expires_at
    clock_consistent = (
        normalized_checked_at >= ceremony.created_at
        and normalized_checked_at
        >= ceremony.readiness_checked_at
    )
    binding_consistent = _binding_consistent(
        ceremony=ceremony,
        execution=execution,
    )
    (
        recovery_state,
        manual_reconciliation_required,
        guidance,
    ) = _recovery(
        ceremony=ceremony,
        execution=execution,
        expired=expired,
        safe_state=(
            binding_consistent
            and clock_consistent
        ),
    )

    return ProductionPilotCeremonyAuditSnapshot(
        ceremony_id=ceremony.ceremony_id,
        pilot_id=ceremony.pilot_id,
        change_ticket=ceremony.change_ticket,
        runbook_version=ceremony.runbook_version,
        status=ceremony.status,
        approval_id=ceremony.approval_id,
        incident_id=ceremony.incident_id,
        artifact_id=ceremony.artifact_id,
        contract_id=ceremony.contract_id,
        safety_patch_sha256=ceremony.patch_sha256,
        reviewer_operator_id=(
            ceremony.reviewer_operator_id
        ),
        executor_operator_id=(
            ceremony.executor_operator_id
        ),
        execution_id=ceremony.execution_id,
        evidence_sha256=ceremony.evidence_sha256,
        activation_sha256=ceremony.activation_sha256,
        readiness_checked_at=(
            ceremony.readiness_checked_at
        ),
        created_at=ceremony.created_at,
        expires_at=ceremony.expires_at,
        activated_at=ceremony.activated_at,
        expired=expired,
        execution_status=(
            execution.status
            if execution is not None
            else None
        ),
        binding_consistent=binding_consistent,
        clock_consistent=clock_consistent,
        recovery_state=recovery_state,
        manual_reconciliation_required=(
            manual_reconciliation_required
        ),
        operator_guidance=guidance,
    )


def _binding_consistent(
    *,
    ceremony: ProductionPilotCeremonyRecord,
    execution: ActionExecutionRecord | None,
) -> bool:
    if execution is None:
        return (
            ceremony.status
            == ProductionPilotCeremonyStatus.READY
            and ceremony.execution_id is None
        )

    metadata = execution.metadata
    basic_binding = (
        execution.approval_id == ceremony.approval_id
        and execution.incident_id == ceremony.incident_id
        and execution.operator_id
        == ceremony.executor_operator_id
        and metadata.get("execution_mode")
        == "kubernetes_production"
        and metadata.get("preflight_artifact_id")
        == str(ceremony.artifact_id)
        and metadata.get("safety_contract_id")
        == str(ceremony.contract_id)
        and metadata.get("safety_patch_sha256")
        == ceremony.patch_sha256
    )
    if not basic_binding:
        return False

    if ceremony.status == ProductionPilotCeremonyStatus.READY:
        return (
            ceremony.execution_id is None
            and ceremony.execution_idempotency_key is None
        )

    return (
        ceremony.execution_id == execution.id
        and ceremony.execution_idempotency_key
        == execution.idempotency_key
        and ceremony.activation_sha256
        == ceremony.expected_activation_sha256()
    )


def _recovery(
    *,
    ceremony: ProductionPilotCeremonyRecord,
    execution: ActionExecutionRecord | None,
    expired: bool,
    safe_state: bool,
) -> tuple[
    ProductionPilotCeremonyRecoveryState,
    bool,
    tuple[str, ...],
]:
    if not safe_state:
        return (
            ProductionPilotCeremonyRecoveryState.INCONSISTENT,
            True,
            _MANUAL_RECOVERY_GUIDANCE,
        )

    if execution is None:
        if expired:
            return (
                ProductionPilotCeremonyRecoveryState.EXPIRED_BEFORE_EXECUTION,
                False,
                (
                    "engage_kill_switch",
                    "do_not_resume_expired_ceremony",
                    "create_new_preflight_approval_and_ceremony",
                ),
            )
        return (
            ProductionPilotCeremonyRecoveryState.READY_FOR_FIRST_RESUME,
            False,
            (
                "keep_kill_switch_engaged_until_runbook_complete",
                "use_one_authenticated_resume",
            ),
        )

    if ceremony.status == ProductionPilotCeremonyStatus.READY:
        if execution.status == ActionExecutionStatus.FAILED:
            return (
                ProductionPilotCeremonyRecoveryState.EXECUTION_FAILED,
                False,
                (
                    "engage_kill_switch",
                    "do_not_retry_resume",
                    "review_persisted_failure",
                ),
            )
        return (
            ProductionPilotCeremonyRecoveryState.CLAIM_NOT_ACTIVATED,
            True,
            _MANUAL_RECOVERY_GUIDANCE,
        )

    if execution.status == ActionExecutionStatus.RUNNING:
        return (
            ProductionPilotCeremonyRecoveryState.ACTIVATED_OUTCOME_UNCONFIRMED,
            True,
            _MANUAL_RECOVERY_GUIDANCE,
        )
    if execution.status == ActionExecutionStatus.SUCCEEDED:
        return (
            ProductionPilotCeremonyRecoveryState.EXECUTION_SUCCEEDED,
            False,
            (
                "engage_kill_switch",
                "do_not_retry_resume",
                "observe_exactly_once_verification",
            ),
        )
    if execution.status == ActionExecutionStatus.FAILED:
        return (
            ProductionPilotCeremonyRecoveryState.EXECUTION_FAILED,
            False,
            (
                "engage_kill_switch",
                "do_not_retry_resume",
                "review_persisted_failure",
            ),
        )
    return (
        ProductionPilotCeremonyRecoveryState.EXECUTION_INDETERMINATE,
        True,
        _MANUAL_RECOVERY_GUIDANCE,
    )


def _aware_utc(
    value: datetime,
) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            "Production Pilot Ceremony audit clock is invalid"
        )
    return value.astimezone(UTC)


__all__ = [
    "ProductionPilotCeremonyAuditSnapshot",
    "ProductionPilotCeremonyOperatorGuidance",
    "ProductionPilotCeremonyRecoveryState",
    "build_production_pilot_ceremony_audit",
]
