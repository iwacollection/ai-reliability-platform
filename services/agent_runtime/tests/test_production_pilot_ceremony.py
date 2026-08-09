import asyncio
import json
import sqlite3

from datetime import timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetStore,
)
from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.action.execution_store import (
    ActionExecutionStore,
)
from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT,
    ProductionPilotActivationChecklist,
    ProductionPilotCeremonyRecord,
)
from services.agent_runtime.app.action.production_pilot_ceremony_service import (
    ProductionPilotCeremonyError,
    ProductionPilotCeremonyService,
)
from services.agent_runtime.app.action.production_pilot_ceremony_store import (
    ProductionPilotCeremonyConflictError,
    ProductionPilotCeremonyStore,
)
from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
)
from services.agent_runtime.app.action.production_pilot_rehearsal import (
    ProductionPilotRehearsalService,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
    MutableClock,
    NOW,
    isolated_services,
    persist_prepared_workflow,
)
from services.agent_runtime.tests.test_production_pilot import (
    control,
)


def checklist(
    executor_operator_id: str = "executor-pilot-1",
) -> ProductionPilotActivationChecklist:
    return ProductionPilotActivationChecklist(
        executor_operator_id=executor_operator_id,
        exact_target_verified=True,
        separate_credentials_verified=True,
        rollback_reviewed=True,
        monitoring_ready=True,
        kill_switch_tested=True,
        budget_available_verified=True,
        runbook_reviewed=True,
        acknowledgement=(
            PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT
        ),
    )


def ceremony_record(
    *,
    reviewer_operator_id: str = "approver-pilot-1",
    idempotency_key: str = "ceremony-pilot-0001",
) -> ProductionPilotCeremonyRecord:
    values = {
        "ceremony_id": "50000000-0000-4000-8000-000000000505",
        "pilot_id": "oom-pilot-v1",
        "change_ticket": "CHG-6001",
        "runbook_version": "oom-runbook-v1",
        "approval_id": APPROVAL_ID,
        "incident_id": "10000000-0000-4000-8000-000000000101",
        "artifact_id": "20000000-0000-4000-8000-000000000202",
        "contract_id": "20000000-0000-4000-8000-000000000202",
        "patch_sha256": "a" * 64,
        "reviewer_operator_id": reviewer_operator_id,
        "executor_operator_id": "executor-pilot-1",
        "idempotency_key": idempotency_key,
        "checklist": checklist().model_dump(mode="json"),
        "readiness_checked_at": (
            NOW + timedelta(minutes=1)
        ).isoformat(),
        "created_at": (
            NOW + timedelta(minutes=1)
        ).isoformat(),
        "expires_at": (
            NOW + timedelta(minutes=10)
        ).isoformat(),
    }
    digest = sha256(
        json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return ProductionPilotCeremonyRecord(
        **values,
        evidence_sha256=digest,
    )


def test_checklist_requires_every_attestation_and_exact_acknowledgement():
    with pytest.raises(ValidationError):
        ProductionPilotActivationChecklist.model_validate(
            {
                **checklist().model_dump(),
                "monitoring_ready": False,
            }
        )

    with pytest.raises(ValidationError):
        ProductionPilotActivationChecklist(
            **{
                **checklist().model_dump(),
                "acknowledgement": "ACKNOWLEDGED",
            }
        )


def test_record_rejects_tampered_evidence_digest():
    original = ceremony_record()

    with pytest.raises(ValidationError, match="digest"):
        ProductionPilotCeremonyRecord.model_validate(
            {
                **original.model_dump(),
                "change_ticket": "CHG-OTHER",
            }
        )


def test_record_activation_is_terminal_digest_bound_and_idempotent():
    ready = ceremony_record()
    activated_at = NOW + timedelta(minutes=2)
    activated = ready.activate(
        execution_id="60000000-0000-4000-8000-000000000606",
        execution_idempotency_key="execute-pilot-0001",
        activated_at=activated_at,
    )

    assert activated.status.value == "activated"
    assert activated.execution_id == UUID(
        "60000000-0000-4000-8000-000000000606"
    )
    assert activated.execution_idempotency_key == "execute-pilot-0001"
    assert activated.activation_sha256 == (
        activated.expected_activation_sha256()
    )
    assert activated.activate(
        execution_id=activated.execution_id,
        execution_idempotency_key="execute-pilot-0001",
        activated_at=NOW + timedelta(minutes=3),
    ) is activated

    with pytest.raises(ValueError, match="another execution"):
        activated.activate(
            execution_id="70000000-0000-4000-8000-000000000707",
            execution_idempotency_key="execute-pilot-0002",
            activated_at=activated_at,
        )

    with pytest.raises(ValidationError, match="activation digest"):
        type(activated).model_validate(
            {
                **activated.model_dump(),
                "execution_idempotency_key": "tampered-key",
            }
        )


@pytest.mark.asyncio
async def test_store_exact_replay_is_cross_instance_idempotent(tmp_path):
    db_path = tmp_path / "pilot_ceremony.db"
    first_store = ProductionPilotCeremonyStore(db_path)
    second_store = ProductionPilotCeremonyStore(db_path)
    record = ceremony_record()

    created = await first_store.claim_ready(record)
    replay = await second_store.claim_ready(record)

    assert created.created is True
    assert replay.created is False
    assert replay.record == record


@pytest.mark.asyncio
async def test_store_activation_is_cross_instance_cas_and_queryable(tmp_path):
    db_path = tmp_path / "pilot_ceremony.db"
    first_store = ProductionPilotCeremonyStore(db_path)
    second_store = ProductionPilotCeremonyStore(db_path)
    ready = ceremony_record()
    await first_store.claim_ready(ready)

    first = await first_store.activate(
        ceremony_id=ready.ceremony_id,
        execution_id="60000000-0000-4000-8000-000000000606",
        execution_idempotency_key="execute-pilot-0001",
        activated_at=NOW + timedelta(minutes=2),
    )
    replay = await second_store.activate(
        ceremony_id=ready.ceremony_id,
        execution_id="60000000-0000-4000-8000-000000000606",
        execution_idempotency_key="execute-pilot-0001",
        activated_at=NOW + timedelta(minutes=3),
    )

    assert first.applied is True
    assert replay.applied is False
    assert replay.record == first.record
    assert await second_store.get_by_execution(
        first.record.execution_id
    ) == first.record

    with pytest.raises(ProductionPilotCeremonyConflictError):
        await second_store.activate(
            ceremony_id=ready.ceremony_id,
            execution_id="70000000-0000-4000-8000-000000000707",
            execution_idempotency_key="execute-pilot-0002",
            activated_at=NOW + timedelta(minutes=3),
        )


def test_store_migrates_legacy_table_with_execution_index(tmp_path):
    db_path = tmp_path / "pilot_ceremony.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE production_pilot_ceremonies
            (
                ceremony_id TEXT PRIMARY KEY,
                pilot_id TEXT NOT NULL UNIQUE,
                approval_id TEXT NOT NULL UNIQUE,
                incident_id TEXT NOT NULL,
                reviewer_operator_id TEXT NOT NULL,
                executor_operator_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                record_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = ProductionPilotCeremonyStore(db_path)
    connection = sqlite3.connect(store.db_path)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(production_pilot_ceremonies)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(production_pilot_ceremonies)"
            ).fetchall()
        }
    finally:
        connection.close()

    assert "execution_id" in columns
    assert "idx_production_pilot_ceremony_execution" in indexes


@pytest.mark.asyncio
async def test_store_cross_instance_concurrency_allows_one_binding(tmp_path):
    db_path = tmp_path / "pilot_ceremony.db"
    stores = [
        ProductionPilotCeremonyStore(db_path)
        for _ in range(8)
    ]

    async def claim(index: int) -> str:
        try:
            result = await stores[index].claim_ready(
                ceremony_record(
                    reviewer_operator_id=f"approver-pilot-{index + 1}",
                    idempotency_key=f"ceremony-pilot-{index + 1:04d}",
                )
            )
            return "created" if result.created else "replay"
        except ProductionPilotCeremonyConflictError:
            return "conflict"

    outcomes = await asyncio.gather(
        *(claim(index) for index in range(len(stores)))
    )

    assert outcomes.count("created") == 1
    assert outcomes.count("conflict") == 7


async def prepared_service(tmp_path):
    clock = MutableClock(
        NOW + timedelta(minutes=1)
    )
    (
        artifact_service,
        _,
        approval_service,
        incident_store,
    ) = isolated_services(tmp_path, clock)
    await persist_prepared_workflow(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
    )
    await approval_service.approve(
        APPROVAL_ID,
        operator_id="approver-workflow-1",
        idempotency_key="approve-ceremony-0001",
        reason="Approve bounded OOMKilled canary",
    )
    pilot_control = control()
    budget_service = ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path / "pilot_budget.db"
        ),
        clock=clock,
    )
    rehearsal = ProductionPilotRehearsalService(
        control=pilot_control,
        budget_service=budget_service,
        production_executor_configured=False,
    )
    service = ProductionPilotCeremonyService(
        store=ProductionPilotCeremonyStore(
            tmp_path / "pilot_ceremony.db"
        ),
        control=pilot_control,
        rehearsal=rehearsal,
        budget_service=budget_service,
        approval_service=approval_service,
        artifact_service=artifact_service,
        clock=clock,
    )
    return service, budget_service


@pytest.mark.asyncio
async def test_service_records_zero_write_evidence_and_exact_replay(tmp_path):
    service, budget_service = await prepared_service(tmp_path)
    arguments = {
        "approval_id": APPROVAL_ID,
        "reviewer_operator_id": "approver-pilot-1",
        "idempotency_key": "ceremony-pilot-0001",
        "checklist": checklist(),
    }

    created = await service.record_checklist(**arguments)
    replay = await service.record_checklist(**arguments)

    assert created.created is True
    assert replay.created is False
    assert replay.record == created.record
    assert created.record.zero_write_verified is True
    assert created.record.external_call_count == 0
    assert created.record.evidence_sha256 == (
        created.record.expected_evidence_sha256()
    )
    assert await budget_service.get("oom-pilot-v1") is None

    with pytest.raises(ProductionPilotCeremonyError, match="conflicts"):
        await service.record_checklist(
            **{
                **arguments,
                "idempotency_key": "ceremony-pilot-0002",
            }
        )


@pytest.mark.asyncio
async def test_service_activates_only_a_matching_running_execution(tmp_path):
    service, budget_service = await prepared_service(tmp_path)
    ready = await service.record_checklist(
        approval_id=APPROVAL_ID,
        reviewer_operator_id="approver-pilot-1",
        idempotency_key="ceremony-pilot-0001",
        checklist=checklist(),
    )
    approval = await service.approval_service.get(APPROVAL_ID)
    assert approval is not None
    execution_service = ActionExecutionService(
        ActionExecutionStore(tmp_path / "action_executions.db")
    )
    claim = await execution_service.claim(
        approval_id=approval.id,
        incident_id=approval.incident_id,
        operator_id="executor-pilot-1",
        idempotency_key="execute-pilot-0001",
        action=approval.action,
        metadata={
            "execution_mode": "kubernetes_production",
        },
    )
    preflight_record = await service.artifact_service.get_by_approval_id(
        approval.id
    )
    assert preflight_record is not None
    service.control._switch_reader = lambda _: (
        KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
    )

    activated = await service.activate_for_execution(
        execution=claim.execution,
        preflight_record=preflight_record,
    )
    replay = await service.activate_for_execution(
        execution=claim.execution,
        preflight_record=preflight_record,
    )

    assert activated.applied is True
    assert replay.applied is False
    assert activated.record.ceremony_id == ready.record.ceremony_id
    assert activated.record.execution_id == claim.execution.id
    assert activated.record.executor_operator_id == claim.execution.operator_id
    assert await budget_service.get("oom-pilot-v1") is None


@pytest.mark.asyncio
async def test_service_rejects_execution_operator_mismatch_without_activation(
    tmp_path,
):
    service, _ = await prepared_service(tmp_path)
    await service.record_checklist(
        approval_id=APPROVAL_ID,
        reviewer_operator_id="approver-pilot-1",
        idempotency_key="ceremony-pilot-0001",
        checklist=checklist(),
    )
    approval = await service.approval_service.get(APPROVAL_ID)
    assert approval is not None
    claim = await ActionExecutionService(
        ActionExecutionStore(tmp_path / "action_executions.db")
    ).claim(
        approval_id=approval.id,
        incident_id=approval.incident_id,
        operator_id="executor-other",
        idempotency_key="execute-pilot-other-0001",
        action=approval.action,
    )
    preflight_record = await service.artifact_service.get_by_approval_id(
        approval.id
    )
    assert preflight_record is not None
    service.control._switch_reader = lambda _: (
        KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
    )

    with pytest.raises(ProductionPilotCeremonyError, match="binding"):
        await service.activate_for_execution(
            execution=claim.execution,
            preflight_record=preflight_record,
        )

    persisted = await service.get_by_approval(APPROVAL_ID)
    assert persisted is not None
    assert persisted.status.value == "ready"


@pytest.mark.asyncio
async def test_service_rejects_same_reviewer_and_executor(tmp_path):
    service, _ = await prepared_service(tmp_path)

    with pytest.raises(ProductionPilotCeremonyError, match="different"):
        await service.record_checklist(
            approval_id=APPROVAL_ID,
            reviewer_operator_id="executor-pilot-1",
            idempotency_key="ceremony-pilot-0001",
            checklist=checklist(),
        )
