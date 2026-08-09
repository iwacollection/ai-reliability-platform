from datetime import timedelta
from pathlib import Path

import pytest

from services.agent_runtime.app.action.production_action_guard import (
    ProductionActionBindingError,
    ProductionActionClockError,
    ProductionActionContractExpiredError,
)
from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
    ARTIFACT_ID,
    MutableClock,
    NOW,
    generic_action,
    isolated_services,
    persist_prepared_workflow,
)


async def prepared_state(
    tmp_path: Path,
):
    clock = MutableClock(
        NOW
        + timedelta(minutes=1)
    )
    (
        artifact_service,
        guard,
        approval_service,
        incident_store,
    ) = isolated_services(
        tmp_path,
        clock,
    )
    record, approval, incident = (
        await persist_prepared_workflow(
            artifact_service=artifact_service,
            approval_service=approval_service,
            incident_store=incident_store,
        )
    )
    return (
        clock,
        artifact_service,
        guard,
        approval_service,
        incident_store,
        record,
        approval,
        incident,
    )


def decision_arguments():
    return {
        "operator_id": "approver-expiry-test",
        "idempotency_key": "approve-expiry-0001",
        "reason": "Reviewed bounded production change",
        "metadata": {
            "source": "test",
        },
    }


@pytest.mark.asyncio
async def test_store_and_service_resolve_artifact_by_approval_id(
    tmp_path: Path,
):
    (
        _,
        artifact_service,
        _,
        _,
        _,
        record,
        _,
        _,
    ) = await prepared_state(
        tmp_path
    )

    resolved = (
        await artifact_service.get_by_approval_id(
            APPROVAL_ID
        )
    )

    assert resolved == record
    assert resolved.artifact_id == ARTIFACT_ID
    assert await artifact_service.get_by_approval_id(
        "00000000-0000-4000-8000-000000000499"
    ) is None


@pytest.mark.asyncio
async def test_unexpired_preflight_can_be_approved(
    tmp_path: Path,
):
    (
        _,
        _,
        _,
        approval_service,
        _,
        _,
        _,
        _,
    ) = await prepared_state(
        tmp_path
    )

    approved = await approval_service.approve(
        APPROVAL_ID,
        **decision_arguments(),
    )

    assert approved.status == (
        ApprovalStatus.APPROVED
    )
    assert approved.action.approved is True
    assert approved.decision is not None


@pytest.mark.asyncio
async def test_expired_preflight_cannot_be_approved_or_mutated(
    tmp_path: Path,
):
    (
        clock,
        _,
        _,
        approval_service,
        _,
        _,
        approval_before,
        _,
    ) = await prepared_state(
        tmp_path
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )

    with pytest.raises(
        ProductionActionContractExpiredError,
        match="Safety Contract has expired",
    ):
        await approval_service.approve(
            APPROVAL_ID,
            **decision_arguments(),
        )

    current = await approval_service.get(
        APPROVAL_ID
    )
    assert current == approval_before
    assert current.status == ApprovalStatus.PENDING
    assert current.action.approved is False
    assert current.decision is None


@pytest.mark.asyncio
async def test_exact_approval_replay_remains_safe_after_expiry(
    tmp_path: Path,
):
    (
        clock,
        _,
        guard,
        approval_service,
        _,
        _,
        _,
        _,
    ) = await prepared_state(
        tmp_path
    )
    arguments = decision_arguments()
    first = await approval_service.approve(
        APPROVAL_ID,
        **arguments,
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )

    with pytest.raises(
        ProductionActionContractExpiredError,
    ):
        await guard.require_active(
            first
        )

    replay = await approval_service.approve(
        APPROVAL_ID,
        **arguments,
    )

    assert replay == first
    assert replay.decision == first.decision


@pytest.mark.asyncio
async def test_expired_preflight_can_still_be_rejected(
    tmp_path: Path,
):
    (
        clock,
        _,
        _,
        approval_service,
        _,
        _,
        _,
        _,
    ) = await prepared_state(
        tmp_path
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )

    rejected = await approval_service.reject(
        APPROVAL_ID,
        operator_id="approver-expiry-test",
        idempotency_key="reject-expiry-0001",
        reason="Expired preparation is rejected",
        metadata={
            "source": "test",
        },
    )

    assert rejected.status == (
        ApprovalStatus.REJECTED
    )
    assert rejected.action.approved is False


@pytest.mark.asyncio
async def test_generic_approval_remains_compatible(
    tmp_path: Path,
):
    clock = MutableClock(
        NOW
        + timedelta(minutes=30)
    )
    (
        _,
        _,
        approval_service,
        _,
    ) = isolated_services(
        tmp_path,
        clock,
    )
    generic = await approval_service.create_approval(
        action=generic_action(),
        reason="Legacy Approval",
    )

    approved = await approval_service.approve(
        generic.id
    )

    assert approved.status == (
        ApprovalStatus.APPROVED
    )
    assert approved.action.approved is True


@pytest.mark.asyncio
async def test_claimed_preflight_without_artifact_fails_closed(
    tmp_path: Path,
):
    clock = MutableClock(
        NOW
        + timedelta(minutes=1)
    )
    (
        _,
        _,
        approval_service,
        _,
    ) = isolated_services(
        tmp_path,
        clock,
    )
    approval = await approval_service.create_approval(
        action=generic_action(),
        reason="Tampered production Approval",
        metadata={
            "source": (
                "production_action_preparation"
            ),
            "preflight_artifact_id": str(
                ARTIFACT_ID
            ),
        },
    )

    with pytest.raises(
        ProductionActionBindingError,
        match="Artifact is unavailable",
    ):
        await approval_service.approve(
            approval.id
        )


@pytest.mark.asyncio
async def test_tampered_preflight_digest_binding_fails_closed(
    tmp_path: Path,
):
    (
        _,
        _,
        _,
        approval_service,
        _,
        _,
        approval,
        _,
    ) = await prepared_state(
        tmp_path
    )
    tampered = approval.model_copy(
        deep=True
    )
    tampered.metadata[
        "safety_patch_sha256"
    ] = "0" * 64
    await approval_service.manager.store.update(
        tampered,
        expected_status=ApprovalStatus.PENDING,
    )

    with pytest.raises(
        ProductionActionBindingError,
        match="metadata is inconsistent",
    ):
        await approval_service.approve(
            APPROVAL_ID,
            **decision_arguments(),
        )

    current = await approval_service.get(
        APPROVAL_ID
    )
    assert current.status == ApprovalStatus.PENDING
    assert current.action.approved is False
    assert current.decision is None


@pytest.mark.asyncio
async def test_clock_rollback_fails_closed(
    tmp_path: Path,
):
    (
        clock,
        _,
        _,
        approval_service,
        _,
        _,
        _,
        _,
    ) = await prepared_state(
        tmp_path
    )
    clock.set(
        NOW
        - timedelta(seconds=1)
    )

    with pytest.raises(
        ProductionActionClockError,
        match="clock is invalid",
    ):
        await approval_service.approve(
            APPROVAL_ID,
            **decision_arguments(),
        )

    current = await approval_service.get(
        APPROVAL_ID
    )
    assert current.status == ApprovalStatus.PENDING
    assert current.decision is None
