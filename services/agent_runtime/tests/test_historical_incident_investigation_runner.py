import json
from datetime import UTC, datetime, timedelta

import pytest

from services.agent_runtime.app.evaluation.real_incident.investigation_runner import (
    HistoricalIncidentInvestigationRunner,
    HistoricalIncidentInvestigationRunnerError,
)
from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentDataset,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


INCIDENT_TIME = datetime(
    2026,
    7,
    18,
    6,
    1,
    0,
    tzinfo=UTC,
)

HUMAN_ANSWER_SECRET = (
    "HUMAN_ONLY_ROOT_CAUSE_"
    "MUST_NEVER_REACH_AGENT"
)


def historical_observation(
    *,
    observation_id,
    source,
    kind,
    offset_seconds,
    data,
):
    return {
        "observation_id": observation_id,
        "source": source,
        "kind": kind,
        "observed_at": (
            INCIDENT_TIME
            + timedelta(
                seconds=offset_seconds
            )
        ).isoformat(),
        "production_signal": True,
        "data": data,
        "metadata": {
            "resource": "payment-api",
            "namespace": "payment",
            "cluster": "production-a",
        },
    }


def dataset_payload():
    return {
        "schema_version": "v1",
        "incident_id": (
            "real-history-001"
        ),
        "event": {
            "header": {
                "event_id": (
                    "11111111-1111-4111-8111-111111111111"
                ),
                "trace_id": (
                    "22222222-2222-4222-8222-222222222222"
                ),
                "source": (
                    "alertmanager"
                ),
                "occurred_at": (
                    INCIDENT_TIME.isoformat()
                ),
            },
            "signal": {
                "type": "alert",
                "name": "PodOOMKilled",
                "severity": "critical",
                "message": (
                    "payment-api restarted"
                ),
                "labels": {},
            },
            "resources": [
                {
                    "kind": "pod",
                    "name": "payment-api",
                    "namespace": "payment",
                    "cluster": "production-a",
                }
            ],
        },
        "observations": [
            historical_observation(
                observation_id=(
                    "obs-k8s-before"
                ),
                source="kubernetes",
                kind="pod_state",
                offset_seconds=-10,
                data={
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
                        }
                    ],
                },
            ),
            historical_observation(
                observation_id=(
                    "obs-memory-before"
                ),
                source="prometheus",
                kind="memory_working_set",
                offset_seconds=-5,
                data={
                    "value": 520000000.0,
                },
            ),

            # Must remain invisible at replay_at = Incident time.
            historical_observation(
                observation_id=(
                    "obs-memory-future"
                ),
                source="prometheus",
                kind="memory_working_set",
                offset_seconds=30,
                data={
                    "value": 999999999.0,
                },
            ),
        ],
        "timeline": [
            {
                "timeline_id": "timeline-human-1",
                "occurred_at": (
                    INCIDENT_TIME
                    + timedelta(
                        minutes=10
                    )
                ).isoformat(),
                "source": "human",
                "event_type": (
                    "operator_note"
                ),
                "summary": (
                    "Human operator later learned the answer"
                ),
                "evidence_refs": [],
            }
        ],
        "ground_truth": {
            "root_cause": (
                HUMAN_ANSWER_SECRET
            ),
            "contributing_factors": [],
            "evidence_refs": [
                "obs-k8s-before",
                "obs-memory-before",
            ],
            "source": (
                "postmortem"
            ),
            "quality": "verified",
            "reviewed_at": (
                INCIDENT_TIME
                + timedelta(
                    hours=2
                )
            ).isoformat(),
            "resolution_summary": (
                "Human-only resolution text"
            ),
        },
        "metadata": {
            "real_incident_test": True,
        },
    }


def dataset():
    return RealIncidentDataset.model_validate(
        dataset_payload()
    )


class StateDrivenReasoner(
    BaseInvestigationReasoner
):
    """
    Deterministic unit-test Reasoner.

    It chooses its next action from the Investigation state rather than from
    Runner instructions, proving that the Runner never owns the Probe path.
    """

    def __init__(
        self,
    ):
        self.states = []
        self.scopes = []

    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:

        self.scopes.append(
            scope.model_copy(
                deep=True
            )
        )

        self.states.append(
            state.model_copy(
                deep=True
            )
        )

        known_ids = [
            item.evidence_id
            for item
            in state.evidence
            if item.trusted
        ]

        if len(
            state.evidence
        ) == 0:

            return InvestigationDecision(
                hypotheses=[
                    IncidentHypothesis(
                        hypothesis_id=(
                            "memory-pressure"
                        ),
                        cause=(
                            "Pod may have exceeded "
                            "its memory boundary"
                        ),
                        confidence=0.45,
                        missing_evidence=[
                            "Pod termination state"
                        ],
                    )
                ],
                rationale_summary=(
                    "First verify the Pod termination state"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            )

        if len(
            state.evidence
        ) == 1:

            return InvestigationDecision(
                hypotheses=[
                    IncidentHypothesis(
                        hypothesis_id=(
                            "memory-pressure"
                        ),
                        cause=(
                            "OOMKilled is supported, "
                            "but memory usage is still needed"
                        ),
                        confidence=0.72,
                        supporting_evidence_ids=(
                            known_ids
                        ),
                        missing_evidence=[
                            "memory working set"
                        ],
                    )
                ],
                rationale_summary=(
                    "Measure memory working set"
                ),
                next_probe=(
                    InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
                ),
            )

        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id=(
                        "memory-pressure"
                    ),
                    cause=(
                        "OOMKilled with high memory usage"
                    ),
                    confidence=0.94,
                    supporting_evidence_ids=(
                        known_ids
                    ),
                )
            ],
            rationale_summary=(
                "Trusted evidence is sufficient"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason.SUFFICIENT_EVIDENCE
            ),
            conclusion=InvestigationConclusion(
                root_cause=(
                    "Container memory pressure "
                    "caused OOMKilled"
                ),
                confidence=0.94,
                evidence_ids=known_ids,
            ),
        )


class ForbiddenRuntimeProbeExecutor:
    def __init__(
        self,
    ):
        self.calls = 0

    async def collect(
        self,
        context,
        scope,
        probe,
    ):
        self.calls += 1

        raise AssertionError(
            "Historical Runner called Runtime production probe executor"
        )


class ForbiddenPipeline:
    def __init__(
        self,
    ):
        self.calls = 0

    async def execute(
        self,
        context,
    ):
        self.calls += 1

        raise AssertionError(
            "Historical Runner called PlannerPipeline"
        )


def enabled_runtime(
    reasoner,
):
    settings = InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=InvestigationLimits(
            max_iterations=6,
            max_tool_calls=6,
            timeout_seconds=10,
        ),
    )

    runtime_probe = (
        ForbiddenRuntimeProbeExecutor()
    )

    runtime_coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=runtime_probe,
            limits=settings.limits,
        )
    )

    runtime = object.__new__(
        AgentRuntime
    )

    runtime.investigation_settings = (
        settings
    )

    runtime.investigation_coordinator = (
        runtime_coordinator
    )

    runtime.pipeline = ForbiddenPipeline()

    return (
        runtime,
        runtime_probe,
    )


@pytest.mark.asyncio
async def test_runner_reuses_agent_brain_but_not_runtime_probe_backend():
    reasoner = (
        StateDrivenReasoner()
    )

    (
        runtime,
        runtime_probe,
    ) = enabled_runtime(
        reasoner
    )

    runner = (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
    )

    result = await runner.run(
        dataset()
    )

    assert (
        runtime.pipeline.calls
        == 0
    )

    assert (
        runtime_probe.calls
        == 0
    )

    assert (
        runner.reasoner
        is reasoner
    )

    assert (
        result.replay_mode
        == "point_in_time"
    )

    assert result.read_only is True

    assert (
        result.decision_influence
        is False
    )

    assert (
        result.investigation.status.value
        == "concluded"
    )

    assert (
        result.investigation.stop_reason
        == InvestigationStopReason.SUFFICIENT_EVIDENCE
    )

    assert (
        result.investigation.attempted_probes
        == [
            InvestigationProbe.KUBERNETES_POD_STATE,
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        ]
    )

    assert (
        result.investigation.tool_call_count
        == 2
    )


@pytest.mark.asyncio
async def test_agent_decides_probe_path_from_state():
    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    runner = (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
    )

    result = await runner.run(
        dataset()
    )

    assert len(
        reasoner.states
    ) == 3

    # Before first decision, Agent has no evidence.
    assert (
        reasoner.states[0].evidence
        == []
    )

    # The second decision sees Kubernetes evidence selected by the Agent's
    # own previous decision.
    assert (
        reasoner.states[1]
        .evidence[0]
        .probe
        == InvestigationProbe.KUBERNETES_POD_STATE
    )

    # The third decision sees both pieces of evidence and decides to stop.
    assert {
        item.probe
        for item
        in reasoner.states[2].evidence
    } == {
        InvestigationProbe.KUBERNETES_POD_STATE,
        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
    }

    assert (
        result.investigation.conclusion
        is not None
    )


@pytest.mark.asyncio
async def test_point_in_time_runner_cannot_see_future_observation():
    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    result = await (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
        .run(
            dataset(),
            replay_at=INCIDENT_TIME,
        )
    )

    prometheus_evidence = next(
        item
        for item
        in result.investigation.evidence
        if item.probe
        == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
    )

    assert (
        prometheus_evidence.observed_at
        == INCIDENT_TIME
        - timedelta(
            seconds=5
        )
    )

    assert (
        prometheus_evidence.facts[
            "value_sum"
        ]
        == 520000000.0
    )

    # The +30 second future value is 999999999 and must not appear.
    serialized = json.dumps(
        result.model_dump(
            mode="json"
        ),
        sort_keys=True,
    )

    assert (
        "999999999"
        not in serialized
    )


@pytest.mark.asyncio
async def test_result_cannot_contain_human_answer_or_timeline():
    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    result = await (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
        .run(
            dataset()
        )
    )

    serialized = json.dumps(
        result.model_dump(
            mode="json"
        ),
        sort_keys=True,
    )

    assert (
        HUMAN_ANSWER_SECRET
        not in serialized
    )

    assert (
        "Human operator later learned the answer"
        not in serialized
    )

    assert (
        "Human-only resolution text"
        not in serialized
    )


@pytest.mark.asyncio
async def test_runner_uses_replay_clock_for_investigation_timestamps():
    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    replay_at = (
        INCIDENT_TIME
        + timedelta(
            seconds=30
        )
    )

    result = await (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
        .run(
            dataset(),
            replay_at=replay_at,
        )
    )

    assert (
        result.replay_at
        == replay_at
    )

    assert (
        result.investigation.started_at
        == replay_at
    )

    assert (
        result.investigation.updated_at
        == replay_at
    )

    prometheus_evidence = next(
        item
        for item
        in result.investigation.evidence
        if item.probe
        == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
    )

    # At T+30 the previously future sample has become causally visible.
    assert (
        prometheus_evidence.observed_at
        == replay_at
    )

    assert (
        prometheus_evidence.facts[
            "value_sum"
        ]
        == 999999999.0
    )


@pytest.mark.asyncio
async def test_run_file_uses_validated_dataset_loader(
    tmp_path,
):
    path = (
        tmp_path
        / "real-incident.json"
    )

    path.write_text(
        json.dumps(
            dataset_payload()
        ),
        encoding="utf-8",
    )

    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    result = await (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
        .run_file(
            path
        )
    )

    assert (
        result.incident_id
        == "real-history-001"
    )

    assert (
        result.incident_time
        == INCIDENT_TIME
    )


def test_disabled_runtime_is_rejected():
    reasoner = (
        StateDrivenReasoner()
    )

    settings = InvestigationSettings()

    runtime = object.__new__(
        AgentRuntime
    )

    runtime.investigation_settings = (
        settings
    )

    runtime.investigation_coordinator = (
        None
    )

    with pytest.raises(
        HistoricalIncidentInvestigationRunnerError,
        match="requires enabled Investigation",
    ):
        HistoricalIncidentInvestigationRunner(
            runtime
        )


@pytest.mark.asyncio
async def test_replay_before_incident_is_rejected():
    reasoner = (
        StateDrivenReasoner()
    )

    runtime, _ = enabled_runtime(
        reasoner
    )

    runner = (
        HistoricalIncidentInvestigationRunner(
            runtime
        )
    )

    with pytest.raises(
        Exception,
        match="cannot be before the Incident",
    ):
        await runner.run(
            dataset(),
            replay_at=(
                INCIDENT_TIME
                - timedelta(
                    seconds=1
                )
            ),
        )
