from datetime import UTC, datetime

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


def build_record(
    **overrides,
) -> ActionExecutionRecord:
    values = {
        "approval_id": "approval-1",
        "operator_id": "sre-1",
        "idempotency_key": "execution-1",
        "action": ActionPlan(
            type=ActionType.RESTART_POD,
            target="payment-api",
            namespace="payment",
            cluster="production-tw",
            risk=ActionRisk.MEDIUM,
            approved=True,
        ),
    }
    values.update(overrides)

    return ActionExecutionRecord(
        **values,
    )


def test_new_execution_is_a_fail_closed_running_claim():
    record = build_record(
        approval_id="  approval-1  ",
        operator_id="  sre-1  ",
        idempotency_key="  execution-1  ",
    )

    assert record.approval_id == "approval-1"
    assert record.operator_id == "sre-1"
    assert record.idempotency_key == "execution-1"
    assert record.status == ActionExecutionStatus.RUNNING
    assert record.completed_at is None
    assert record.is_terminal is False
    assert record.requires_reconciliation is False
    assert record.automatic_replay_allowed is False


@pytest.mark.parametrize(
    "field_name",
    [
        "approval_id",
        "operator_id",
        "idempotency_key",
    ],
)
def test_required_identifiers_reject_blank_values(
    field_name: str,
):
    with pytest.raises(ValidationError):
        build_record(
            **{
                field_name: "   ",
            }
        )


def test_running_execution_can_succeed_once():
    record = build_record()

    record.succeed(
        {
            "success": True,
            "action": "restart_pod",
            "target": "payment-api",
        }
    )

    assert record.status == ActionExecutionStatus.SUCCEEDED
    assert record.result["success"] is True
    assert record.completed_at is not None
    assert record.is_terminal is True
    assert record.automatic_replay_allowed is False

    with pytest.raises(
        ValueError,
        match="not running",
    ):
        record.succeed(
            {
                "success": True,
            }
        )


def test_success_transition_requires_positive_executor_result():
    record = build_record()

    with pytest.raises(
        ValueError,
        match="result.success=true",
    ):
        record.succeed(
            {
                "success": False,
            }
        )

    assert record.status == ActionExecutionStatus.RUNNING
    assert record.completed_at is None


def test_running_execution_can_fail_once():
    record = build_record()

    record.fail(
        {
            "success": False,
            "status": "executor_failed",
        },
        error_type="ExecutorError",
        error_message="Kubernetes API request failed",
    )

    assert record.status == ActionExecutionStatus.FAILED
    assert record.result["success"] is False
    assert record.error_type == "ExecutorError"
    assert record.completed_at is not None
    assert record.is_terminal is True
    assert record.automatic_replay_allowed is False

    with pytest.raises(
        ValueError,
        match="not running",
    ):
        record.fail(
            {
                "success": False,
            }
        )


def test_failure_transition_rejects_successful_executor_result():
    record = build_record()

    with pytest.raises(
        ValueError,
        match="cannot have result.success=true",
    ):
        record.fail(
            {
                "success": True,
            }
        )

    assert record.status == ActionExecutionStatus.RUNNING
    assert record.completed_at is None


def test_crash_window_becomes_indeterminate_and_is_not_replayed():
    record = build_record()

    record.mark_indeterminate(
        "Runtime restarted while the external executor was running"
    )

    assert (
        record.status
        == ActionExecutionStatus.INDETERMINATE
    )
    assert record.requires_reconciliation is True
    assert record.is_terminal is False
    assert record.automatic_replay_allowed is False
    assert record.error_type == "IndeterminateExecution"
    assert record.completed_at is not None

    with pytest.raises(
        ValueError,
        match="not running",
    ):
        record.succeed(
            {
                "success": True,
            }
        )


def test_indeterminate_transition_requires_reason():
    record = build_record()

    with pytest.raises(
        ValueError,
        match="requires a reason",
    ):
        record.mark_indeterminate("   ")

    assert record.status == ActionExecutionStatus.RUNNING
    assert record.completed_at is None


def test_invalid_persisted_lifecycle_snapshots_are_rejected():
    completed_at = datetime.now(UTC)

    with pytest.raises(
        ValidationError,
        match="running execution cannot have completed_at",
    ):
        build_record(
            status=ActionExecutionStatus.RUNNING,
            completed_at=completed_at,
        )

    with pytest.raises(
        ValidationError,
        match="closed execution must have completed_at",
    ):
        build_record(
            status=ActionExecutionStatus.SUCCEEDED,
            result={
                "success": True,
            },
        )

    with pytest.raises(
        ValidationError,
        match="result.success=true",
    ):
        build_record(
            status=ActionExecutionStatus.SUCCEEDED,
            result={
                "success": False,
            },
            completed_at=completed_at,
        )


def test_execution_record_round_trip_preserves_action_scope():
    original = build_record()

    original.mark_indeterminate(
        "External outcome requires reconciliation"
    )

    restored = ActionExecutionRecord.model_validate_json(
        original.model_dump_json()
    )

    assert restored == original
    assert restored.action.target == "payment-api"
    assert restored.action.namespace == "payment"
    assert restored.action.cluster == "production-tw"
    assert (
        restored.status
        == ActionExecutionStatus.INDETERMINATE
    )
    assert restored.automatic_replay_allowed is False
