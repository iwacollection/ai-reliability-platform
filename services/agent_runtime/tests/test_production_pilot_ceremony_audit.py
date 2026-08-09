from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.production_pilot_ceremony_audit import (
    ProductionPilotCeremonyRecoveryState,
    build_production_pilot_ceremony_audit,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    NOW,
)
from services.agent_runtime.tests.test_production_pilot_ceremony import (
    ceremony_record,
)


EXECUTION_ID = UUID(
    "60000000-0000-4000-8000-000000000606"
)


def execution_record(
    *,
    status: ActionExecutionStatus = ActionExecutionStatus.RUNNING,
    operator_id: str = "executor-pilot-1",
) -> ActionExecutionRecord:
    result = {}
    completed_at = None
    if status == ActionExecutionStatus.SUCCEEDED:
        result = {
            "success": True,
            "mode": "kubernetes_production",
        }
        completed_at = NOW + timedelta(minutes=3)
    elif status in {
        ActionExecutionStatus.FAILED,
        ActionExecutionStatus.INDETERMINATE,
    }:
        result = {
            "success": False,
            "mode": "kubernetes_production",
        }
        completed_at = NOW + timedelta(minutes=3)

    return ActionExecutionRecord(
        id=EXECUTION_ID,
        approval_id=(
            "40000000-0000-4000-8000-000000000404"
        ),
        incident_id=(
            "10000000-0000-4000-8000-000000000101"
        ),
        operator_id=operator_id,
        idempotency_key="execute-pilot-0001",
        action=ActionPlan(
            type=ActionType.INCREASE_MEMORY_LIMIT,
            target="payment-api",
            namespace="payment",
            cluster="production-a",
            risk=ActionRisk.MEDIUM,
            approved=True,
        ),
        status=status,
        result=result,
        completed_at=completed_at,
        metadata={
            "execution_mode": "kubernetes_production",
            "preflight_artifact_id": (
                "20000000-0000-4000-8000-000000000202"
            ),
            "safety_contract_id": (
                "20000000-0000-4000-8000-000000000202"
            ),
            "safety_patch_sha256": "a" * 64,
        },
    )


def activated_ceremony():
    return ceremony_record().activate(
        execution_id=EXECUTION_ID,
        execution_idempotency_key="execute-pilot-0001",
        activated_at=NOW + timedelta(minutes=2),
    )


def test_ready_snapshot_is_bounded_and_allows_only_first_human_resume():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=ceremony_record(),
        execution=None,
        checked_at=NOW + timedelta(minutes=2),
    )

    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.READY_FOR_FIRST_RESUME.value
    )
    assert snapshot.binding_consistent is True
    assert snapshot.clock_consistent is True
    assert snapshot.manual_reconciliation_required is False
    assert snapshot.automatic_resume_allowed is False
    assert snapshot.operator_guidance == (
        "keep_kill_switch_engaged_until_runbook_complete",
        "use_one_authenticated_resume",
    )

    text = str(
        snapshot.model_dump(
            mode="json"
        )
    ).lower()
    for forbidden in (
        "patch_json",
        "workload_uid",
        "resource_version",
        "idempotency_key",
        "authorization",
        "credential",
        "bearer",
        "token",
    ):
        assert forbidden not in text

    with pytest.raises(
        ValidationError,
    ):
        type(snapshot).model_validate(
            {
                **snapshot.model_dump(),
                "operator_guidance": [
                    "external_error_text"
                ],
            }
        )


def test_expired_ready_snapshot_requires_new_preparation():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=ceremony_record(),
        execution=None,
        checked_at=NOW + timedelta(minutes=11),
    )

    assert snapshot.expired is True
    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.EXPIRED_BEFORE_EXECUTION.value
    )
    assert "do_not_resume_expired_ceremony" in (
        snapshot.operator_guidance
    )


def test_running_activated_snapshot_requires_manual_reconciliation():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=activated_ceremony(),
        execution=execution_record(),
        checked_at=NOW + timedelta(minutes=3),
    )

    assert snapshot.binding_consistent is True
    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.ACTIVATED_OUTCOME_UNCONFIRMED.value
    )
    assert snapshot.manual_reconciliation_required is True
    assert snapshot.automatic_resume_allowed is False
    assert snapshot.operator_guidance == (
        "engage_kill_switch",
        "do_not_retry_resume",
        "inspect_deployment_state_read_only",
        "reconcile_existing_action_execution",
        "start_verification_only_after_confirmed_success",
    )


@pytest.mark.parametrize(
    (
        "status",
        "expected_state",
        "manual_required",
    ),
    (
        (
            ActionExecutionStatus.SUCCEEDED,
            ProductionPilotCeremonyRecoveryState.EXECUTION_SUCCEEDED,
            False,
        ),
        (
            ActionExecutionStatus.FAILED,
            ProductionPilotCeremonyRecoveryState.EXECUTION_FAILED,
            False,
        ),
        (
            ActionExecutionStatus.INDETERMINATE,
            ProductionPilotCeremonyRecoveryState.EXECUTION_INDETERMINATE,
            True,
        ),
    ),
)
def test_activated_terminal_outcome_recovery_is_explicit(
    status,
    expected_state,
    manual_required,
):
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=activated_ceremony(),
        execution=execution_record(
            status=status
        ),
        checked_at=NOW + timedelta(minutes=4),
    )

    assert snapshot.recovery_state == expected_state.value
    assert snapshot.manual_reconciliation_required is manual_required
    assert snapshot.automatic_resume_allowed is False


def test_claim_before_activation_is_never_automatically_resumed():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=ceremony_record(),
        execution=execution_record(),
        checked_at=NOW + timedelta(minutes=2),
    )

    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.CLAIM_NOT_ACTIVATED.value
    )
    assert snapshot.manual_reconciliation_required is True
    assert "do_not_retry_resume" in snapshot.operator_guidance


def test_execution_binding_mismatch_is_inconsistent_and_fail_closed():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=activated_ceremony(),
        execution=execution_record(
            operator_id="spoofed-executor"
        ),
        checked_at=NOW + timedelta(minutes=3),
    )

    assert snapshot.binding_consistent is False
    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.INCONSISTENT.value
    )
    assert snapshot.manual_reconciliation_required is True
    assert snapshot.automatic_resume_allowed is False


def test_audit_rejects_a_naive_clock():
    with pytest.raises(
        ValueError,
        match="clock",
    ):
        build_production_pilot_ceremony_audit(
            ceremony=ceremony_record(),
            execution=None,
            checked_at=(
                NOW.replace(
                    tzinfo=None
                )
            ),
        )


def test_clock_rollback_is_visible_and_requires_manual_reconciliation():
    snapshot = build_production_pilot_ceremony_audit(
        ceremony=ceremony_record(),
        execution=None,
        checked_at=NOW,
    )

    assert snapshot.binding_consistent is True
    assert snapshot.clock_consistent is False
    assert snapshot.recovery_state == (
        ProductionPilotCeremonyRecoveryState.INCONSISTENT.value
    )
    assert snapshot.manual_reconciliation_required is True
