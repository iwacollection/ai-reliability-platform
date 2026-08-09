from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightArtifact,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.preflight_artifact_store import (
    PreflightArtifactStore,
)
from services.agent_runtime.app.action.production_action_query import (
    ProductionActionQueryService,
)
from services.agent_runtime.app.action.safety_models import (
    KubernetesMutationPrecondition,
    KubernetesServerDryRunProof,
    KubernetesWorkloadScope,
    MemoryLimitChange,
    ProductionActionSafetyContract,
)
from services.agent_runtime.app.approval.manager import (
    ApprovalManager,
)
from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.approval.store import (
    ApprovalStore,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)


NOW = datetime(
    2026,
    8,
    9,
    8,
    0,
    tzinfo=UTC,
)
INCIDENT_ID = UUID(
    "11111111-1111-4111-8111-111111111811"
)
CONTRACT_ID = UUID(
    "22222222-2222-4222-8222-222222222822"
)
WORKLOAD_UID = UUID(
    "33333333-3333-4333-8333-333333333833"
)
APPROVAL_ID = (
    "44444444-4444-4444-8444-444444444844"
)
IDEMPOTENCY_KEY = (
    "query-payment-api-0001"
)


def artifact() -> KubernetesPreflightArtifact:
    scope = KubernetesWorkloadScope(
        cluster="production-a",
        namespace="payment",
        name="payment-api",
        container="payment-api",
    )
    patch_json = (
        '{"kind":"Deployment","memory":"640Mi",'
        '"target":"payment-api"}'
    )
    patch_digest = sha256(
        patch_json.encode(
            "utf-8"
        )
    ).hexdigest()
    precondition = KubernetesMutationPrecondition(
        workload_uid=WORKLOAD_UID,
        resource_version="300",
        generation=7,
    )
    contract = ProductionActionSafetyContract(
        contract_id=CONTRACT_ID,
        incident_id=INCIDENT_ID,
        scope=scope,
        precondition=precondition,
        memory=MemoryLimitChange(
            current_limit="512Mi",
            desired_limit="640Mi",
        ),
        dry_run=KubernetesServerDryRunProof(
            validated_at=NOW,
            workload_uid=WORKLOAD_UID,
            resource_version="300",
            generation=7,
            patch_sha256=patch_digest,
        ),
        prepared_at=NOW,
        expires_at=(
            NOW
            + timedelta(minutes=10)
        ),
    )
    plan = contract.bind_plan(
        ActionPlan(
            type=(
                ActionType.INCREASE_MEMORY_LIMIT
            ),
            target="payment-api",
            namespace="payment",
            cluster="production-a",
            risk=ActionRisk.MEDIUM,
            metadata={
                "reason": (
                    "OOMKilled requires bounded remediation"
                ),
                "source_pod": (
                    "payment-api-abc"
                ),
                "preflight_mode": (
                    "kubernetes_server_dry_run"
                ),
            },
        )
    )
    return KubernetesPreflightArtifact(
        contract=contract,
        plan=plan,
        patch_json=patch_json,
        dry_run_generation=8,
        source_pod_uid="pod-uid",
        source_replicaset_uid=(
            "replica-set-uid"
        ),
        source_restart_count=4,
    )


def services(
    tmp_path: Path,
):
    artifact_service = (
        PreflightArtifactService(
            PreflightArtifactStore(
                tmp_path
                / "preflight_artifacts.db"
            )
        )
    )
    approval_service = ApprovalService(
        ApprovalManager(
            ApprovalStore(
                tmp_path
                / "approvals.db"
            )
        )
    )
    incident_store = IncidentStore(
        tmp_path
        / "incidents.db"
    )
    return (
        artifact_service,
        approval_service,
        incident_store,
    )


async def save_incident(
    incident_store: IncidentStore,
) -> IncidentState:
    incident = IncidentState(
        id=INCIDENT_ID,
        status=IncidentStatus.CONFIRMED,
        reason=(
            "Trusted production remediation is awaiting approval"
        ),
    )
    return await incident_store.save(
        incident
    )


async def prepare_bound_state(
    tmp_path: Path,
    *,
    save_linked_incident: bool = True,
    approval_metadata: dict | None = None,
):
    (
        artifact_service,
        approval_service,
        incident_store,
    ) = services(
        tmp_path
    )
    claim = await artifact_service.claim_prepared(
        artifact(),
        IDEMPOTENCY_KEY,
    )
    expected_metadata = {
        "source": (
            "production_action_preparation"
        ),
        "preflight_artifact_id": str(
            CONTRACT_ID
        ),
        "preflight_idempotency_key": (
            IDEMPOTENCY_KEY
        ),
        "safety_contract_id": str(
            CONTRACT_ID
        ),
        "safety_patch_sha256": (
            claim.record.artifact.contract
            .dry_run.patch_sha256
        ),
        "preparation_operator_id": (
            "test-analyst-operator"
        ),
    }
    approval = await approval_service.create_approval(
        action=claim.record.artifact.plan,
        reason=(
            "Approve bounded production remediation"
        ),
        incident_id=INCIDENT_ID,
        request_id=APPROVAL_ID,
        metadata=(
            expected_metadata
            if approval_metadata is None
            else approval_metadata
        ),
    )
    record = await artifact_service.bind_approval(
        claim.record,
        approval.id,
    )

    incident = None
    if save_linked_incident:
        incident = await save_incident(
            incident_store
        )

    return (
        artifact_service,
        approval_service,
        incident_store,
        record,
        approval,
        incident,
    )


def query_service(
    *,
    artifact_service,
    approval_service,
    incident_store,
    now: datetime,
) -> ProductionActionQueryService:
    return ProductionActionQueryService(
        artifact_service=(
            artifact_service
        ),
        approval_service=(
            approval_service
        ),
        incident_store=incident_store,
        clock=lambda: now,
    )


@pytest.mark.asyncio
async def test_pending_approval_snapshot_has_exact_ttl_and_blocker(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        _,
        _,
        _,
    ) = await prepare_bound_state(
        tmp_path
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == (
        "pending_approval"
    )
    assert result.expired is False
    assert result.remaining_seconds == 300
    assert result.consistency_passed is True
    assert result.consistency_issues == ()
    assert result.execution_eligible is False
    assert result.execution_blockers == (
        "approval_pending",
    )
    assert (
        result.approval_decision_required
        is True
    )
    assert (
        result.replacement_preflight_required
        is False
    )


@pytest.mark.asyncio
async def test_approved_unexpired_snapshot_is_execution_candidate(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        _,
        approval,
        _,
    ) = await prepare_bound_state(
        tmp_path
    )
    approved = await approval_service.approve(
        approval.id
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert approved.status == (
        ApprovalStatus.APPROVED
    )
    assert result is not None
    assert result.phase == (
        "ready_for_execution"
    )
    assert result.consistency_passed is True
    assert result.execution_eligible is True
    assert result.execution_blockers == ()
    assert (
        result.approval_decision_required
        is False
    )


@pytest.mark.asyncio
async def test_expired_query_is_read_only_and_requires_replacement(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        record_before,
        approval_before,
        incident_before,
    ) = await prepare_bound_state(
        tmp_path
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=11)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == (
        "expired_pending_approval"
    )
    assert result.expired is True
    assert result.remaining_seconds == 0
    assert result.execution_eligible is False
    assert "safety_contract_expired" in (
        result.execution_blockers
    )
    assert (
        result.replacement_preflight_required
        is True
    )
    assert (
        result.approval_decision_required
        is False
    )
    assert await artifact_service.get(
        CONTRACT_ID
    ) == record_before
    assert await approval_service.get(
        APPROVAL_ID
    ) == approval_before
    assert await incident_store.get(
        str(INCIDENT_ID)
    ) == incident_before


@pytest.mark.asyncio
async def test_clock_rollback_fails_closed_without_false_ttl(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        _,
        _,
        _,
    ) = await prepare_bound_state(
        tmp_path
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            - timedelta(seconds=1)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == "clock_invalid"
    assert result.clock_valid is False
    assert result.expired is False
    assert result.remaining_seconds == 0
    assert result.execution_eligible is False
    assert "query_clock_invalid" in (
        result.execution_blockers
    )
    assert (
        result.approval_decision_required
        is False
    )
    assert (
        result.replacement_preflight_required
        is False
    )


@pytest.mark.asyncio
async def test_incomplete_binding_is_visible_without_recovery_write(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
    ) = services(
        tmp_path
    )
    claim = await artifact_service.claim_prepared(
        artifact(),
        IDEMPOTENCY_KEY,
    )
    await save_incident(
        incident_store
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=1)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == (
        "approval_binding_incomplete"
    )
    assert result.consistency_passed is False
    assert result.consistency_issues == (
        "artifact_approval_binding_incomplete",
        "artifact_missing_approval_id",
    )
    assert (
        "approval_binding_incomplete"
        in result.execution_blockers
    )
    assert await artifact_service.get(
        CONTRACT_ID
    ) == claim.record


@pytest.mark.asyncio
async def test_missing_incident_is_reported_as_inconsistent(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        _,
        _,
        _,
    ) = await prepare_bound_state(
        tmp_path,
        save_linked_incident=False,
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=1)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == "inconsistent"
    assert result.consistency_issues == (
        "linked_incident_not_found",
    )
    assert "incident_unavailable" in (
        result.execution_blockers
    )


@pytest.mark.asyncio
async def test_tampered_approval_metadata_is_reported(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
        _,
        _,
        _,
    ) = await prepare_bound_state(
        tmp_path,
        approval_metadata={
            "source": "tampered",
        },
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=(
            NOW
            + timedelta(minutes=1)
        ),
    )

    result = await service.get(
        CONTRACT_ID
    )

    assert result is not None
    assert result.phase == "inconsistent"
    assert result.consistency_issues == (
        "approval_artifact_metadata_mismatch",
    )
    assert result.execution_eligible is False


@pytest.mark.asyncio
async def test_unknown_artifact_returns_none(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
    ) = services(
        tmp_path
    )
    service = query_service(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        now=NOW,
    )

    assert await service.get(
        CONTRACT_ID
    ) is None


def test_query_constructor_and_clock_fail_closed(
    tmp_path: Path,
):
    (
        artifact_service,
        approval_service,
        incident_store,
    ) = services(
        tmp_path
    )

    with pytest.raises(
        TypeError,
        match="Artifact service is invalid",
    ):
        ProductionActionQueryService(
            artifact_service=object(),
            approval_service=approval_service,
            incident_store=incident_store,
        )

    service = ProductionActionQueryService(
        artifact_service=artifact_service,
        approval_service=approval_service,
        incident_store=incident_store,
        clock=lambda: datetime(
            2026,
            8,
            9,
            8,
            0,
        ),
    )

    with pytest.raises(
        ValueError,
        match="aware datetime",
    ):
        service._now()

    assert not hasattr(
        service,
        "resolver",
    )
