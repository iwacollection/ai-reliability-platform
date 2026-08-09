from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.epistemic_guard import (
    EpistemicConclusionGuard,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.probes import (
    ReadOnlyInvestigationProbeExecutor,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.tools.base import (
    BaseTool,
)
from services.agent_runtime.app.tools.kubernetes.change_tool import (
    KubernetesChangeTool,
    KubernetesChangeTopologyError,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesAuthorizationError,
    KubernetesConfigurationError,
    KubernetesTool,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


NOW = datetime(
    2026,
    8,
    11,
    0,
    30,
    tzinfo=UTC,
)

INCIDENT = datetime(
    2026,
    8,
    11,
    0,
    20,
    tzinfo=UTC,
)


def owner(
    kind: str,
    name: str,
    uid: str,
):
    return {
        "apiVersion": (
            "apps/v1"
            if kind
            in {
                "ReplicaSet",
                "Deployment",
            }
            else "v1"
        ),
        "kind": kind,
        "name": name,
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": True,
    }


def replica_set(
    *,
    name: str,
    uid: str,
    revision: int,
    image: str,
    created_at: str,
):
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "namespace": "payment",
            "uid": uid,
            "creationTimestamp": created_at,
            "annotations": {
                "deployment.kubernetes.io/revision": (
                    str(
                        revision
                    )
                )
            },
            "ownerReferences": [
                owner(
                    "Deployment",
                    "payment-api",
                    "deployment-uid",
                )
            ],
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": image,
                        }
                    ]
                }
            }
        },
    }


def deployment():
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "payment-api",
            "namespace": "payment",
            "uid": "deployment-uid",
            "generation": 9,
            "annotations": {
                "deployment.kubernetes.io/revision": "7"
            },
        },
        "spec": {
            "replicas": 4,
            "selector": {
                "matchLabels": {
                    "app": "payment-api"
                }
            },
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "payment-api:v7",
                        }
                    ]
                }
            },
        },
        "status": {
            "observedGeneration": 9,
            "updatedReplicas": 4,
            "readyReplicas": 2,
            "availableReplicas": 2,
            "unavailableReplicas": 2,
        },
    }


def handler(
    request: httpx.Request,
) -> httpx.Response:
    path = request.url.path

    if path.endswith(
        "/pods/payment-api"
    ):
        return httpx.Response(
            200,
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "payment-api",
                    "namespace": "payment",
                    "uid": "pod-uid",
                    "ownerReferences": [
                        owner(
                            "ReplicaSet",
                            "payment-api-7b9f",
                            "rs-current-uid",
                        )
                    ],
                },
            },
            request=request,
        )

    if path.endswith(
        "/replicasets/payment-api-7b9f"
    ):
        return httpx.Response(
            200,
            json=replica_set(
                name="payment-api-7b9f",
                uid="rs-current-uid",
                revision=7,
                image="payment-api:v7",
                created_at=(
                    "2026-08-11T00:15:00Z"
                ),
            ),
            request=request,
        )

    if path.endswith(
        "/deployments/payment-api"
    ):
        return httpx.Response(
            200,
            json=deployment(),
            request=request,
        )

    if path.endswith(
        "/replicasets"
    ):
        assert (
            request.url.params.get(
                "labelSelector"
            )
            == "app=payment-api"
        )

        return httpx.Response(
            200,
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSetList",
                "metadata": {},
                "items": [
                    replica_set(
                        name="payment-api-6aaa",
                        uid="rs-old-uid",
                        revision=6,
                        image="payment-api:v6",
                        created_at=(
                            "2026-08-10T22:00:00Z"
                        ),
                    ),
                    replica_set(
                        name="payment-api-7b9f",
                        uid="rs-current-uid",
                        revision=7,
                        image="payment-api:v7",
                        created_at=(
                            "2026-08-11T00:15:00Z"
                        ),
                    ),
                ],
            },
            request=request,
        )

    return httpx.Response(
        404,
        json={},
        request=request,
    )


@pytest.mark.asyncio
async def test_kubernetes_change_tool_resolves_bounded_deployment_history():
    transport = httpx.MockTransport(
        handler
    )

    async with httpx.AsyncClient(
        transport=transport,
    ) as client:
        kubernetes = KubernetesTool(
            api_url=(
                "https://kubernetes.test"
            ),
            bearer_token="unit-token",
            cluster_name="production-a",
            allow_dry_run_fallback=False,
            client=client,
            clock=lambda: NOW,
        )

        result = await (
            KubernetesChangeTool(
                kubernetes
            )
            .execute(
                target="payment-api",
                namespace="payment",
                cluster="production-a",
                incident_time=(
                    INCIDENT.isoformat()
                ),
            )
        )

    assert result[
        "source"
    ] == "kubernetes_change"

    assert result[
        "mode"
    ] == "read_only"

    assert result[
        "production_signal"
    ] is True

    data = result[
        "data"
    ]

    assert data[
        "owner_chain_verified"
    ] is True

    assert data[
        "deployment_name"
    ] == "payment-api"

    assert data[
        "revision_before"
    ] == 6

    assert data[
        "revision_after"
    ] == 7

    assert data[
        "revision_changed"
    ] is True

    assert data[
        "image_before"
    ] == "app=payment-api:v6"

    assert data[
        "image_after"
    ] == "app=payment-api:v7"

    assert data[
        "image_changed"
    ] is True

    assert data[
        "rollout_offset_seconds"
    ] == 300.0

    assert data[
        "generation"
    ] == 9

    assert data[
        "observed_generation"
    ] == 9

    assert data[
        "replicas_desired"
    ] == 4

    assert data[
        "replicas_ready"
    ] == 2


@pytest.mark.asyncio
async def test_change_tool_fails_closed_when_pod_is_not_deployment_owned():
    def bad_handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "payment-api",
                    "uid": "pod-uid",
                    "ownerReferences": [
                        owner(
                            "StatefulSet",
                            "payment-api",
                            "statefulset-uid",
                        )
                    ],
                },
            },
            request=request,
        )

    transport = httpx.MockTransport(
        bad_handler
    )

    async with httpx.AsyncClient(
        transport=transport,
    ) as client:
        kubernetes = KubernetesTool(
            api_url=(
                "https://kubernetes.test"
            ),
            bearer_token="unit-token",
            client=client,
            allow_dry_run_fallback=False,
        )

        with pytest.raises(
            KubernetesChangeTopologyError,
        ):
            await (
                KubernetesChangeTool(
                    kubernetes
                )
                .execute(
                    target="payment-api",
                    namespace="payment",
                )
            )


@pytest.mark.asyncio
async def test_change_tool_has_no_mock_fallback_without_kubernetes_api():
    kubernetes = KubernetesTool(
        api_url=None,
        allow_dry_run_fallback=True,
    )

    kubernetes.api_url = None

    with pytest.raises(
        KubernetesConfigurationError,
    ):
        await (
            KubernetesChangeTool(
                kubernetes
            )
            .execute(
                target="payment-api",
                namespace="payment",
            )
        )


@pytest.mark.asyncio
async def test_change_tool_fails_closed_on_authorization_error():
    def forbidden(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            403,
            json={},
            request=request,
        )

    transport = httpx.MockTransport(
        forbidden
    )

    async with httpx.AsyncClient(
        transport=transport,
    ) as client:
        kubernetes = KubernetesTool(
            api_url=(
                "https://kubernetes.test"
            ),
            bearer_token="unit-token",
            client=client,
            allow_dry_run_fallback=False,
        )

        with pytest.raises(
            KubernetesAuthorizationError,
        ):
            await (
                KubernetesChangeTool(
                    kubernetes
                )
                .execute(
                    target="payment-api",
                    namespace="payment",
                )
            )


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="CrashLoopBackOff",
        alert_message="payment-api started failing after rollout",
        event_occurred_at=INCIDENT,
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


class StaticChangeTool(
    BaseTool
):
    @property
    def name(
        self,
    ) -> str:
        return "kubernetes_change"

    @property
    def is_available(
        self,
    ) -> bool:
        return True

    async def execute(
        self,
        **kwargs,
    ):
        return {
            "success": True,
            "source": "kubernetes_change",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": NOW.isoformat(),
            "data": {
                "owner_chain_verified": True,
                "workload_kind": "Deployment",
                "deployment_name": "payment-api",
                "revision_before": 6,
                "revision_after": 7,
                "revision_changed": True,
                "image_before": "app=payment-api:v6",
                "image_after": "app=payment-api:v7",
                "image_changed": True,
                "rollout_started_at": "2026-08-11T00:15:00Z",
                "generation": 9,
                "observed_generation": 9,
                "replicas_desired": 4,
                "replicas_updated": 4,
                "replicas_ready": 2,
                "replicas_available": 2,
                "replicas_unavailable": 2,
                "history_complete": True,
            },
        }


def change_manager() -> ToolManager:
    registry = ToolRegistry()
    registry.register(
        StaticChangeTool()
    )

    return ToolManager(
        registry
    )


@pytest.mark.asyncio
async def test_change_probe_is_capability_gated_and_normalized():
    tools = change_manager()

    context = SimpleNamespace(
        tools=tools,
        trace=None,
    )

    executor = (
        ReadOnlyInvestigationProbeExecutor()
    )

    available = executor.available_probes(
        context
    )

    assert (
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
        in available
    )

    evidence = await executor.collect(
        context,
        scope(),
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    )

    assert evidence.trusted is True
    assert evidence.source == (
        "kubernetes_change"
    )

    assert evidence.facts[
        "revision_before"
    ] == 6

    assert evidence.facts[
        "revision_after"
    ] == 7

    assert evidence.facts[
        "image_changed"
    ] is True

    assert evidence.facts[
        "rollout_offset_seconds"
    ] == 300.0

    assert evidence.facts[
        "recent_rollout_before_incident"
    ] is True


def test_default_state_does_not_enable_change_probe():
    state = InvestigationState(
        scope=scope()
    )

    assert (
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
        not in state.available_probes
    )

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=state.scope,
            state=state,
        )
    )

    state_json = prompt.rsplit(
        "State:\n",
        1,
    )[
        1
    ]

    assert (
        '"kubernetes_workload_change"'
        not in state_json
    )


def test_change_enabled_state_exposes_change_probe_and_causal_discipline():
    state = InvestigationState(
        scope=scope(),
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
        ],
    )

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=state.scope,
            state=state,
        )
    )

    assert (
        "kubernetes_workload_change"
        in prompt
    )

    assert (
        "not by itself proof"
        in prompt
    )

    assert (
        "Do not claim that a workload change CAUSED"
        in prompt
    )


class NeverReasoner(
    BaseInvestigationReasoner
):
    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        raise RuntimeError(
            "not needed"
        )


class AvailableExecutor:
    def available_probes(
        self,
        context,
    ):
        return [
            InvestigationProbe.KUBERNETES_POD_STATE,
            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
        ]

    async def collect(
        self,
        context,
        scope,
        probe,
    ):
        raise AssertionError(
            "collect should not run"
        )


def test_coordinator_resolves_capability_probe_set_without_tool_call():
    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=NeverReasoner(),
            probe_executor=AvailableExecutor(),
        )
    )

    available = coordinator._available_probes(
        SimpleNamespace()
    )

    assert available == [
        InvestigationProbe.KUBERNETES_POD_STATE,
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    ]


def change_evidence(
    evidence_id: str = "change",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        probe=(
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
        source="kubernetes_change",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts={
            "revision_before": 6,
            "revision_after": 7,
            "image_changed": True,
        },
    )


def log_evidence(
    evidence_id: str = "logs",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        probe=(
            InvestigationProbe
            .KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ),
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts={
            "log_excerpt": (
                "panic: incompatible schema after image rollout"
            ),
        },
    )


def sufficient_change_decision(
    evidence_ids,
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause=(
                    "image rollout introduced an incompatible startup schema"
                ),
                confidence=0.9,
                supporting_evidence_ids=list(
                    evidence_ids
                ),
                conflicting_evidence_ids=[],
                missing_evidence=[],
                optional_evidence=[],
            )
        ],
        rationale_summary=(
            "bounded change and runtime evidence support the hypothesis"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        next_probe=None,
        conclusion=InvestigationConclusion(
            root_cause=(
                "image rollout introduced an incompatible startup schema"
            ),
            confidence=0.9,
            evidence_ids=list(
                evidence_ids
            ),
        ),
    )


def test_guard_rejects_change_evidence_as_solo_causal_proof():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            change_evidence()
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=(
                sufficient_change_decision(
                    [
                        "change"
                    ]
                )
            ),
            state=state,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "ChangeEvidenceRequiresIndependentSupport"
    )


def test_guard_allows_change_when_independent_positive_support_is_cited():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            change_evidence(),
            log_evidence(),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=(
                sufficient_change_decision(
                    [
                        "change",
                        "logs",
                    ]
                )
            ),
            state=state,
        )
    )

    assert result.allowed is True
