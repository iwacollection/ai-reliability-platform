from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.evidence_time import (
    InvestigationEvidenceTimePolicy,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
)
from services.agent_runtime.app.investigation.probes import (
    InvestigationProbeResponseError,
    ReadOnlyInvestigationProbeExecutor,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)


INCIDENT_TIME = datetime(
    2026,
    8,
    10,
    4,
    0,
    tzinfo=UTC,
)


class NeverReasoner(
    BaseInvestigationReasoner
):
    async def decide(
        self,
        scope,
        state,
    ):
        raise RuntimeError(
            "stop after scope capture"
        )


def build_scope():
    return InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="Pod restarted",
        event_occurred_at=INCIDENT_TIME,
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


class RecordingTools:
    def __init__(
        self,
        response,
    ):
        self.response = response
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

        return self.response


@pytest.mark.asyncio
async def test_coordinator_carries_event_occurred_at_into_scope():
    context = SimpleNamespace(
        event=SimpleNamespace(
            header=SimpleNamespace(
                occurred_at=INCIDENT_TIME,
            ),
            signal=SimpleNamespace(
                name="PodOOMKilled",
                message="Pod restarted",
            ),
            resources=[
                SimpleNamespace(
                    name="payment-api",
                    namespace="payment",
                    cluster="production-a",
                )
            ],
        ),
        metadata={},
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=NeverReasoner(),
            probe_executor=SimpleNamespace(
                collect=lambda *args: None
            ),
            utc_clock=lambda: INCIDENT_TIME,
        )
    )

    result = await coordinator.investigate(
        context
    )

    assert (
        result.scope.event_occurred_at
        == INCIDENT_TIME
    )


@pytest.mark.asyncio
async def test_prometheus_query_is_anchored_to_incident_time():
    tools = RecordingTools(
        {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": (
                INCIDENT_TIME
                + timedelta(seconds=30)
            ).isoformat(),
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {},
                        "value": [
                            (
                                INCIDENT_TIME
                                + timedelta(seconds=30)
                            ).timestamp(),
                            "512",
                        ],
                    }
                ],
            },
        }
    )

    context = SimpleNamespace(
        tools=tools,
        trace=None,
    )

    evidence = await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            context,
            build_scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        )
    )

    assert (
        tools.calls[0]["kwargs"]["time"]
        == INCIDENT_TIME
    )

    assert (
        evidence.facts["temporal_basis"]
        == "incident_time"
    )

    assert (
        evidence.facts[
            "event_offset_seconds"
        ]
        == 30.0
    )


@pytest.mark.asyncio
async def test_prometheus_sample_outside_incident_window_fails_closed():
    observed_at = (
        INCIDENT_TIME
        + timedelta(minutes=5)
    )

    tools = RecordingTools(
        {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": (
                observed_at.isoformat()
            ),
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {},
                        "value": [
                            observed_at.timestamp(),
                            "512",
                        ],
                    }
                ],
            },
        }
    )

    with pytest.raises(
        InvestigationProbeResponseError,
        match="temporally relevant",
    ):
        await (
            ReadOnlyInvestigationProbeExecutor()
            .collect(
                SimpleNamespace(
                    tools=tools,
                    trace=None,
                ),
                build_scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_kubernetes_evidence_is_explicitly_current_state():
    tools = RecordingTools(
        {
            "success": True,
            "source": "kubernetes",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": (
                INCIDENT_TIME
                + timedelta(minutes=10)
            ).isoformat(),
            "data": {
                "phase": "Running",
                "ready": True,
                "scheduled": True,
                "oom_killed": True,
                "containers": [],
            },
        }
    )

    evidence = await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            SimpleNamespace(
                tools=tools,
                trace=None,
            ),
            build_scope(),
            InvestigationProbe.KUBERNETES_POD_STATE,
        )
    )

    assert (
        evidence.facts["temporal_basis"]
        == "current_state"
    )

    assert (
        "time"
        not in tools.calls[0]["kwargs"]
    )


def test_time_policy_has_bounded_prometheus_skew():
    policy = InvestigationEvidenceTimePolicy()

    assert policy.prometheus_max_event_skew == (
        timedelta(minutes=2)
    )
