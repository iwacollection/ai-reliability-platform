import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
)
from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightArtifact,
)
from services.agent_runtime.app.action.safety_models import (
    KubernetesMutationPrecondition,
    KubernetesServerDryRunProof,
    KubernetesWorkloadScope,
    MemoryLimitChange,
    ProductionActionSafetyContract,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INCIDENT_ID = UUID("10000000-0000-4000-8000-000000000101")
CONTRACT_ID = UUID("20000000-0000-4000-8000-000000000202")
WORKLOAD_UID = UUID("30000000-0000-4000-8000-000000000303")
EXECUTION_ID = UUID("40000000-0000-4000-8000-000000000404")
APPROVAL_ID = "50000000-0000-4000-8000-000000000505"
API_URL = "https://kubernetes.test"


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values or (NOW + timedelta(minutes=1),))
        self.last = self.values[-1]

    def __call__(self) -> datetime:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def scope() -> KubernetesWorkloadScope:
    return KubernetesWorkloadScope(
        cluster="production-a",
        namespace="payment",
        name="payment-api",
        container="payment-api",
    )


def canonical_patch() -> str:
    patch = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "payment-api",
            "namespace": "payment",
            "resourceVersion": "500",
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "ai-reliability-platform/safety-contract-id": str(
                            CONTRACT_ID
                        ),
                        "ai-reliability-platform/safety-policy-version": (
                            "oom-memory-increase-v1"
                        ),
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "resources": {
                                "limits": {
                                    "memory": "640Mi",
                                }
                            },
                        }
                    ]
                },
            }
        },
    }
    return json.dumps(
        patch,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def preflight_record() -> PreflightArtifactRecord:
    patch_json = canonical_patch()
    digest = sha256(patch_json.encode("utf-8")).hexdigest()
    precondition = KubernetesMutationPrecondition(
        workload_uid=WORKLOAD_UID,
        resource_version="500",
        generation=9,
    )
    contract = ProductionActionSafetyContract(
        contract_id=CONTRACT_ID,
        incident_id=INCIDENT_ID,
        scope=scope(),
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
            patch_sha256=digest,
        ),
        prepared_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    plan = contract.bind_plan(
        ActionPlan(
            type=ActionType.INCREASE_MEMORY_LIMIT,
            target="payment-api",
            namespace="payment",
            cluster="production-a",
            risk=ActionRisk.MEDIUM,
            metadata={
                "reason": "Bounded OOMKilled pilot remediation",
                "source_pod": "payment-api-abc",
                "preflight_mode": "kubernetes_server_dry_run",
            },
        )
    )
    artifact = KubernetesPreflightArtifact(
        contract=contract,
        plan=plan,
        patch_json=patch_json,
        dry_run_generation=10,
        source_pod_uid="pod-uid",
        source_replicaset_uid="replicaset-uid",
        source_restart_count=5,
    )
    return PreflightArtifactRecord(
        artifact_id=CONTRACT_ID,
        incident_id=INCIDENT_ID,
        idempotency_key="production-executor-test-0001",
        status=PreflightArtifactStatus.APPROVAL_BOUND,
        artifact=artifact,
        approval_id=APPROVAL_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def execution_record(
    record: PreflightArtifactRecord | None = None,
) -> ActionExecutionRecord:
    record = record or preflight_record()
    action = record.artifact.plan.model_copy(deep=True)
    action.approved = True
    return ActionExecutionRecord(
        id=EXECUTION_ID,
        approval_id=APPROVAL_ID,
        incident_id=INCIDENT_ID,
        operator_id="executor-production-test",
        idempotency_key="production-execution-test-0001",
        action=action,
        metadata={
            "source": "action_runtime.resume",
        },
        created_at=NOW,
        started_at=NOW,
        updated_at=NOW,
    )


def deployment_payload(
    *,
    memory: str,
    resource_version: str,
    generation: int,
    annotations: dict[str, str] | None = None,
    uid: str = str(WORKLOAD_UID),
) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "payment-api",
            "namespace": "payment",
            "uid": uid,
            "resourceVersion": resource_version,
            "generation": generation,
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": annotations or {},
                },
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "resources": {
                                "limits": {
                                    "memory": memory,
                                    "cpu": "500m",
                                }
                            },
                        }
                    ]
                },
            }
        },
    }


def desired_annotations() -> dict[str, str]:
    return {
        "ai-reliability-platform/safety-contract-id": str(CONTRACT_ID),
        "ai-reliability-platform/safety-policy-version": (
            "oom-memory-increase-v1"
        ),
    }


__all__ = [
    "API_URL",
    "APPROVAL_ID",
    "NOW",
    "SequenceClock",
    "canonical_patch",
    "deployment_payload",
    "desired_annotations",
    "execution_record",
    "preflight_record",
    "scope",
]
