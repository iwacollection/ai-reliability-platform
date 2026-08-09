from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

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
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.tools.kubernetes.change_tool import (
    KubernetesChangeTool,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
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
    10,
    50,
    tzinfo=UTC,
)

INCIDENT = datetime(
    2026,
    8,
    11,
    10,
    45,
    tzinfo=UTC,
)


def owner(
    kind: str,
    name: str,
    uid: str,
):
    return {
        "kind": kind,
        "name": name,
        "uid": uid,
        "controller": True,
    }


def replica_set(
    *,
    name: str,
    uid: str,
    revision: int,
    configmap: str,
    secret: str,
    checksum: str,
):
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "uid": uid,
            "creationTimestamp": (
                "2026-08-11T10:40:00Z"
                if revision == 8
                else "2026-08-10T22:00:00Z"
            ),
            "annotations": {
                "deployment.kubernetes.io/revision": str(
                    revision
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
                "metadata": {
                    "annotations": {
                        "checksum/config": checksum,
                        "team": "payments",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": (
                                f"payment-api:v{revision}"
                            ),
                            "envFrom": [
                                {
                                    "configMapRef": {
                                        "name": configmap
                                    }
                                },
                                {
                                    "secretRef": {
                                        "name": secret
                                    }
                                },
                            ],
                            "env": [
                                {
                                    "name": "FEATURE_MODE",
                                    "valueFrom": {
                                        "configMapKeyRef": {
                                            "name": configmap,
                                            "key": "FEATURE_MODE",
                                        }
                                    },
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "runtime-config",
                            "configMap": {
                                "name": configmap
                            },
                        },
                        {
                            "name": "runtime-secret",
                            "secret": {
                                "secretName": secret
                            },
                        },
                    ],
                },
            }
        },
    }


def deployment():
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "payment-api",
            "uid": "deployment-uid",
            "generation": 10,
            "annotations": {
                "deployment.kubernetes.io/revision": "8"
            },
        },
        "spec": {
            "replicas": 3,
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
                            "image": "payment-api:v8",
                        }
                    ]
                }
            },
        },
        "status": {
            "observedGeneration": 10,
            "updatedReplicas": 3,
            "readyReplicas": 2,
            "availableReplicas": 2,
            "unavailableReplicas": 1,
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
                    "uid": "pod-uid",
                    "ownerReferences": [
                        owner(
                            "ReplicaSet",
                            "payment-api-8aaa",
                            "rs-current-uid",
                        )
                    ],
                },
            },
            request=request,
        )

    if path.endswith(
        "/replicasets/payment-api-8aaa"
    ):
        return httpx.Response(
            200,
            json=replica_set(
                name="payment-api-8aaa",
                uid="rs-current-uid",
                revision=8,
                configmap="payment-config-v2",
                secret="payment-secret-v2",
                checksum="new-checksum-value",
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

    if (
        path.endswith(
            "/replicasets"
        )
        and "labelSelector"
        in request.url.params
    ):
        return httpx.Response(
            200,
            json={
                "items": [
                    replica_set(
                        name="payment-api-7aaa",
                        uid="rs-old-uid",
                        revision=7,
                        configmap="payment-config-v1",
                        secret="payment-secret-v1",
                        checksum="old-checksum-value",
                    ),
                    replica_set(
                        name="payment-api-8aaa",
                        uid="rs-current-uid",
                        revision=8,
                        configmap="payment-config-v2",
                        secret="payment-secret-v2",
                        checksum="new-checksum-value",
                    ),
                ],
                "metadata": {},
            },
            request=request,
        )

    if path.endswith(
        "/configmaps/payment-config-v2"
    ):
        return httpx.Response(
            200,
            json={
                "metadata": {
                    "name": "payment-config-v2",
                    "uid": "configmap-uid-v2",
                    "resourceVersion": "99018",
                },
                "data": {
                    "FEATURE_MODE": "dangerous-value-that-must-not-leak"
                },
            },
            request=request,
        )

    raise AssertionError(
        f"unexpected request: {request.url}"
    )


@pytest.mark.asyncio
async def test_config_view_compares_template_refs_without_secret_reads_or_config_values():
    seen_paths = []

    def recording_handler(
        request: httpx.Request,
    ) -> httpx.Response:
        seen_paths.append(
            request.url.path
        )

        return handler(
            request
        )

    transport = httpx.MockTransport(
        recording_handler
    )

    async with httpx.AsyncClient(
        transport=transport,
    ) as client:
        kubernetes = KubernetesTool(
            api_url="https://kubernetes.test",
            bearer_token="unit-token",
            cluster_name="production-a",
            allow_dry_run_fallback=False,
            client=client,
            clock=lambda: NOW,
        )

        result = await KubernetesChangeTool(
            kubernetes
        ).execute(
            target="payment-api",
            namespace="payment",
            cluster="production-a",
            incident_time=INCIDENT.isoformat(),
            view="config",
        )

    data = result[
        "data"
    ]

    assert data[
        "configmap_refs_before"
    ] == "payment-config-v1"

    assert data[
        "configmap_refs_after"
    ] == "payment-config-v2"

    assert data[
        "configmap_refs_changed"
    ] is True

    assert data[
        "secret_refs_before"
    ] == "payment-secret-v1"

    assert data[
        "secret_refs_after"
    ] == "payment-secret-v2"

    assert data[
        "secret_refs_changed"
    ] is True

    assert data[
        "config_annotation_changed"
    ] is True

    assert data[
        "current_configmap_metadata_status"
    ] == "complete"

    assert (
        "payment-config-v2:rv=99018"
        in data[
            "current_configmap_metadata_summary"
        ]
    )

    assert data[
        "secret_content_queried"
    ] is False

    assert data[
        "configmap_content_exposed"
    ] is False

    assert not any(
        "/secrets/"
        in path
        for path in seen_paths
    )

    serialized = str(
        result
    )

    assert (
        "dangerous-value-that-must-not-leak"
        not in serialized
    )


class StaticConfigChangeTool:
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
        *,
        view="workload",
        **kwargs,
    ):
        if view != "config":
            raise AssertionError(
                "focused test expects config view"
            )

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
                "temporal_basis": (
                    "workload_template_config_change"
                ),
                "revision_before": 7,
                "revision_after": 8,
                "configmap_refs_before": "payment-config-v1",
                "configmap_refs_after": "payment-config-v2",
                "configmap_refs_changed": True,
                "configmap_refs_added": "payment-config-v2",
                "configmap_refs_removed": "payment-config-v1",
                "secret_refs_before": "payment-secret-v1",
                "secret_refs_after": "payment-secret-v2",
                "secret_refs_changed": True,
                "secret_refs_added": "payment-secret-v2",
                "secret_refs_removed": "payment-secret-v1",
                "config_annotation_keys_before": "checksum/config",
                "config_annotation_keys_after": "checksum/config",
                "config_annotation_fingerprint_before": "oldhash",
                "config_annotation_fingerprint_after": "newhash",
                "config_annotation_changed": True,
                "current_configmap_metadata_status": "complete",
                "current_configmap_metadata_summary": (
                    "payment-config-v2:rv=99018:uid=configmap-uid-v2"
                ),
                "current_configmap_metadata_error": None,
                "secret_content_queried": False,
                "configmap_content_exposed": False,
            },
        }


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message=(
            "payment-api restarts after configuration rollout"
        ),
        event_occurred_at=INCIDENT,
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


@pytest.mark.asyncio
async def test_config_probe_is_capability_gated_and_normalized_as_separate_evidence():
    registry = ToolRegistry()
    registry.register(
        StaticConfigChangeTool()
    )

    manager = ToolManager(
        registry
    )

    context = SimpleNamespace(
        tools=manager,
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

    assert (
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
        in available
    )

    evidence = await executor.collect(
        context,
        scope(),
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE,
    )

    assert evidence.source == (
        "kubernetes_change"
    )

    assert evidence.trusted is True

    assert evidence.facts[
        "configmap_refs_changed"
    ] is True

    assert evidence.facts[
        "secret_refs_changed"
    ] is True

    assert evidence.facts[
        "config_annotation_changed"
    ] is True

    assert evidence.facts[
        "secret_content_queried"
    ] is False

    assert len(
        evidence.facts
    ) <= 32


def test_default_state_still_does_not_enable_change_or_config_probes():
    state = InvestigationState(
        scope=scope()
    )

    assert (
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
        not in state.available_probes
    )

    assert (
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
        not in state.available_probes
    )


def test_reasoner_prompt_documents_config_safety_and_causality():
    state = InvestigationState(
        scope=scope(),
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            InvestigationProbe.KUBERNETES_CONFIG_CHANGE,
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
        "kubernetes_config_change"
        in prompt
    )

    assert (
        "Secret content is never queried"
        in prompt
    )

    assert (
        "ConfigMap data values are never exposed"
        in prompt
    )

    assert (
        "resourceVersion alone does not prove"
        in prompt
    )


def config_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="config",
        probe=(
            InvestigationProbe
            .KUBERNETES_CONFIG_CHANGE
        ),
        source="kubernetes_change",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts={
            "configmap_refs_changed": True,
            "config_annotation_changed": True,
        },
    )


def log_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="logs",
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
                "panic: missing required FEATURE_MODE after config rollout"
            ),
        },
    )


def decision(
    evidence_ids,
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause=(
                    "configuration rollout removed required FEATURE_MODE"
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
            "configuration evidence supports the candidate cause"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        next_probe=None,
        conclusion=InvestigationConclusion(
            root_cause=(
                "configuration rollout removed required FEATURE_MODE"
            ),
            confidence=0.9,
            evidence_ids=list(
                evidence_ids
            ),
        ),
    )


def test_guard_rejects_config_change_as_solo_causal_proof():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            config_evidence()
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                [
                    "config"
                ]
            ),
            state=state,
        )
    )

    assert result.allowed is False

    assert (
        result.code
        == "ChangeEvidenceRequiresIndependentSupport"
    )


def test_guard_allows_config_change_with_independent_runtime_support():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            config_evidence(),
            log_evidence(),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                [
                    "config",
                    "logs",
                ]
            ),
            state=state,
        )
    )

    assert result.allowed is True
