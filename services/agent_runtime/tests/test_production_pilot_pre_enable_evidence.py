from datetime import timedelta
import sqlite3

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.action.execution_store import (
    ActionExecutionStore,
)
from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
    ProductionPilotReadinessService,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetStore,
)
from services.agent_runtime.app.action.production_pilot_ceremony_service import (
    ProductionPilotCeremonyService,
)
from services.agent_runtime.app.action.production_pilot_ceremony_store import (
    ProductionPilotCeremonyStore,
)
from services.agent_runtime.app.action.production_pilot_crash_rehearsal import (
    ProductionPilotCrashRecoveryRehearsalService,
)
from services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (
    PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT,
    ProductionPilotPreEnableEvidenceConflictError,
    ProductionPilotPreEnableEvidenceError,
    ProductionPilotPreEnableEvidencePack,
    ProductionPilotPreEnableEvidenceService,
    ProductionPilotPreEnableSignOffRequest,
)
from services.agent_runtime.app.action.production_pilot_rehearsal import (
    ProductionPilotRehearsalService,
)
from services.agent_runtime.app.verification.service import (
    VerificationService,
)
from services.agent_runtime.app.verification.store import (
    VerificationStore,
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
    execution_config,
    pilot_config,
)
from services.agent_runtime.tests.test_production_pilot_ceremony import (
    checklist,
)


EXECUTOR_ID = "test-executor-operator"


def sqlite_logical_snapshot(
    tmp_path,
) -> dict[str, object]:
    """Read every SQLite schema and row without depending on WAL layout."""

    snapshot: dict[str, object] = {}
    for path in sorted(
        tmp_path.glob("*.db")
    ):
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            schema = tuple(
                connection.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                ).fetchall()
            )
            snapshot[
                f"{path.name}:schema"
            ] = schema
            table_names = tuple(
                row[1]
                for row in schema
                if row[0] == "table"
            )
            for table_name in table_names:
                quoted_name = table_name.replace(
                    '"',
                    '""',
                )
                rows = connection.execute(
                    f'SELECT * FROM "{quoted_name}"'
                ).fetchall()
                snapshot[
                    f"{path.name}:{table_name}"
                ] = tuple(
                    sorted(
                        rows,
                        key=repr,
                    )
                )
        finally:
            connection.close()
    return snapshot


async def pre_enable_environment(
    tmp_path,
):
    clock = MutableClock(
        NOW + timedelta(minutes=1)
    )
    (
        artifact_service,
        _,
        approval_service,
        incident_store,
    ) = isolated_services(
        tmp_path,
        clock,
    )
    await persist_prepared_workflow(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
    )
    await approval_service.approve(
        APPROVAL_ID,
        operator_id="approver-evidence-1",
        idempotency_key="approve-evidence-0001",
        reason="Approve bounded OOMKilled Pilot evidence",
    )

    pilot_control = control(
        pilot=pilot_config(
            authorized_operator_ids=(
                EXECUTOR_ID,
            )
        ),
        execution=execution_config(
            enabled=False
        ),
    )
    budget_service = ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path / "pilot_budget.db"
        ),
        clock=clock,
    )
    rehearsal_service = ProductionPilotRehearsalService(
        control=pilot_control,
        budget_service=budget_service,
        production_executor_configured=False,
    )
    ceremony_service = ProductionPilotCeremonyService(
        store=ProductionPilotCeremonyStore(
            tmp_path / "pilot_ceremony.db"
        ),
        control=pilot_control,
        rehearsal=rehearsal_service,
        budget_service=budget_service,
        approval_service=approval_service,
        artifact_service=artifact_service,
        clock=clock,
    )
    await ceremony_service.record_checklist(
        approval_id=APPROVAL_ID,
        reviewer_operator_id="approver-ceremony-1",
        idempotency_key="ceremony-evidence-0001",
        checklist=checklist(
            EXECUTOR_ID
        ),
    )

    action_execution_service = ActionExecutionService(
        ActionExecutionStore(
            tmp_path / "action_executions.db"
        )
    )
    verification_service = VerificationService(
        VerificationStore(
            tmp_path / "verifications.db"
        )
    )
    evidence_service = ProductionPilotPreEnableEvidenceService(
        readiness_service=ProductionPilotReadinessService(
            control=pilot_control,
            production_executor_configured=False,
        ),
        rehearsal_service=rehearsal_service,
        crash_rehearsal_service=(
            ProductionPilotCrashRecoveryRehearsalService()
        ),
        ceremony_service=ceremony_service,
        budget_service=budget_service,
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        action_execution_service=action_execution_service,
        verification_service=verification_service,
    )
    return {
        "service": evidence_service,
        "clock": clock,
        "control": pilot_control,
        "ceremony": ceremony_service,
        "budget": budget_service,
        "artifact": artifact_service,
        "approval": approval_service,
        "incident": incident_store,
        "action_execution": action_execution_service,
        "verification": verification_service,
    }


def sign_off_request(
    evidence_sha256: str,
) -> ProductionPilotPreEnableSignOffRequest:
    return ProductionPilotPreEnableSignOffRequest(
        expected_evidence_sha256=(
            evidence_sha256
        ),
        acknowledgement=(
            PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT
        ),
    )


@pytest.mark.asyncio
async def test_evidence_pack_requires_exact_safe_pre_enable_state(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )

    evidence = await environment["service"].get(
        APPROVAL_ID
    )

    assert evidence is not None
    assert evidence.ready_for_sign_off is True
    assert evidence.evidence_blockers == ()
    assert evidence.artifact_state == "approval_bound"
    assert evidence.approval_state == "approved"
    assert evidence.incident_state == "confirmed"
    assert evidence.ceremony_state == "ready"
    assert evidence.budget_state == "available"
    assert evidence.action_execution_state == "not_created"
    assert evidence.verification_state == "not_created"
    assert evidence.contract_clock_state == "valid"
    assert evidence.ceremony_clock_state == "valid"
    assert evidence.kill_switch_state == "engaged"
    assert evidence.production_execution_enabled is False
    assert evidence.production_executor_configured is False
    assert evidence.bindings_consistent is True
    assert evidence.executor_allowlisted is True
    assert evidence.reviewer_executor_separated is True
    assert evidence.approval_executor_separated is True
    assert evidence.enablement_rehearsal_passed is True
    assert evidence.crash_recovery_rehearsal_passed is True
    assert evidence.crash_recovery_checkpoint_count == 13
    assert evidence.authorizes_enablement is False
    assert evidence.authorizes_execution is False
    assert evidence.automatic_resume_allowed is False


@pytest.mark.asyncio
async def test_sign_off_is_digest_bound_zero_write_and_exact_replay(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )
    service = environment["service"]
    evidence = await service.get(
        APPROVAL_ID
    )
    assert evidence is not None
    request = sign_off_request(
        evidence.evidence_sha256
    )

    before = sqlite_logical_snapshot(
        tmp_path
    )
    first = await service.sign_off(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    second = await service.sign_off(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    after = sqlite_logical_snapshot(
        tmp_path
    )

    assert first == second
    assert first.sign_off_passed is True
    assert first.persisted is False
    assert first.storage_write_count == 0
    assert first.durable_claim_created is False
    assert first.budget_reservation_count == 0
    assert first.external_call_count == 0
    assert first.kubernetes_call_count == 0
    assert first.production_executor_call_count == 0
    assert first.verification_call_count == 0
    assert first.real_write_attempted is False
    assert first.authorizes_enablement is False
    assert first.authorizes_execution is False
    assert before == after


@pytest.mark.asyncio
async def test_sign_off_rejects_stale_digest_and_wrong_executor(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )
    service = environment["service"]
    evidence = await service.get(
        APPROVAL_ID
    )
    assert evidence is not None

    with pytest.raises(
        ProductionPilotPreEnableEvidenceConflictError,
        match="changed",
    ):
        await service.sign_off(
            approval_id=APPROVAL_ID,
            operator_id=EXECUTOR_ID,
            request=sign_off_request(
                "0" * 64
            ),
        )

    with pytest.raises(
        ProductionPilotPreEnableEvidenceError,
        match="exact reviewed Executor",
    ):
        await service.sign_off(
            approval_id=APPROVAL_ID,
            operator_id="test-admin-operator",
            request=sign_off_request(
                evidence.evidence_sha256
            ),
        )


@pytest.mark.asyncio
async def test_existing_claim_or_disengaged_switch_blocks_sign_off(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )
    service = environment["service"]
    approval = await environment["approval"].get(
        APPROVAL_ID
    )
    assert approval is not None

    await environment["action_execution"].claim(
        approval_id=approval.id,
        incident_id=approval.incident_id,
        operator_id=EXECUTOR_ID,
        idempotency_key="pre-enable-existing-claim-0001",
        action=approval.action,
    )
    claimed = await service.get(
        APPROVAL_ID
    )
    assert claimed is not None
    assert claimed.ready_for_sign_off is False
    assert (
        "action_execution_already_exists"
        in claimed.evidence_blockers
    )

    environment["control"]._switch_reader = (
        lambda _: KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
    )
    unsafe = await service.get(
        APPROVAL_ID
    )
    assert unsafe is not None
    assert "kill_switch_must_be_engaged" in (
        unsafe.evidence_blockers
    )


@pytest.mark.asyncio
async def test_expiry_and_feature_gate_enablement_fail_closed(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )
    service = environment["service"]

    environment["control"]._clock = lambda: (
        NOW + timedelta(hours=2)
    )
    expired = await service.get(
        APPROVAL_ID
    )
    assert expired is not None
    assert expired.ready_for_sign_off is False
    assert "safety_contract_expired" in expired.evidence_blockers
    assert "ceremony_expired" in expired.evidence_blockers
    assert "pilot_not_ready_for_enablement" in (
        expired.evidence_blockers
    )


@pytest.mark.asyncio
async def test_evidence_digest_rejects_tampering(
    tmp_path,
):
    environment = await pre_enable_environment(
        tmp_path
    )
    evidence = await environment["service"].get(
        APPROVAL_ID
    )
    assert evidence is not None
    values = evidence.model_dump(
        mode="json"
    )
    values["desired_memory_limit"] = "4096Mi"

    with pytest.raises(
        ValidationError,
        match="evidence digest is invalid",
    ):
        ProductionPilotPreEnableEvidencePack.model_validate(
            values
        )


def test_sign_off_requires_exact_acknowledgement():
    with pytest.raises(
        ValidationError,
        match="acknowledgement is invalid",
    ):
        ProductionPilotPreEnableSignOffRequest(
            expected_evidence_sha256="a" * 64,
            acknowledgement="ACKNOWLEDGED",
        )
