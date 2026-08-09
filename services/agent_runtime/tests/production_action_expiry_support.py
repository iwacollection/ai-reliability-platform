from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightArtifact,
    KubernetesPreflightPolicy,
    KubernetesPreflightResolver,
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
from services.agent_runtime.app.action.production_action_guard import (
    ProductionActionExpiryGuard,
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
    12,
    0,
    tzinfo=UTC,
)
INCIDENT_ID = UUID(
    "10000000-0000-4000-8000-000000000101"
)
ARTIFACT_ID = UUID(
    "20000000-0000-4000-8000-000000000202"
)
WORKLOAD_UID = UUID(
    "30000000-0000-4000-8000-000000000303"
)
APPROVAL_ID = (
    "40000000-0000-4000-8000-000000000404"
)
PREFLIGHT_KEY = (
    "expiry-payment-api-0001"
)


class MutableClock:
    def __init__(
        self,
        value: datetime,
    ) -> None:
        self.value = value
        self._sequence: list[datetime] = []

    def __call__(
        self,
    ) -> datetime:
        if self._sequence:
            return self._sequence.pop(
                0
            )
        return self.value

    def set(
        self,
        value: datetime,
    ) -> None:
        self.value = value
        self._sequence = []

    def set_sequence(
        self,
        *values: datetime,
    ) -> None:
        self._sequence = list(
            values
        )
        if values:
            self.value = values[-1]


def workload_scope() -> KubernetesWorkloadScope:
    return KubernetesWorkloadScope(
        cluster="production-a",
        namespace="payment",
        name="payment-api",
        container="payment-api",
    )


def preflight_artifact() -> KubernetesPreflightArtifact:
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
        resource_version="500",
        generation=9,
    )
    contract = ProductionActionSafetyContract(
        contract_id=ARTIFACT_ID,
        incident_id=INCIDENT_ID,
        scope=workload_scope(),
        precondition=precondition,
        memory=MemoryLimitChange(
            current_limit="512Mi",
            desired_limit="640Mi",
        ),
        dry_run=KubernetesServerDryRunProof(
            validated_at=NOW,
            workload_uid=WORKLOAD_UID,
            resource_version="500",
            generation=9,
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
                    "OOMKilled bounded production remediation"
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
        dry_run_generation=10,
        source_pod_uid="pod-uid",
        source_replicaset_uid=(
            "replica-set-uid"
        ),
        source_restart_count=6,
    )


def resolver() -> KubernetesPreflightResolver:
    return KubernetesPreflightResolver(
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        policy=KubernetesPreflightPolicy(
            enabled=True,
            allowed_targets=(
                workload_scope(),
            ),
        ),
        bearer_token=(
            "test-service-account-token-000001"
        ),
    )


def isolated_services(
    tmp_path: Path,
    clock: MutableClock,
):
    artifact_service = (
        PreflightArtifactService(
            PreflightArtifactStore(
                tmp_path
                / "preflight_artifacts.db"
            )
        )
    )
    guard = ProductionActionExpiryGuard(
        artifact_service=artifact_service,
        clock=clock,
    )
    approval_service = ApprovalService(
        ApprovalManager(
            ApprovalStore(
                tmp_path
                / "approvals.db"
            ),
            transition_guard=guard,
        )
    )
    incident_store = IncidentStore(
        tmp_path
        / "incidents.db"
    )
    return (
        artifact_service,
        guard,
        approval_service,
        incident_store,
    )


async def persist_prepared_workflow(
    *,
    artifact_service: PreflightArtifactService,
    approval_service: ApprovalService,
    incident_store: IncidentStore,
):
    claim = await artifact_service.claim_prepared(
        preflight_artifact(),
        PREFLIGHT_KEY,
    )
    metadata = {
        "source": (
            "production_action_preparation"
        ),
        "preflight_artifact_id": str(
            ARTIFACT_ID
        ),
        "preflight_idempotency_key": (
            PREFLIGHT_KEY
        ),
        "safety_contract_id": str(
            ARTIFACT_ID
        ),
        "safety_patch_sha256": (
            claim.record.artifact.contract
            .dry_run.patch_sha256
        ),
        "preparation_operator_id": (
            "analyst-expiry-test"
        ),
    }
    approval = await approval_service.create_approval(
        action=claim.record.artifact.plan,
        reason=(
            "Approve bounded production remediation"
        ),
        incident_id=INCIDENT_ID,
        request_id=APPROVAL_ID,
        metadata=metadata,
    )
    record = await artifact_service.bind_approval(
        claim.record,
        approval.id,
    )
    incident = await incident_store.save(
        IncidentState(
            id=INCIDENT_ID,
            status=IncidentStatus.CONFIRMED,
            reason=(
                "Production remediation is awaiting approval"
            ),
        )
    )
    return record, approval, incident


def generic_action() -> ActionPlan:
    return ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="legacy-payment-api",
        namespace="payment",
        cluster="production-a",
        risk=ActionRisk.MEDIUM,
        metadata={
            "reason": "Legacy compatible Action",
        },
    )


__all__ = [
    "APPROVAL_ID",
    "ARTIFACT_ID",
    "INCIDENT_ID",
    "MutableClock",
    "NOW",
    "PREFLIGHT_KEY",
    "generic_action",
    "isolated_services",
    "persist_prepared_workflow",
    "preflight_artifact",
    "resolver",
]
