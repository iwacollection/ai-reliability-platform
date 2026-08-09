import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.production_pilot_budget_models import (
    ProductionPilotBudgetRecord,
    ProductionPilotBudgetStatus,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetConflictError,
    ProductionPilotBudgetStore,
)
from services.agent_runtime.tests.kubernetes_production_executor_support import (
    execution_record,
    preflight_record,
)


NOW = datetime(
    2026,
    8,
    9,
    12,
    0,
    tzinfo=UTC,
)
PILOT_ID = "oom-one-write-pilot-v1"
CONTRACT_ID = UUID(
    "00000000-0000-4000-8000-000000000601"
)
DIGEST = "a" * 64


def budget_record(
    index: int = 1,
) -> ProductionPilotBudgetRecord:
    return ProductionPilotBudgetRecord(
        pilot_id=PILOT_ID,
        execution_id=UUID(
            f"00000000-0000-4000-8000-{index:012d}"
        ),
        approval_id=f"approval-budget-{index}",
        contract_id=CONTRACT_ID,
        operator_id=f"executor-budget-{index}",
        patch_sha256=DIGEST,
        reserved_at=NOW,
        updated_at=NOW,
    )


def test_budget_model_is_immutable_and_consumption_is_terminal():
    record = budget_record()
    consumed = record.consume(
        consumed_at=(
            NOW + timedelta(seconds=1)
        )
    )

    assert record.status == (
        ProductionPilotBudgetStatus.RESERVED
    )
    assert consumed.status == (
        ProductionPilotBudgetStatus.CONSUMED
    )
    assert consumed.consume(
        consumed_at=(
            NOW + timedelta(seconds=2)
        )
    ) is consumed

    with pytest.raises(ValidationError):
        record.status = (
            ProductionPilotBudgetStatus.CONSUMED
        )

    with pytest.raises(ValidationError):
        record.consume(
            consumed_at=(
                NOW - timedelta(seconds=1)
            )
        )


@pytest.mark.asyncio
async def test_exact_reservation_and_consumption_replays_are_idempotent(
    tmp_path,
):
    first_store = ProductionPilotBudgetStore(
        tmp_path / "pilot_budget.db"
    )
    second_store = ProductionPilotBudgetStore(
        tmp_path / "pilot_budget.db"
    )
    record = budget_record()

    first = await first_store.reserve(
        record
    )
    replay = await second_store.reserve(
        record
    )
    consumed = await first_store.consume(
        pilot_id=PILOT_ID,
        execution_id=record.execution_id,
        contract_id=record.contract_id,
        patch_sha256=record.patch_sha256,
        consumed_at=(
            NOW + timedelta(seconds=1)
        ),
    )
    consumed_replay = await second_store.consume(
        pilot_id=PILOT_ID,
        execution_id=record.execution_id,
        contract_id=record.contract_id,
        patch_sha256=record.patch_sha256,
        consumed_at=(
            NOW + timedelta(seconds=2)
        ),
    )

    assert first.created is True
    assert replay.created is False
    assert consumed.applied is True
    assert consumed_replay.applied is False
    assert consumed_replay.record == consumed.record


@pytest.mark.asyncio
async def test_one_pilot_rejects_a_different_execution(
    tmp_path,
):
    store = ProductionPilotBudgetStore(
        tmp_path / "pilot_budget.db"
    )
    await store.reserve(
        budget_record(1)
    )

    with pytest.raises(
        ProductionPilotBudgetConflictError
    ):
        await store.reserve(
            budget_record(2)
        )


@pytest.mark.asyncio
async def test_cross_instance_concurrency_allows_one_distinct_execution(
    tmp_path,
):
    db_path = tmp_path / "pilot_budget.db"
    stores = [
        ProductionPilotBudgetStore(
            db_path
        )
        for _ in range(12)
    ]

    async def reserve(index):
        try:
            result = await stores[index].reserve(
                budget_record(index + 1)
            )
            return "created" if result.created else "replay"
        except ProductionPilotBudgetConflictError:
            return "conflict"

    outcomes = await asyncio.gather(
        *(
            reserve(index)
            for index in range(len(stores))
        )
    )

    assert outcomes.count("created") == 1
    assert outcomes.count("conflict") == 11
    persisted = await stores[0].get(
        PILOT_ID
    )
    assert persisted is not None
    assert persisted.status == (
        ProductionPilotBudgetStatus.RESERVED
    )


@pytest.mark.asyncio
async def test_consumption_rejects_wrong_binding(
    tmp_path,
):
    store = ProductionPilotBudgetStore(
        tmp_path / "pilot_budget.db"
    )
    record = budget_record()
    await store.reserve(record)

    with pytest.raises(
        ProductionPilotBudgetConflictError
    ):
        await store.consume(
            pilot_id=PILOT_ID,
            execution_id=record.execution_id,
            contract_id=record.contract_id,
            patch_sha256="b" * 64,
            consumed_at=(
                NOW + timedelta(seconds=1)
            ),
        )


@pytest.mark.asyncio
async def test_service_derives_immutable_binding_and_consumes_once(
    tmp_path,
):
    service = ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path / "pilot_budget.db"
        ),
        clock=lambda: NOW,
    )
    execution = execution_record()
    record = preflight_record()

    reservation = await service.reserve(
        pilot_id=PILOT_ID,
        execution=execution,
        preflight_record=record,
    )
    consumption = await service.consume(
        pilot_id=PILOT_ID,
        execution=execution,
        preflight_record=record,
    )
    replay = await service.consume(
        pilot_id=PILOT_ID,
        execution=execution,
        preflight_record=record,
    )

    assert reservation.created is True
    assert reservation.record.execution_id == execution.id
    assert reservation.record.approval_id == execution.approval_id
    assert reservation.record.contract_id == (
        record.artifact.contract.contract_id
    )
    assert consumption.applied is True
    assert replay.applied is False
    assert replay.record.status == (
        ProductionPilotBudgetStatus.CONSUMED
    )


@pytest.mark.asyncio
async def test_service_rejects_non_running_execution_without_budget_write(
    tmp_path,
):
    service = ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path / "pilot_budget.db"
        ),
        clock=lambda: NOW,
    )
    execution = execution_record()
    execution.fail(
        {"success": False},
        error_type="TestFailure",
    )

    with pytest.raises(ValueError, match="RUNNING"):
        await service.reserve(
            pilot_id=PILOT_ID,
            execution=execution,
            preflight_record=preflight_record(),
        )

    assert await service.get(PILOT_ID) is None
