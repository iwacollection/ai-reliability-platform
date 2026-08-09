import asyncio

from pathlib import Path
from uuid import uuid4

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)

from services.agent_runtime.app.approval.manager import (
    ApprovalManager,
)

from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)

from services.agent_runtime.app.approval.service import (
    ApprovalService,
)

from services.agent_runtime.app.approval.store import (
    ApprovalConflictError,
    ApprovalStore,
)


def build_action() -> ActionPlan:
    return ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="payment-api",
        risk=ActionRisk.MEDIUM,
        approved=False,
        metadata={
            "reason":
            "Pod memory limit exceeded",
        },
    )


@pytest.mark.asyncio
async def test_approval_persists_across_store_instances(
    tmp_path: Path,
):
    """
    A newly created ApprovalStore must be able to read an approval
    written by a previous ApprovalStore instance.
    """

    db_path = (
        tmp_path
        / "approvals.db"
    )

    first_service = ApprovalService(
        manager=ApprovalManager(
            store=ApprovalStore(
                db_path
            )
        )
    )

    incident_id = uuid4()

    created = await first_service.create_approval(
        action=build_action(),
        reason=(
            "Medium risk action requires "
            "human approval."
        ),
        incident_id=incident_id,
    )

    second_service = ApprovalService(
        manager=ApprovalManager(
            store=ApprovalStore(
                db_path
            )
        )
    )

    loaded = await second_service.get(
        created.id
    )

    assert loaded is not None

    assert loaded.id == created.id

    assert loaded.incident_id == (
        incident_id
    )

    assert loaded.status == (
        ApprovalStatus.PENDING
    )

    assert loaded.action.type == (
        ActionType.INCREASE_MEMORY_LIMIT
    )

    assert loaded.action.target == (
        "payment-api"
    )


@pytest.mark.asyncio
async def test_sqlite_cas_across_store_instances(
    tmp_path: Path,
):
    """
    Two independent ApprovalStore instances must not be able to
    overwrite each other's approval decision.
    """

    db_path = (
        tmp_path
        / "approvals.db"
    )

    first_store = ApprovalStore(
        db_path
    )

    second_store = ApprovalStore(
        db_path
    )

    request = ApprovalRequest(
        id=str(
            uuid4()
        ),
        incident_id=uuid4(),
        action=build_action(),
        reason=(
            "Medium risk action requires "
            "human approval."
        ),
    )

    await first_store.save(
        request
    )

    approved_request = await first_store.get(
        request.id
    )

    rejected_request = await second_store.get(
        request.id
    )

    assert approved_request is not None

    assert rejected_request is not None

    approved_request.status = (
        ApprovalStatus.APPROVED
    )

    approved_request.action.approved = True

    rejected_request.status = (
        ApprovalStatus.REJECTED
    )

    rejected_request.action.approved = False

    results = await asyncio.gather(
        first_store.update(
            approved_request,
            expected_status=(
                ApprovalStatus.PENDING
            ),
        ),
        second_store.update(
            rejected_request,
            expected_status=(
                ApprovalStatus.PENDING
            ),
        ),
        return_exceptions=True,
    )

    successful_updates = [
        result
        for result in results
        if isinstance(
            result,
            ApprovalRequest,
        )
    ]

    conflicts = [
        result
        for result in results
        if isinstance(
            result,
            ApprovalConflictError,
        )
    ]

    assert len(
        successful_updates
    ) == 1

    assert len(
        conflicts
    ) == 1

    final_store = ApprovalStore(
        db_path
    )

    stored = await final_store.get(
        request.id
    )

    assert stored is not None

    assert stored.status in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
    }

    if (
        stored.status
        == ApprovalStatus.APPROVED
    ):
        assert stored.action.approved is True

    else:
        assert stored.action.approved is False
