import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightArtifact,
    KubernetesPreflightPolicy,
    KubernetesPreflightRequest,
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
    ProductionActionPreparationRequest,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.preflight_artifact_store import (
    PreflightArtifactConflictError,
    PreflightArtifactStore,
)
from services.agent_runtime.app.action.production_action_preparation import (
    ProductionActionPreparationConflictError,
    ProductionActionPreparationService,
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


NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
INCIDENT_ID = UUID("11111111-1111-4111-8111-111111111111")
CONTRACT_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKLOAD_UID = UUID("33333333-3333-4333-8333-333333333333")
IDEMPOTENCY_KEY = "prepare-payment-api-0001"


def preflight_request(
    *,
    pod_name: str = "payment-api-abc",
    namespace: str = "payment",
    reason: str = "OOMKilled requires bounded remediation",
) -> KubernetesPreflightRequest:
    return KubernetesPreflightRequest(
        incident_id=INCIDENT_ID,
        cluster="production-a",
        namespace=namespace,
        pod_name=pod_name,
        container="payment-api",
        reason=reason,
    )


def artifact(
    *,
    contract_id: UUID = CONTRACT_ID,
    deployment: str = "payment-api",
    reason: str = "OOMKilled requires bounded remediation",
) -> KubernetesPreflightArtifact:
    scope = KubernetesWorkloadScope(
        cluster="production-a",
        namespace="payment",
        name=deployment,
        container="payment-api",
    )
    patch_json = (
        '{"kind":"Deployment","memory":"640Mi","target":"'
        + deployment
        + '"}'
    )
    digest = sha256(patch_json.encode("utf-8")).hexdigest()
    precondition = KubernetesMutationPrecondition(
        workload_uid=WORKLOAD_UID,
        resource_version="300",
        generation=7,
    )
    contract = ProductionActionSafetyContract(
        contract_id=contract_id,
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
            patch_sha256=digest,
        ),
        prepared_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    plan = contract.bind_plan(
        ActionPlan(
            type=ActionType.INCREASE_MEMORY_LIMIT,
            target=deployment,
            namespace="payment",
            cluster="production-a",
            risk=ActionRisk.MEDIUM,
            metadata={
                "reason": reason,
                "source_pod": "payment-api-abc",
                "preflight_mode": "kubernetes_server_dry_run",
            },
        )
    )
    return KubernetesPreflightArtifact(
        contract=contract,
        plan=plan,
        patch_json=patch_json,
        dry_run_generation=8,
        source_pod_uid="pod-uid",
        source_replicaset_uid="replica-set-uid",
        source_restart_count=4,
    )


def preparation_request(
    *,
    request: KubernetesPreflightRequest | None = None,
    idempotency_key: str = IDEMPOTENCY_KEY,
    approval_reason: str = "Approve bounded OOMKilled memory remediation",
) -> ProductionActionPreparationRequest:
    return ProductionActionPreparationRequest(
        preflight=request or preflight_request(),
        idempotency_key=idempotency_key,
        approval_reason=approval_reason,
    )


def resolver_for(
    result: KubernetesPreflightArtifact,
    calls: list[KubernetesPreflightRequest],
) -> KubernetesPreflightResolver:
    scope = result.contract.scope
    resolver = KubernetesPreflightResolver(
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        policy=KubernetesPreflightPolicy(
            enabled=True,
            allowed_targets=(scope,),
        ),
        bearer_token="test-service-account-token-000001",
    )

    async def prepare(request: KubernetesPreflightRequest):
        calls.append(request)
        return result

    resolver.prepare = prepare
    return resolver


def services(tmp_path: Path):
    artifact_store = PreflightArtifactStore(
        tmp_path / "preflight_artifacts.db"
    )
    artifact_service = PreflightArtifactService(artifact_store)
    approval_service = ApprovalService(
        ApprovalManager(
            ApprovalStore(tmp_path / "approvals.db")
        )
    )
    return artifact_store, artifact_service, approval_service


def coordinator(
    tmp_path: Path,
    *,
    result: KubernetesPreflightArtifact | None = None,
    calls: list[KubernetesPreflightRequest] | None = None,
):
    store, artifact_service, approval_service = services(tmp_path)
    tracked_calls = calls if calls is not None else []
    preparation = ProductionActionPreparationService(
        resolver=resolver_for(result or artifact(), tracked_calls),
        artifact_service=artifact_service,
        approval_service=approval_service,
    )
    return preparation, store, artifact_service, approval_service, tracked_calls


@pytest.mark.asyncio
async def test_store_persists_immutable_artifact_across_instances(tmp_path: Path):
    path = tmp_path / "preflight_artifacts.db"
    first = PreflightArtifactService(PreflightArtifactStore(path))
    claim = await first.claim_prepared(artifact(), IDEMPOTENCY_KEY)
    second = PreflightArtifactService(PreflightArtifactStore(path))
    recovered = await second.get_by_request(INCIDENT_ID, IDEMPOTENCY_KEY)

    assert claim.created is True
    assert recovered == claim.record
    assert recovered.status == PreflightArtifactStatus.PREPARED
    assert recovered.artifact.patch_json == artifact().patch_json


@pytest.mark.asyncio
async def test_same_artifact_claim_is_idempotent_across_instances(tmp_path: Path):
    path = tmp_path / "preflight_artifacts.db"
    first = PreflightArtifactService(PreflightArtifactStore(path))
    second = PreflightArtifactService(PreflightArtifactStore(path))

    results = await asyncio.gather(
        first.claim_prepared(artifact(), IDEMPOTENCY_KEY),
        second.claim_prepared(artifact(), IDEMPOTENCY_KEY),
    )

    assert sum(item.created for item in results) == 1
    assert results[0].record.artifact_id == results[1].record.artifact_id


@pytest.mark.asyncio
async def test_idempotency_key_cannot_bind_another_logical_preflight(
    tmp_path: Path,
):
    service = PreflightArtifactService(
        PreflightArtifactStore(tmp_path / "preflight_artifacts.db")
    )
    await service.claim_prepared(artifact(), IDEMPOTENCY_KEY)

    with pytest.raises(PreflightArtifactConflictError, match="another request"):
        await service.claim_prepared(
            artifact(
                contract_id=UUID("99999999-9999-4999-8999-999999999999"),
                deployment="other-api",
            ),
            IDEMPOTENCY_KEY,
        )


@pytest.mark.asyncio
async def test_approval_binding_is_cas_and_exact_replay(tmp_path: Path):
    service = PreflightArtifactService(
        PreflightArtifactStore(tmp_path / "preflight_artifacts.db")
    )
    claim = await service.claim_prepared(artifact(), IDEMPOTENCY_KEY)
    first = await service.bind_approval(claim.record, "approval-a")
    replay = await service.bind_approval(first, "approval-a")

    assert first.status == PreflightArtifactStatus.APPROVAL_BOUND
    assert replay == first

    with pytest.raises(PreflightArtifactConflictError, match="another Approval"):
        await service.bind_approval(claim.record, "approval-b")


@pytest.mark.asyncio
async def test_preparation_persists_artifact_before_one_deterministic_approval(
    tmp_path: Path,
):
    preparation, store, _, approval_service, calls = coordinator(tmp_path)
    result = await preparation.prepare(preparation_request())

    assert len(calls) == 1
    assert result.artifact_created is True
    assert result.approval_created is True
    assert result.idempotent_replay is False
    assert result.record.status == PreflightArtifactStatus.APPROVAL_BOUND
    assert result.record.approval_id == result.approval.id
    assert result.approval.status == ApprovalStatus.PENDING
    assert result.approval.action.approved is False
    assert result.approval.incident_id == INCIDENT_ID
    assert result.approval.metadata["preflight_artifact_id"] == str(CONTRACT_ID)
    assert result.approval.metadata["safety_contract_id"] == str(CONTRACT_ID)
    assert await store.get(CONTRACT_ID) == result.record
    assert await approval_service.get(result.approval.id) == result.approval


@pytest.mark.asyncio
async def test_exact_replay_skips_resolver_and_creates_no_second_approval(
    tmp_path: Path,
):
    preparation, store, _, approval_service, calls = coordinator(tmp_path)
    first = await preparation.prepare(preparation_request())
    second = await preparation.prepare(preparation_request())

    assert len(calls) == 1
    assert second.idempotent_replay is True
    assert second.record == first.record
    assert second.approval == first.approval
    assert len(await store.list_by_incident(INCIDENT_ID)) == 1
    assert len(await approval_service.list_by_incident(INCIDENT_ID)) == 1


@pytest.mark.asyncio
async def test_concurrent_preparation_creates_one_artifact_and_one_approval(
    tmp_path: Path,
):
    preparation, store, _, approval_service, calls = coordinator(tmp_path)

    results = await asyncio.gather(
        preparation.prepare(preparation_request()),
        preparation.prepare(preparation_request()),
    )

    assert sum(item.artifact_created for item in results) == 1
    assert sum(item.approval_created for item in results) == 1
    assert results[0].record.artifact_id == results[1].record.artifact_id
    assert results[0].approval.id == results[1].approval.id
    assert len(await store.list_by_incident(INCIDENT_ID)) == 1
    assert len(await approval_service.list_by_incident(INCIDENT_ID)) == 1
    assert 1 <= len(calls) <= 2


@pytest.mark.asyncio
async def test_restart_replay_recovers_artifact_and_approval_without_preflight(
    tmp_path: Path,
):
    first, _, _, _, first_calls = coordinator(tmp_path)
    initial = await first.prepare(preparation_request())

    second_calls = []
    restarted, _, _, _, _ = coordinator(tmp_path, calls=second_calls)
    replay = await restarted.prepare(preparation_request())

    assert len(first_calls) == 1
    assert second_calls == []
    assert replay.idempotent_replay is True
    assert replay.record == initial.record
    assert replay.approval == initial.approval


@pytest.mark.asyncio
async def test_crash_window_after_approval_commit_is_recovered(tmp_path: Path):
    preparation, _, artifact_service, approval_service, calls = coordinator(tmp_path)
    claim = await artifact_service.claim_prepared(artifact(), IDEMPOTENCY_KEY)
    approval_id = preparation._approval_id(claim.record)
    approval = await approval_service.create_approval(
        action=claim.record.artifact.plan,
        reason=preparation_request().approval_reason,
        incident_id=INCIDENT_ID,
        request_id=approval_id,
        metadata=preparation._approval_metadata(claim.record),
    )

    recovered = await preparation.prepare(preparation_request())

    assert calls == []
    assert recovered.artifact_created is False
    assert recovered.approval_created is False
    assert recovered.record.approval_id == approval.id
    assert recovered.record.status == PreflightArtifactStatus.APPROVAL_BOUND


@pytest.mark.asyncio
async def test_approval_failure_leaves_safe_prepared_record_and_replay_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    preparation, _, artifact_service, approval_service, calls = coordinator(tmp_path)
    original = approval_service.create_approval
    create_calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        if create_calls == 1:
            raise RuntimeError("simulated approval storage outage")
        return await original(*args, **kwargs)

    monkeypatch.setattr(approval_service, "create_approval", fail_once)

    with pytest.raises(RuntimeError, match="storage outage"):
        await preparation.prepare(preparation_request())

    prepared = await artifact_service.get_by_request(INCIDENT_ID, IDEMPOTENCY_KEY)
    assert prepared is not None
    assert prepared.status == PreflightArtifactStatus.PREPARED
    assert prepared.approval_id is None

    recovered = await preparation.prepare(preparation_request())
    assert len(calls) == 1
    assert create_calls == 2
    assert recovered.record.status == PreflightArtifactStatus.APPROVAL_BOUND


@pytest.mark.asyncio
async def test_conflicting_request_replay_is_rejected_before_resolver(
    tmp_path: Path,
):
    preparation, _, _, _, calls = coordinator(tmp_path)
    await preparation.prepare(preparation_request())

    conflicting = preparation_request(
        request=preflight_request(pod_name="different-pod")
    )
    with pytest.raises(PreflightArtifactConflictError, match="stored request"):
        await preparation.prepare(conflicting)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tampered_deterministic_approval_is_rejected(tmp_path: Path):
    preparation, _, artifact_service, approval_service, calls = coordinator(tmp_path)
    claim = await artifact_service.claim_prepared(artifact(), IDEMPOTENCY_KEY)
    approval_id = preparation._approval_id(claim.record)
    await approval_service.create_approval(
        action=claim.record.artifact.plan,
        reason=preparation_request().approval_reason,
        incident_id=INCIDENT_ID,
        request_id=approval_id,
        metadata={"preflight_artifact_id": "tampered"},
    )

    with pytest.raises(
        ProductionActionPreparationConflictError,
        match="does not match",
    ):
        await preparation.prepare(preparation_request())

    assert calls == []


@pytest.mark.asyncio
async def test_replay_after_human_approval_preserves_binding(tmp_path: Path):
    preparation, _, _, approval_service, calls = coordinator(tmp_path)
    initial = await preparation.prepare(preparation_request())
    approved = await approval_service.approve(initial.approval.id)
    replay = await preparation.prepare(preparation_request())

    assert approved.status == ApprovalStatus.APPROVED
    assert approved.action.approved is True
    assert replay.approval.status == ApprovalStatus.APPROVED
    assert replay.approval.action.approved is True
    assert replay.record.approval_id == approved.id
    assert len(calls) == 1
