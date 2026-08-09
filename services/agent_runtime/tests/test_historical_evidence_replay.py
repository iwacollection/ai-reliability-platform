from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.agent_runtime.app.evaluation.real_incident.historical_replay import (
    HistoricalEvidenceAmbiguousError,
    HistoricalEvidenceNotFoundError,
    HistoricalReplayClockError,
    HistoricalReplayOperationNotAllowedError,
    create_historical_replay_environment,
)
from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentReplaySource,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
)
from services.agent_runtime.app.investigation.probes import (
    InvestigationProbeResponseError,
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


def observation(
    *,
    observation_id,
    source,
    kind,
    offset_seconds,
    data,
    production_signal=True,
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
        "production_signal": (
            production_signal
        ),
        "data": data,
        "metadata": {
            "resource": "payment-api",
            "namespace": "payment",
            "cluster": "production-a",
        },
    }


def replay_source():
    return RealIncidentReplaySource.model_validate(
        {
            "schema_version": "v1",
            "incident_id": (
                "incident-history-001"
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
                observation(
                    observation_id=(
                        "obs-k8s-before"
                    ),
                    source="kubernetes",
                    kind="pod_state",
                    offset_seconds=-10,
                    data={
                        "phase": "Running",
                        "ready": True,
                        "scheduled": True,
                        "oom_killed": False,
                        "containers": [
                            {
                                "restart_count": 1,
                                "state_reason": None,
                                "last_termination_reason": None,
                            }
                        ],
                    },
                ),
                observation(
                    observation_id=(
                        "obs-k8s-future"
                    ),
                    source="kubernetes",
                    kind="pod_state",
                    offset_seconds=30,
                    data={
                        "phase": "Running",
                        "ready": False,
                        "scheduled": True,
                        "oom_killed": True,
                        "uid": (
                            "must-not-leak"
                        ),
                        "containers": [
                            {
                                "restart_count": 7,
                                "state_reason": (
                                    "CrashLoopBackOff"
                                ),
                                "last_termination_reason": (
                                    "OOMKilled"
                                ),
                                "image": (
                                    "must-not-leak"
                                ),
                            }
                        ],
                    },
                ),
                observation(
                    observation_id=(
                        "obs-working-before"
                    ),
                    source="prometheus",
                    kind="memory_working_set",
                    offset_seconds=-20,
                    data={
                        "value": 400000000.0,
                    },
                ),
                observation(
                    observation_id=(
                        "obs-working-future"
                    ),
                    source="prometheus",
                    kind="memory_working_set",
                    offset_seconds=20,
                    data={
                        "value": 520000000.0,
                    },
                ),
                observation(
                    observation_id=(
                        "obs-limit-before"
                    ),
                    source="prometheus",
                    kind="memory_limit",
                    offset_seconds=-25,
                    data={
                        "value": 536870912.0,
                    },
                ),
                observation(
                    observation_id=(
                        "obs-limit-future"
                    ),
                    source="prometheus",
                    kind="memory_limit",
                    offset_seconds=25,
                    data={
                        "value": 536870912.0,
                    },
                ),
                observation(
                    observation_id=(
                        "obs-restart-before"
                    ),
                    source="prometheus",
                    kind="restart_count",
                    offset_seconds=-15,
                    data={
                        "value": 2,
                    },
                ),
                observation(
                    observation_id=(
                        "obs-restart-future"
                    ),
                    source="prometheus",
                    kind="restart_count",
                    offset_seconds=15,
                    data={
                        "value": 7,
                    },
                ),
            ],
        }
    )


def scope():
    return InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message=(
            "payment-api restarted"
        ),
        event_occurred_at=(
            INCIDENT_TIME
        ),
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )


def context(
    environment,
):
    return SimpleNamespace(
        tools=environment.tools,
        trace=None,
    )


@pytest.mark.asyncio
async def test_replay_starts_at_incident_time_and_hides_future_kubernetes():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    evidence = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.KUBERNETES_POD_STATE,
        )
    )

    assert (
        environment.clock.current_time
        == INCIDENT_TIME
    )

    assert (
        evidence.observed_at
        == INCIDENT_TIME
        - timedelta(
            seconds=10
        )
    )

    assert (
        evidence.facts[
            "oom_killed"
        ]
        is False
    )

    assert (
        evidence.facts[
            "max_restart_count"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_replay_starts_at_incident_time_and_hides_future_prometheus():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    evidence = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        )
    )

    assert (
        evidence.observed_at
        == INCIDENT_TIME
        - timedelta(
            seconds=20
        )
    )

    assert (
        evidence.facts[
            "value_sum"
        ]
        == 400000000.0
    )

    assert (
        evidence.facts[
            "event_offset_seconds"
        ]
        == -20.0
    )


@pytest.mark.asyncio
async def test_advancing_clock_reveals_newly_available_evidence():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    environment.clock.advance_to(
        INCIDENT_TIME
        + timedelta(
            seconds=30
        )
    )

    kubernetes = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.KUBERNETES_POD_STATE,
        )
    )

    prometheus = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        )
    )

    assert (
        kubernetes.observed_at
        == INCIDENT_TIME
        + timedelta(
            seconds=30
        )
    )

    assert (
        kubernetes.facts[
            "oom_killed"
        ]
        is True
    )

    assert (
        kubernetes.facts[
            "max_restart_count"
        ]
        == 7
    )

    assert (
        prometheus.observed_at
        == INCIDENT_TIME
        + timedelta(
            seconds=20
        )
    )

    assert (
        prometheus.facts[
            "value_sum"
        ]
        == 520000000.0
    )

    assert (
        prometheus.facts[
            "event_offset_seconds"
        ]
        == 20.0
    )

    serialized = str(
        kubernetes.model_dump(
            mode="json"
        )
    )

    assert (
        "must-not-leak"
        not in serialized
    )


def test_replay_clock_is_monotonic():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    environment.clock.advance(
        timedelta(
            seconds=30
        )
    )

    assert (
        environment.clock.current_time
        == INCIDENT_TIME
        + timedelta(
            seconds=30
        )
    )

    with pytest.raises(
        HistoricalReplayClockError,
        match="cannot move backward",
    ):
        environment.clock.advance_to(
            INCIDENT_TIME
            + timedelta(
                seconds=10
            )
        )


def test_environment_rejects_start_before_incident():
    with pytest.raises(
        HistoricalReplayClockError,
        match="cannot start before",
    ):
        create_historical_replay_environment(
            replay_source(),
            start_at=(
                INCIDENT_TIME
                - timedelta(
                    seconds=1
                )
            ),
        )


@pytest.mark.asyncio
async def test_future_only_observation_is_not_visible():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    payload[
        "observations"
    ] = [
        item
        for item
        in payload[
            "observations"
        ]
        if not (
            item[
                "kind"
            ]
            == "memory_working_set"
            and (
                datetime.fromisoformat(
                    item[
                        "observed_at"
                    ]
                )
                <= INCIDENT_TIME
            )
        )
    ]

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        HistoricalEvidenceNotFoundError,
        match="causally-visible",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_prometheus_query_cannot_read_beyond_replay_clock():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    prometheus = (
        environment.tools.registry.get(
            "prometheus"
        )
    )

    with pytest.raises(
        HistoricalReplayClockError,
        match="cannot read beyond",
    ):
        await prometheus.execute(
            query=(
                'sum(container_memory_working_set_bytes{'
                'pod="payment-api",'
                'namespace="payment",'
                'cluster="production-a"'
                '})'
            ),
            time=(
                INCIDENT_TIME
                + timedelta(
                    seconds=30
                )
            ),
        )


@pytest.mark.asyncio
async def test_latest_visible_sample_wins_not_nearest_future_sample():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    evidence = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.PROMETHEUS_RESTART_COUNT,
        )
    )

    assert (
        evidence.facts[
            "value_sum"
        ]
        == 2.0
    )

    environment.clock.advance_to(
        INCIDENT_TIME
        + timedelta(
            seconds=15
        )
    )

    evidence = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.PROMETHEUS_RESTART_COUNT,
        )
    )

    assert (
        evidence.facts[
            "value_sum"
        ]
        == 7.0
    )


@pytest.mark.asyncio
async def test_stale_prometheus_sample_fails_at_later_replay_clock():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    environment.clock.advance_to(
        INCIDENT_TIME
        + timedelta(
            minutes=5
        )
    )

    with pytest.raises(
        InvestigationProbeResponseError,
        match="temporally relevant",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_stale_kubernetes_snapshot_fails_at_later_replay_clock():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    environment.clock.advance_to(
        INCIDENT_TIME
        + timedelta(
            minutes=20
        )
    )

    with pytest.raises(
        HistoricalEvidenceNotFoundError,
        match="stale for the Replay Clock",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.KUBERNETES_POD_STATE,
            )
        )


@pytest.mark.asyncio
async def test_non_production_historical_evidence_still_fails_trust_boundary():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    for item in payload[
        "observations"
    ]:
        if (
            item[
                "observation_id"
            ]
            == "obs-working-before"
        ):
            item[
                "production_signal"
            ] = False

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        InvestigationProbeResponseError,
        match="not a production signal",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_historical_kubernetes_replay_rejects_mutation():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    kubernetes = (
        environment.tools.registry.get(
            "kubernetes"
        )
    )

    with pytest.raises(
        HistoricalReplayOperationNotAllowedError,
        match="not read-only",
    ):
        await kubernetes.execute(
            action="delete",
            resource="pod",
            target="payment-api",
            namespace="payment",
        )


@pytest.mark.asyncio
async def test_historical_prometheus_replay_rejects_arbitrary_query():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    prometheus = (
        environment.tools.registry.get(
            "prometheus"
        )
    )

    with pytest.raises(
        HistoricalReplayOperationNotAllowedError,
        match="allowlist",
    ):
        await prometheus.execute(
            query=(
                "sum(rate("
                "http_requests_total[5m]"
                "))"
            ),
            time=(
                environment.clock.current_time
            ),
        )


@pytest.mark.asyncio
async def test_equal_latest_timestamp_fails_ambiguous():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    duplicate = deepcopy(
        next(
            item
            for item
            in payload[
                "observations"
            ]
            if item[
                "observation_id"
            ]
            == "obs-working-before"
        )
    )

    duplicate[
        "observation_id"
    ] = "obs-working-before-duplicate"

    duplicate[
        "data"
    ][
        "value"
    ] = 123.0

    payload[
        "observations"
    ].append(
        duplicate
    )

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        HistoricalEvidenceAmbiguousError,
        match="ambiguous latest timestamp",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


def test_environment_uses_one_shared_replay_clock():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    kubernetes = (
        environment.tools.registry.get(
            "kubernetes"
        )
    )

    prometheus = (
        environment.tools.registry.get(
            "prometheus"
        )
    )

    assert (
        kubernetes.store.clock
        is environment.clock
    )

    assert (
        prometheus.store.clock
        is environment.clock
    )

    assert (
        environment
        .probe_executor
        .time_policy
        .clock
        is environment.clock
    )


def test_historical_tools_reuse_live_tool_names():
    environment = (
        create_historical_replay_environment(
            replay_source()
        )
    )

    names = {
        tool.name
        for tool
        in environment
        .tools
        .registry
        .list_tools()
    }

    assert names == {
        "kubernetes",
        "prometheus",
    }



@pytest.mark.asyncio
async def test_unscoped_prometheus_observation_cannot_satisfy_probe():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    payload[
        "observations"
    ] = [
        item
        for item
        in payload[
            "observations"
        ]
        if item[
            "kind"
        ] != "memory_working_set"
    ]

    unscoped = observation(
        observation_id=(
            "obs-unscoped-working-set"
        ),
        source="prometheus",
        kind="memory_working_set",
        offset_seconds=-10,
        data={
            "value": 999999999.0,
        },
    )

    unscoped[
        "metadata"
    ] = {}

    payload[
        "observations"
    ].append(
        unscoped
    )

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        HistoricalEvidenceNotFoundError,
        match="causally-visible",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_newer_wrong_resource_observation_cannot_override_correct_resource():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    wrong_resource = observation(
        observation_id=(
            "obs-other-pod-working-set"
        ),
        source="prometheus",
        kind="memory_working_set",
        offset_seconds=-1,
        data={
            "value": 999999999.0,
        },
    )

    wrong_resource[
        "metadata"
    ][
        "resource"
    ] = "other-api"

    payload[
        "observations"
    ].append(
        wrong_resource
    )

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    evidence = await (
        environment.probe_executor.collect(
            context(
                environment
            ),
            scope(),
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        )
    )

    # The correct payment-api sample is older (-20s), but it must win
    # because the newer -1s sample belongs to another resource.
    assert (
        evidence.facts[
            "value_sum"
        ]
        == 400000000.0
    )

    assert (
        evidence.facts[
            "event_offset_seconds"
        ]
        == -20.0
    )


@pytest.mark.asyncio
async def test_missing_cluster_scope_cannot_match_clustered_incident():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    payload[
        "observations"
    ] = [
        item
        for item
        in payload[
            "observations"
        ]
        if item[
            "kind"
        ] != "memory_working_set"
    ]

    incomplete = observation(
        observation_id=(
            "obs-missing-cluster"
        ),
        source="prometheus",
        kind="memory_working_set",
        offset_seconds=-10,
        data={
            "value": 500000000.0,
        },
    )

    incomplete[
        "metadata"
    ].pop(
        "cluster"
    )

    payload[
        "observations"
    ].append(
        incomplete
    )

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        HistoricalEvidenceNotFoundError,
        match="causally-visible",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            )
        )


@pytest.mark.asyncio
async def test_unscoped_kubernetes_snapshot_cannot_satisfy_pod_probe():
    payload = (
        replay_source()
        .model_dump(
            mode="json"
        )
    )

    payload[
        "observations"
    ] = [
        item
        for item
        in payload[
            "observations"
        ]
        if item[
            "kind"
        ] != "pod_state"
    ]

    unscoped = observation(
        observation_id=(
            "obs-unscoped-pod"
        ),
        source="kubernetes",
        kind="pod_state",
        offset_seconds=-5,
        data={
            "phase": "Running",
            "ready": False,
            "scheduled": True,
            "oom_killed": True,
            "containers": [
                {
                    "restart_count": 100,
                    "state_reason": (
                        "CrashLoopBackOff"
                    ),
                    "last_termination_reason": (
                        "OOMKilled"
                    ),
                }
            ],
        },
    )

    unscoped[
        "metadata"
    ] = {}

    payload[
        "observations"
    ].append(
        unscoped
    )

    source = (
        RealIncidentReplaySource
        .model_validate(
            payload
        )
    )

    environment = (
        create_historical_replay_environment(
            source
        )
    )

    with pytest.raises(
        HistoricalEvidenceNotFoundError,
        match="causally-visible",
    ):
        await (
            environment.probe_executor.collect(
                context(
                    environment
                ),
                scope(),
                InvestigationProbe.KUBERNETES_POD_STATE,
            )
        )
