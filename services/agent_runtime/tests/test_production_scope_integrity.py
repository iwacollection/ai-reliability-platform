from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from common.domain.raw_event import RawEvent
from services.gateway.app.parser.factory import (
    create_parser_registry,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.probes import (
    ReadOnlyInvestigationProbeExecutor,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesConfigurationError,
    KubernetesTool,
)


INCIDENT_TIME = datetime(
    2026,
    8,
    11,
    2,
    30,
    tzinfo=UTC,
)

CLUSTER = "prod-sg-17"
NAMESPACE = "printing-control"
POD = "printer-session-api-abc123"

OTHER_CLUSTER = "prod-us-03"
OTHER_NAMESPACE = "fleet-edge"
OTHER_POD = "device-gateway-xyz789"


def alertmanager_payload(
    *,
    cluster: str,
    namespace: str,
    pod: str,
) -> dict:
    return {
        "receiver": "production",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PodRestartHigh",
                    "severity": "critical",
                    "cluster": cluster,
                    "namespace": namespace,
                    "pod": pod,
                },
                "annotations": {
                    "summary": (
                        "pod restart rate is elevated"
                    )
                },
                "startsAt": (
                    INCIDENT_TIME.isoformat()
                ),
            }
        ],
    }


def parse_event(
    *,
    cluster: str = CLUSTER,
    namespace: str = NAMESPACE,
    pod: str = POD,
):
    parser = (
        create_parser_registry()
        .get(
            "alertmanager"
        )
    )

    return parser.parse(
        RawEvent(
            source="alertmanager",
            payload=alertmanager_payload(
                cluster=cluster,
                namespace=namespace,
                pod=pod,
            ),
            headers={},
        )
    )


class TerminalReasoner(
    BaseInvestigationReasoner
):
    def __init__(
        self,
    ) -> None:
        self.scopes = []

    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        self.scopes.append(
            scope
        )

        return InvestigationDecision(
            hypotheses=[
                {
                    "hypothesis_id": "scope-check",
                    "cause": (
                        "scope integrity test has no RCA"
                    ),
                    "confidence": 0.1,
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": [],
                    "missing_evidence": [
                        "root-cause evidence"
                    ],
                    "optional_evidence": [],
                }
            ],
            rationale_summary=(
                "scope integrity test terminates without probes"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason
                .INSUFFICIENT_EVIDENCE
            ),
            next_probe=None,
            conclusion=None,
        )


class NeverProbeExecutor:
    def __init__(
        self,
    ) -> None:
        self.calls = 0

    def available_probes(
        self,
        context,
    ):
        return [
            InvestigationProbe.KUBERNETES_POD_STATE,
        ]

    async def collect(
        self,
        context,
        scope,
        probe,
    ):
        self.calls += 1
        raise AssertionError(
            "terminal scope test must not collect evidence"
        )


class RecordingTools:
    def __init__(
        self,
    ) -> None:
        self.calls = []

    async def call(
        self,
        name,
        context=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "name": name,
                "kwargs": kwargs,
            }
        )

        if name == "kubernetes":
            if (
                kwargs.get(
                    "action"
                )
                == "previous_logs"
            ):
                return {
                    "success": True,
                    "source": "kubernetes",
                    "mode": "read_only",
                    "production_signal": True,
                    "observed_at": (
                        INCIDENT_TIME.isoformat()
                    ),
                    "cluster": kwargs.get(
                        "cluster"
                    ),
                    "data": {
                        "container_name": "app",
                        "previous": True,
                        "line_count": 1,
                        "truncated": False,
                        "redaction_count": 0,
                        "excerpt": (
                            "safe test log"
                        ),
                    },
                }

            return {
                "success": True,
                "source": "kubernetes",
                "mode": "read_only",
                "production_signal": True,
                "observed_at": (
                    INCIDENT_TIME.isoformat()
                ),
                "cluster": kwargs.get(
                    "cluster"
                ),
                "data": {
                    "phase": "Running",
                    "ready": True,
                    "scheduled": True,
                    "oom_killed": False,
                    "containers": [],
                },
            }

        if name == "prometheus":
            return {
                "success": True,
                "source": "prometheus",
                "mode": "read_only",
                "production_signal": True,
                "observed_at": (
                    INCIDENT_TIME.isoformat()
                ),
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {},
                            "value": [
                                (
                                    INCIDENT_TIME
                                    .timestamp()
                                ),
                                "1",
                            ],
                        }
                    ],
                },
            }

        raise AssertionError(
            f"unexpected tool: {name}"
        )


def test_gateway_parser_preserves_non_demo_production_scope():
    event = parse_event()

    assert len(
        event.resources
    ) == 1

    resource = event.resources[
        0
    ]

    assert resource.name == POD
    assert resource.namespace == NAMESPACE
    assert resource.cluster == CLUSTER

    serialized = str(
        event.model_dump(
            mode="json"
        )
    )

    assert "payment-api-6df78" not in serialized
    assert '"payment"' not in serialized


@pytest.mark.asyncio
async def test_parser_to_investigation_scope_preserves_exact_scope():
    event = parse_event()

    reasoner = TerminalReasoner()
    probes = NeverProbeExecutor()

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=probes,
            utc_clock=lambda: INCIDENT_TIME,
        )
    )

    result = await coordinator.investigate(
        SimpleNamespace(
            event=event,
            metadata={},
            tools=None,
        )
    )

    assert result.scope.resource == POD
    assert result.scope.namespace == NAMESPACE
    assert result.scope.cluster == CLUSTER

    assert len(
        reasoner.scopes
    ) == 1

    assert reasoner.scopes[
        0
    ] == result.scope

    assert probes.calls == 0


@pytest.mark.asyncio
async def test_pod_state_probe_forwards_cluster_namespace_and_resource():
    tools = RecordingTools()

    context = SimpleNamespace(
        tools=tools,
        trace=None,
    )

    scope = InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="restart rate elevated",
        event_occurred_at=INCIDENT_TIME,
        resource=POD,
        namespace=NAMESPACE,
        cluster=CLUSTER,
    )

    await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            context,
            scope,
            InvestigationProbe.KUBERNETES_POD_STATE,
        )
    )

    assert tools.calls == [
        {
            "name": "kubernetes",
            "kwargs": {
                "action": "describe",
                "resource": "pod",
                "target": POD,
                "namespace": NAMESPACE,
                "cluster": CLUSTER,
            },
        }
    ]


@pytest.mark.asyncio
async def test_previous_logs_probe_forwards_cluster_namespace_and_resource():
    tools = RecordingTools()

    context = SimpleNamespace(
        tools=tools,
        trace=None,
    )

    scope = InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="restart rate elevated",
        event_occurred_at=INCIDENT_TIME,
        resource=POD,
        namespace=NAMESPACE,
        cluster=CLUSTER,
    )

    await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            context,
            scope,
            (
                InvestigationProbe
                .KUBERNETES_PREVIOUS_CONTAINER_LOGS
            ),
        )
    )

    assert tools.calls == [
        {
            "name": "kubernetes",
            "kwargs": {
                "action": "previous_logs",
                "resource": "pod",
                "target": POD,
                "namespace": NAMESPACE,
                "cluster": CLUSTER,
            },
        }
    ]


@pytest.mark.asyncio
async def test_prometheus_scope_contains_exact_cluster_namespace_and_resource():
    tools = RecordingTools()

    context = SimpleNamespace(
        tools=tools,
        trace=None,
    )

    scope = InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="restart rate elevated",
        event_occurred_at=INCIDENT_TIME,
        resource=POD,
        namespace=NAMESPACE,
        cluster=CLUSTER,
    )

    await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            context,
            scope,
            (
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
        )
    )

    query = tools.calls[
        0
    ][
        "kwargs"
    ][
        "query"
    ]

    assert (
        f'pod="{POD}"'
        in query
    )

    assert (
        f'namespace="{NAMESPACE}"'
        in query
    )

    assert (
        f'cluster="{CLUSTER}"'
        in query
    )

    assert "payment-api" not in query
    assert 'namespace="payment"' not in query


@pytest.mark.asyncio
async def test_kubernetes_tool_rejects_cross_cluster_request_before_http():
    http_calls = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        http_calls.append(
            request
        )

        return httpx.Response(
            500,
            request=request,
        )

    transport = httpx.MockTransport(
        handler
    )

    async with httpx.AsyncClient(
        transport=transport,
    ) as client:
        tool = KubernetesTool(
            api_url=(
                "https://sg-cluster.invalid"
            ),
            cluster_name=CLUSTER,
            bearer_token="unit-token",
            allow_dry_run_fallback=False,
            client=client,
            clock=lambda: INCIDENT_TIME,
        )

        with pytest.raises(
            KubernetesConfigurationError,
            match=(
                "Requested cluster does not match "
                "configured Kubernetes cluster"
            ),
        ):
            await tool.execute(
                action="describe",
                resource="pod",
                target=OTHER_POD,
                namespace=OTHER_NAMESPACE,
                cluster=OTHER_CLUSTER,
            )

    assert http_calls == []


@pytest.mark.asyncio
async def test_two_cluster_scopes_do_not_bleed_between_parsed_events():
    first = parse_event(
        cluster=CLUSTER,
        namespace=NAMESPACE,
        pod=POD,
    )

    second = parse_event(
        cluster=OTHER_CLUSTER,
        namespace=OTHER_NAMESPACE,
        pod=OTHER_POD,
    )

    first_resource = (
        first.resources[
            0
        ]
    )

    second_resource = (
        second.resources[
            0
        ]
    )

    assert (
        first_resource.cluster,
        first_resource.namespace,
        first_resource.name,
    ) == (
        CLUSTER,
        NAMESPACE,
        POD,
    )

    assert (
        second_resource.cluster,
        second_resource.namespace,
        second_resource.name,
    ) == (
        OTHER_CLUSTER,
        OTHER_NAMESPACE,
        OTHER_POD,
    )

    assert (
        first_resource.cluster
        != second_resource.cluster
    )

    assert (
        first_resource.namespace
        != second_resource.namespace
    )

    assert (
        first_resource.name
        != second_resource.name
    )
