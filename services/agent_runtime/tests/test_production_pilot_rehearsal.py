from datetime import timedelta

import pytest

from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
    KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED,
)
from services.agent_runtime.app.action.production_pilot_budget_models import (
    ProductionPilotBudgetRecord,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetStore,
)
from services.agent_runtime.app.action.production_pilot_rehearsal import (
    ProductionPilotRehearsalService,
)
from services.agent_runtime.tests.test_production_pilot import (
    NOW,
    control,
)


def budget_service(
    tmp_path,
) -> ProductionPilotBudgetService:
    return ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path / "pilot_budget.db"
        ),
        clock=lambda: (
            NOW + timedelta(minutes=1)
        ),
    )


@pytest.mark.asyncio
async def test_rehearsal_passes_only_with_engaged_switch_and_empty_budget(
    tmp_path,
):
    service = budget_service(
        tmp_path
    )
    rehearsal = ProductionPilotRehearsalService(
        control=control(
            switch_value=(
                KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED
            )
        ),
        budget_service=service,
        production_executor_configured=False,
    )

    result = await rehearsal.run(
        operator_id="executor-pilot-1"
    )

    assert result.passed is True
    assert result.zero_write is True
    assert result.budget_state == "available"
    assert result.durable_claim_created is False
    assert result.external_call_count == 0
    assert result.real_write_attempted is False
    assert await service.get(
        "oom-pilot-v1"
    ) is None


@pytest.mark.asyncio
async def test_rehearsal_rejects_disengaged_switch_and_wrong_operator(
    tmp_path,
):
    rehearsal = ProductionPilotRehearsalService(
        control=control(
            switch_value=(
                KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
            )
        ),
        budget_service=budget_service(
            tmp_path
        ),
        production_executor_configured=True,
    )

    result = await rehearsal.run(
        operator_id="executor-not-authorized"
    )

    assert result.passed is False
    assert result.kill_switch_engaged is False
    assert (
        "kill_switch_must_be_engaged_for_rehearsal"
        in result.blockers
    )
    assert (
        "operator_not_authorized_for_pilot"
        in result.blockers
    )
    assert result.external_call_count == 0


@pytest.mark.asyncio
async def test_rehearsal_rejects_existing_budget_without_mutating_it(
    tmp_path,
):
    service = budget_service(
        tmp_path
    )
    record = ProductionPilotBudgetRecord(
        pilot_id="oom-pilot-v1",
        execution_id=(
            "00000000-0000-4000-8000-000000000701"
        ),
        approval_id="approval-rehearsal-existing",
        contract_id=(
            "00000000-0000-4000-8000-000000000702"
        ),
        operator_id="executor-pilot-1",
        patch_sha256="c" * 64,
        reserved_at=(
            NOW + timedelta(minutes=1)
        ),
        updated_at=(
            NOW + timedelta(minutes=1)
        ),
    )
    await service.store.reserve(
        record
    )
    rehearsal = ProductionPilotRehearsalService(
        control=control(
            switch_value=(
                KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED
            )
        ),
        budget_service=service,
        production_executor_configured=False,
    )

    result = await rehearsal.run(
        operator_id="executor-pilot-1"
    )

    assert result.passed is False
    assert result.budget_state == "reserved"
    assert "pilot_budget_reserved" in result.blockers
    assert await service.get(
        "oom-pilot-v1"
    ) == record
