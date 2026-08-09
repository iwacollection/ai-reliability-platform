from datetime import timedelta

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionReconciliationDecision,
    ActionExecutionReconciliationOutcome,
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)


def build_record(
    **overrides,
) -> ActionExecutionRecord:
    values = {
        "approval_id": "approval-reconciliation-1",
        "operator_id": "executor-operator",
        "idempotency_key": "execution-key-1",
        "action": ActionPlan(
            type=ActionType.RESTART_POD,
            target="payment-api",
            namespace="payment",
            cluster="production-tw",
            risk=ActionRisk.MEDIUM,
            approved=True,
        ),
    }
    values.update(
        overrides
    )

    return ActionExecutionRecord(
        **values
    )


def build_decision(
    *,
    outcome=(
        ActionExecutionReconciliationOutcome.SUCCEEDED
    ),
    result=None,
    **overrides,
) -> ActionExecutionReconciliationDecision:
    values = {
        "outcome": outcome,
        "operator_id": "reconciliation-operator",
        "idempotency_key": "reconciliation-key-1",
        "reason": "External outcome was confirmed",
        "result": (
            {
                "success": True,
                "source": "operator_evidence",
            }
            if result is None
            else result
        ),
    }
    values.update(
        overrides
    )

    return ActionExecutionReconciliationDecision(
        **values
    )


def indeterminate_record() -> ActionExecutionRecord:
    record = build_record()
    record.mark_indeterminate(
        "Executor connection was lost before an outcome was returned"
    )

    return record


def test_legacy_execution_defaults_to_no_reconciliation():
    record = build_record()

    assert record.reconciliation is None
    assert record.was_reconciled is False
    assert record.requires_reconciliation is False


def test_reconciliation_decision_normalizes_audit_text():
    decision = build_decision(
        operator_id="  sre-operator  ",
        idempotency_key="  reconcile-request-1  ",
        reason="  Verified from the Kubernetes API  ",
        error_type="   ",
        error_message="   ",
    )

    assert decision.operator_id == "sre-operator"
    assert decision.idempotency_key == (
        "reconcile-request-1"
    )
    assert decision.reason == (
        "Verified from the Kubernetes API"
    )
    assert decision.error_type is None
    assert decision.error_message is None


@pytest.mark.parametrize(
    "result",
    [
        {},
        {
            "success": False,
        },
        {
            "success": 1,
        },
        {
            "success": "true",
        },
        {
            "success": None,
        },
    ],
)
def test_successful_reconciliation_requires_literal_true(
    result,
):
    with pytest.raises(
        ValidationError,
        match="result.success=true",
    ):
        build_decision(
            result=result
        )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {
            "success": True,
        },
        {
            "success": 0,
        },
        {
            "success": "false",
        },
        {
            "success": None,
        },
    ],
)
def test_failed_reconciliation_requires_literal_false(
    result,
):
    with pytest.raises(
        ValidationError,
        match="result.success=false",
    ):
        build_decision(
            outcome=(
                ActionExecutionReconciliationOutcome.FAILED
            ),
            result=result,
        )


@pytest.mark.parametrize(
    "field_name, value",
    [
        (
            "operator_id",
            "   ",
        ),
        (
            "idempotency_key",
            "   ",
        ),
        (
            "reason",
            "   ",
        ),
    ],
)
def test_reconciliation_rejects_blank_audit_fields(
    field_name: str,
    value: str,
):
    with pytest.raises(
        ValidationError
    ):
        build_decision(
            **{
                field_name: value,
            }
        )


def test_successful_reconciliation_rejects_error_details():
    with pytest.raises(
        ValidationError,
        match="cannot contain error details",
    ):
        build_decision(
            error_type="UnexpectedError",
            error_message=(
                "A successful decision cannot retain this error"
            ),
        )


def test_reconciliation_decision_is_immutable():
    decision = build_decision()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        decision.reason = "Changed after persistence"


def test_indeterminate_execution_can_be_reconciled_as_success():
    record = indeterminate_record()
    decision = build_decision(
        reason=(
            "Pod UID changed and the replacement Pod is healthy"
        ),
        metadata={
            "evidence": "kubernetes_api",
        },
    )

    record.reconcile(
        decision
    )

    assert record.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert record.result == decision.result
    assert record.error_type is None
    assert record.error_message is None
    assert record.completed_at == (
        decision.reconciled_at
    )
    assert record.updated_at == (
        decision.reconciled_at
    )
    assert record.reconciliation == decision
    assert record.was_reconciled is True
    assert record.requires_reconciliation is False
    assert record.is_terminal is True
    assert record.automatic_replay_allowed is False


def test_indeterminate_execution_can_be_reconciled_as_failure():
    record = indeterminate_record()
    decision = build_decision(
        outcome=(
            ActionExecutionReconciliationOutcome.FAILED
        ),
        reason=(
            "The target workload was not changed"
        ),
        result={
            "success": False,
            "source": "operator_evidence",
        },
    )

    record.reconcile(
        decision
    )

    assert record.status == (
        ActionExecutionStatus.FAILED
    )
    assert record.result == decision.result
    assert record.error_type == (
        "ReconciledExecutionFailure"
    )
    assert record.error_message == (
        decision.reason
    )
    assert record.completed_at == (
        decision.reconciled_at
    )
    assert record.reconciliation == decision
    assert record.was_reconciled is True
    assert record.requires_reconciliation is False
    assert record.is_terminal is True
    assert record.automatic_replay_allowed is False


def test_failed_reconciliation_preserves_explicit_error_details():
    record = indeterminate_record()
    decision = build_decision(
        outcome=(
            ActionExecutionReconciliationOutcome.FAILED
        ),
        reason="Kubernetes audit evidence confirmed failure",
        result={
            "success": False,
        },
        error_type="KubernetesMutationNotObserved",
        error_message=(
            "Resource generation and Pod UID were unchanged"
        ),
    )

    record.reconcile(
        decision
    )

    assert record.error_type == (
        "KubernetesMutationNotObserved"
    )
    assert record.error_message == (
        "Resource generation and Pod UID were unchanged"
    )


@pytest.mark.parametrize(
    "initial_status",
    [
        ActionExecutionStatus.RUNNING,
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    ],
)
def test_only_indeterminate_execution_can_be_reconciled(
    initial_status: ActionExecutionStatus,
):
    record = build_record()

    if initial_status == ActionExecutionStatus.SUCCEEDED:
        record.succeed(
            {
                "success": True,
            }
        )
    elif initial_status == ActionExecutionStatus.FAILED:
        record.fail(
            {
                "success": False,
            }
        )

    with pytest.raises(
        ValueError,
        match="only an indeterminate",
    ):
        record.reconcile(
            build_decision()
        )

    assert record.status == initial_status
    assert record.reconciliation is None


@pytest.mark.parametrize(
    "tamper",
    [
        "status",
        "result",
        "completed_at",
    ],
)
def test_reconciled_snapshot_rejects_inconsistent_audit_data(
    tamper: str,
):
    record = indeterminate_record()
    record.reconcile(
        build_decision()
    )
    payload = record.model_dump(
        mode="python"
    )

    if tamper == "status":
        payload["status"] = (
            ActionExecutionStatus.FAILED
        )
    elif tamper == "result":
        payload["result"] = {
            "success": True,
            "source": "tampered",
        }
    else:
        payload["completed_at"] = (
            record.completed_at
            + timedelta(
                seconds=1
            )
        )

    with pytest.raises(
        ValidationError
    ):
        ActionExecutionRecord.model_validate(
            payload
        )


def test_reconciled_record_json_round_trip_preserves_audit():
    original = indeterminate_record()
    original.reconcile(
        build_decision(
            metadata={
                "ticket": "INC-2026-0001",
            }
        )
    )

    restored = (
        ActionExecutionRecord.model_validate_json(
            original.model_dump_json()
        )
    )

    assert restored == original
    assert restored.reconciliation is not None
    assert restored.reconciliation.metadata == {
        "ticket": "INC-2026-0001",
    }
    assert restored.was_reconciled is True
    assert restored.automatic_replay_allowed is False


def test_legacy_json_without_reconciliation_field_still_loads():
    original = indeterminate_record()
    legacy_payload = original.model_dump(
        mode="python"
    )
    legacy_payload.pop(
        "reconciliation"
    )

    restored = ActionExecutionRecord.model_validate(
        legacy_payload
    )

    assert restored.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert restored.reconciliation is None
    assert restored.was_reconciled is False
    assert restored.requires_reconciliation is True
    assert restored.automatic_replay_allowed is False
