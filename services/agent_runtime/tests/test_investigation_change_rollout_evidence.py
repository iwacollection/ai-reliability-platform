from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
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


NOW = datetime(
    2026,
    8,
    11,
    10,
    30,
    tzinfo=UTC,
)

INCIDENT = datetime(
    2026,
    8,
    11,
    10,
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
):
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "namespace": "payment",
            "uid": uid,
            "creationTimestamp": (
                "2026-08-11T10:15:00Z"
                if revision == 7
                else "2026-08-10T22:00:00Z"
            ),
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
            "conditions": [
                {
                    "type": "Progressing",
                    "status": "False",
                    "reason": "ProgressDeadlineExceeded",
                },
                {
                    "type": "Available",
                    "status": "False",
                    "reason": "MinimumReplicasUnavailable",
                },
                {
                    "type": "ReplicaFailure",
                    "status": "True",
                    "reason": "FailedCreate",
                },
            ],
        },
    }


def event(
    *,
    uid: str,
    kind: str,
    name: str,
    event_type: str,
    reason: str,
    message: str,
    timestamp: str,
):
    return {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": {
            "creationTimestamp": timestamp,
        },
        "involvedObject": {
            "uid": uid,
            "kind": kind,
            "name": name,
        },
        "type": event_type,
        "reason": reason,
        "message": message,
        "lastTimestamp": timestamp,
    }


def object_handler(
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
                "apiVersion": "apps/v1",
                "kind": "ReplicaSetList",
                "metadata": {},
                "items": [
                    replica_set(
                        name="payment-api-6aaa",
                        uid="rs-old-uid",
                        revision=6,
                        image="payment-api:v6",
                    ),
                    replica_set(
                        name="payment-api-7b9f",
                        uid="rs-current-uid",
                        revision=7,
                        image="payment-api:v7",
                    ),
                ],
            },
            request=request,
        )

    raise AssertionError(
        f"unexpected object path: {request.url}"
    )


def handler_with_events(
    request: httpx.Request,
) -> httpx.Response:
    if request.url.path.endswith(
        "/events"
    ):
        selector = request.url.params.get(
            "fieldSelector"
        )

        if selector == (
            "involvedObject.uid=pod-uid"
        ):
            items = [
                event(
                    uid="pod-uid",
                    kind="Pod",
                    name="payment-api",
                    event_type="Warning",
                    reason="BackOff",
                    message=(
                        "Back-off restarting failed container app"
                    ),
                    timestamp=(
                        "2026-08-11T10:19:00Z"
                    ),
                )
            ]

        elif selector == (
            "involvedObject.uid=rs-current-uid"
        ):
            items = [
                event(
                    uid="rs-current-uid",
                    kind="ReplicaSet",
                    name="payment-api-7b9f",
                    event_type="Warning",
                    reason="FailedCreate",
                    message=(
                        "Error creating pod during rollout"
                    ),
                    timestamp=(
                        "2026-08-11T10:18:00Z"
                    ),
                )
            ]

        elif selector == (
            "involvedObject.uid=deployment-uid"
        ):
            items = [
                event(
                    uid="deployment-uid",
                    kind="Deployment",
                    name="payment-api",
                    event_type="Normal",
                    reason="ScalingReplicaSet",
                    message=(
                        "Scaled up replica set payment-api-7b9f"
                    ),
                    timestamp=(
                        "2026-08-11T10:15:30Z"
                    ),
                )
            ]

        else:
            items = []

        return httpx.Response(
            200,
            json={
                "apiVersion": "v1",
                "kind": "EventList",
                "metadata": {},
                "items": items,
            },
            request=request,
        )

    return object_handler(
        request
    )


def handler_events_forbidden(
    request: httpx.Request,
) -> httpx.Response:
    if request.url.path.endswith(
        "/events"
    ):
        return httpx.Response(
            403,
            json={},
            request=request,
        )

    return object_handler(
        request
    )


@pytest.mark.asyncio
async def test_change_tool_adds_rollout_conditions_and_incident_window_events():
    transport = httpx.MockTransport(
        handler_with_events
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
            incident_time=(
                INCIDENT.isoformat()
            ),
        )

    data = result[
        "data"
    ]

    assert (
        "Progressing=False:ProgressDeadlineExceeded"
        in data[
            "rollout_condition_summary"
        ]
    )

    assert (
        "ReplicaFailure=True:FailedCreate"
        in data[
            "rollout_condition_summary"
        ]
    )

    assert data[
        "rollout_failure_signal"
    ] is True

    assert (
        "ProgressDeadlineExceeded"
        in data[
            "rollout_failure_reason"
        ]
    )

    assert data[
        "generation_observed"
    ] is True

    assert data[
        "rollout_complete"
    ] is False

    assert data[
        "events_status"
    ] == "complete"

    assert data[
        "recent_event_count"
    ] == 3

    assert data[
        "recent_warning_count"
    ] == 2

    assert (
        "BackOff"
        in data[
            "recent_event_reasons"
        ]
    )

    assert (
        "FailedCreate"
        in data[
            "recent_event_reasons"
        ]
    )

    assert (
        "ScalingReplicaSet"
        in data[
            "recent_event_reasons"
        ]
    )

    assert (
        "Back-off restarting failed container"
        in data[
            "recent_event_summary"
        ]
    )


@pytest.mark.asyncio
async def test_event_rbac_denial_degrades_only_event_enrichment():
    transport = httpx.MockTransport(
        handler_events_forbidden
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
            incident_time=(
                INCIDENT.isoformat()
            ),
        )

    assert result[
        "success"
    ] is True

    data = result[
        "data"
    ]

    assert data[
        "revision_after"
    ] == 7

    assert data[
        "rollout_failure_signal"
    ] is True

    assert data[
        "events_status"
    ] == "unavailable"

    assert data[
        "events_error_code"
    ] == "authorization_denied"

    assert data[
        "recent_event_count"
    ] == 0


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message=(
            "payment-api is restarting after rollout"
        ),
        event_occurred_at=INCIDENT,
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


def test_change_probe_normalizes_rollout_and_event_facts():
    executor = (
        ReadOnlyInvestigationProbeExecutor()
    )

    result = {
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
            "rollout_started_at": (
                "2026-08-11T10:15:00+00:00"
            ),
            "generation": 9,
            "observed_generation": 9,
            "replicas_desired": 4,
            "replicas_updated": 4,
            "replicas_ready": 2,
            "replicas_available": 2,
            "replicas_unavailable": 2,
            "history_complete": True,
            "rollout_condition_summary": (
                "Progressing=False:ProgressDeadlineExceeded;"
                "Available=False:MinimumReplicasUnavailable;"
                "ReplicaFailure=True:FailedCreate"
            ),
            "generation_observed": True,
            "rollout_complete": False,
            "rollout_failure_signal": True,
            "rollout_failure_reason": (
                "ProgressDeadlineExceeded;FailedCreate"
            ),
            "events_status": "complete",
            "events_error_code": None,
            "recent_event_count": 3,
            "recent_warning_count": 2,
            "recent_event_reasons": (
                "BackOff;FailedCreate;ScalingReplicaSet"
            ),
            "recent_event_summary": (
                "Pod/payment-api Warning BackOff"
            ),
        },
    }

    evidence = (
        executor
        ._normalize_kubernetes_change(
            scope=scope(),
            probe=(
                InvestigationProbe
                .KUBERNETES_WORKLOAD_CHANGE
            ),
            result=result,
        )
    )

    assert evidence.trusted is True

    assert evidence.facts[
        "rollout_failure_signal"
    ] is True

    assert (
        "ProgressDeadlineExceeded"
        in evidence.facts[
            "rollout_condition_summary"
        ]
    )

    assert evidence.facts[
        "events_status"
    ] == "complete"

    assert len(
        evidence.facts
    ) <= 32

    assert evidence.facts[
        "recent_warning_count"
    ] == 2

    assert (
        "FailedCreate"
        in evidence.facts[
            "recent_event_reasons"
        ]
    )


def test_reasoner_prompt_documents_rollout_failure_and_event_semantics():
    state = InvestigationState(
        scope=scope(),
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            (
                InvestigationProbe
                .KUBERNETES_WORKLOAD_CHANGE
            ),
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
        "ProgressDeadlineExceeded"
        in prompt
    )

    assert (
        "ReplicaFailure"
        in prompt
    )

    assert (
        "Kubernetes Event summaries"
        in prompt
    )

    assert (
        "not by itself proof"
        in prompt
    )

    assert (
        "temporal change evidence alone is not proof"
        in prompt
    )
