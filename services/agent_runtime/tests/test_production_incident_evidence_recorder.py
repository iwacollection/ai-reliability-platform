from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.evaluation.real_incident.historical_replay import (
    create_historical_replay_environment,
)
from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentReplaySource,
)
from services.agent_runtime.app.incident_evidence.recorder import (
    ProductionIncidentEvidenceRecorder,
    ProductionIncidentEvidenceScopeError,
    ProductionIncidentEvidenceUnavailableError,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
)


NOW = datetime(
    2026,
    8,
    10,
    8,
    10,
    0,
    tzinfo=UTC,
)


class TrustedToolManager:
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
            return {
                "success": True,
                "source": "kubernetes",
                "mode": "read_only",
                "production_signal": True,
                "observed_at": NOW.isoformat(),
                "data": {
                    "phase": "Running",
                    "ready": False,
                    "scheduled": True,
                    "oom_killed": True,
                    "containers": [
                        {
                            "restart_count": 7,
                            "state_reason": (
                                "CrashLoopBackOff"
                            ),
                            "last_termination_reason": (
                                "OOMKilled"
                            ),
                            "image_id": (
                                "must-not-be-retained"
                            ),
                        }
                    ],
                    "uid": "must-not-be-retained",
                },
            }

        query = kwargs[
            "query"
        ]

        if (
            "container_memory_working_set_bytes"
            in query
        ):
            value = 503316480.0

        elif (
            "kube_pod_container_resource_limits"
            in query
        ):
            value = 536870912.0

        elif (
            "kube_pod_container_status_restarts_total"
            in query
        ):
            value = 7.0

        else:
            raise AssertionError(
                "Unexpected Prometheus query"
            )

        return {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": NOW.isoformat(),
            "query": (
                "must-not-be-retained"
            ),
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "pod": (
                                "payment-api"
                            ),
                        },
                        "value": [
                            NOW.timestamp(),
                            str(
                                value
                            ),
                        ],
                    }
                ],
            },
        }


class UntrustedToolManager:
    async def call(
        self,
        name,
        context=None,
        **kwargs,
    ):
        if name == "kubernetes":
            return {
                "success": True,
                "source": "mock_kubernetes",
                "mode": "dry_run",
                "production_signal": False,
                "observed_at": NOW.isoformat(),
                "data": {
                    "phase": "Running",
                    "containers": [],
                },
            }

        return {
            "success": True,
            "source": "mock_prometheus",
            "mode": "mock",
            "production_signal": False,
            "observed_at": NOW.isoformat(),
            "data": {
                "resultType": "vector",
                "result": [],
            },
        }


def event(
    *,
    resources: int = 1,
) -> StandardEvent:
    return StandardEvent(
        header=Header(
            event_id=UUID(
                "11111111-1111-4111-8111-111111111111"
            ),
            trace_id=UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            source=(
                EventSource.ALERTMANAGER
            ),
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message=(
                "payment-api restarted"
            ),
            labels={},
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name=(
                    "payment-api"
                    if index == 0
                    else f"payment-api-{index}"
                ),
                namespace="payment",
                cluster="production-a",
            )
            for index
            in range(
                resources
            )
        ],
    )


def context(
    tools,
    *,
    resources: int = 1,
):
    return SimpleNamespace(
        event=event(
            resources=resources
        ),
        tools=tools,
    )


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message=(
            "payment-api restarted"
        ),
        event_occurred_at=NOW,
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


@pytest.mark.asyncio
async def test_recorder_persists_replay_safe_capture(
    tmp_path,
):
    tools = TrustedToolManager()

    recorder = (
        ProductionIncidentEvidenceRecorder(
            tmp_path
            / "captures"
        )
    )

    result = await recorder.record(
        context(
            tools
        )
    )

    assert result.created is True
    assert result.observation_count == 4

    assert result.collected_probes == (
        InvestigationProbe.KUBERNETES_POD_STATE,
        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
        InvestigationProbe.PROMETHEUS_RESTART_COUNT,
    )

    source = (
        RealIncidentReplaySource
        .model_validate_json(
            result.path.read_text(
                encoding="utf-8"
            )
        )
    )

    assert source.incident_id == (
        "capture-"
        "11111111-1111-4111-8111-111111111111"
    )

    assert len(
        source.observations
    ) == 4

    serialized = json.dumps(
        source.model_dump(
            mode="json"
        ),
        sort_keys=True,
    )

    assert (
        "ground_truth"
        not in serialized
    )

    assert (
        "timeline"
        not in serialized
    )

    assert (
        "must-not-be-retained"
        not in serialized
    )

    assert (
        "must-not-be-retained"
        not in result.path.read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.asyncio
async def test_capture_round_trips_through_historical_replay(
    tmp_path,
):
    recorder = (
        ProductionIncidentEvidenceRecorder(
            tmp_path
        )
    )

    result = await recorder.record(
        context(
            TrustedToolManager()
        )
    )

    source = (
        RealIncidentReplaySource
        .model_validate_json(
            result.path.read_text(
                encoding="utf-8"
            )
        )
    )

    environment = (
        create_historical_replay_environment(
            source,
            start_at=NOW,
        )
    )

    replay_context = (
        SimpleNamespace(
            tools=environment.tools,
            trace=None,
        )
    )

    pod = await (
        environment
        .probe_executor
        .collect(
            replay_context,
            scope(),
            InvestigationProbe.KUBERNETES_POD_STATE,
        )
    )

    working = await (
        environment
        .probe_executor
        .collect(
            replay_context,
            scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        )
    )

    limit = await (
        environment
        .probe_executor
        .collect(
            replay_context,
            scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
        )
    )

    restarts = await (
        environment
        .probe_executor
        .collect(
            replay_context,
            scope(),
            InvestigationProbe.PROMETHEUS_RESTART_COUNT,
        )
    )

    assert (
        pod.facts[
            "oom_killed"
        ]
        is True
    )

    assert (
        pod.facts[
            "max_restart_count"
        ]
        == 7
    )

    assert (
        working.facts[
            "value_sum"
        ]
        == 503316480.0
    )

    assert (
        limit.facts[
            "value_sum"
        ]
        == 536870912.0
    )

    assert (
        restarts.facts[
            "value_sum"
        ]
        == 7.0
    )


@pytest.mark.asyncio
async def test_duplicate_event_is_idempotent_and_does_not_probe_again(
    tmp_path,
):
    tools = TrustedToolManager()

    recorder = (
        ProductionIncidentEvidenceRecorder(
            tmp_path
        )
    )

    first = await recorder.record(
        context(
            tools
        )
    )

    call_count = len(
        tools.calls
    )

    second = await recorder.record(
        context(
            tools
        )
    )

    assert first.created is True
    assert second.created is False
    assert first.path == second.path

    assert len(
        tools.calls
    ) == call_count


@pytest.mark.asyncio
async def test_mock_or_dry_run_evidence_is_never_persisted(
    tmp_path,
):
    recorder = (
        ProductionIncidentEvidenceRecorder(
            tmp_path
        )
    )

    with pytest.raises(
        ProductionIncidentEvidenceUnavailableError,
        match=(
            "No trusted production evidence"
        ),
    ):
        await recorder.record(
            context(
                UntrustedToolManager()
            )
        )

    assert list(
        tmp_path.glob(
            "*.json"
        )
    ) == []


@pytest.mark.asyncio
async def test_ambiguous_event_scope_fails_before_any_probe(
    tmp_path,
):
    tools = TrustedToolManager()

    recorder = (
        ProductionIncidentEvidenceRecorder(
            tmp_path
        )
    )

    with pytest.raises(
        ProductionIncidentEvidenceScopeError,
        match=(
            "exactly one resource"
        ),
    ):
        await recorder.record(
            context(
                tools,
                resources=2,
            )
        )

    assert tools.calls == []


def test_recorder_has_no_llm_or_action_authority():
    import inspect

    from services.agent_runtime.app.incident_evidence import (
        recorder as module,
    )

    source = inspect.getsource(
        module
    )

    forbidden = (
        "create_llm_gateway",
        "LLMInvestigationReasoner",
        "ActionRuntime",
        "ApprovalService",
        "VerificationRuntime",
        "pipeline.execute",
        "kubernetes_patch",
        "kubernetes_delete",
    )

    for token in forbidden:
        assert token not in source
