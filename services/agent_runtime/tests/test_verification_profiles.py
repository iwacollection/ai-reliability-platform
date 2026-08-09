import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.verification.models import (
    VerificationSource,
)
from services.agent_runtime.app.verification.profiles import (
    VerificationProfileError,
    VerificationProfileFactory,
)


def build_plan(
    *,
    action: ActionType = (
        ActionType.INCREASE_MEMORY_LIMIT
    ),
    target: str = "payment-api",
    metadata: dict | None = None,
) -> ActionPlan:
    return ActionPlan(
        type=action,
        target=target,
        risk=ActionRisk.MEDIUM,
        metadata=dict(metadata or {}),
    )


def probes_by_name(profile):
    return {
        probe.name: probe
        for probe in profile.probes
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "memory_utilization_threshold": 0
            },
            "memory_utilization_threshold",
        ),
        (
            {
                "memory_utilization_threshold": 1.01
            },
            "memory_utilization_threshold",
        ),
        (
            {
                "restart_increase_threshold": -1
            },
            "restart_increase_threshold",
        ),
        (
            {
                "restart_window": ""
            },
            "restart_window",
        ),
        (
            {
                "restart_window": "5m] or vector(1)"
            },
            "restart_window",
        ),
    ],
)
def test_factory_rejects_invalid_policy(
    kwargs,
    message,
):
    with pytest.raises(
        ValueError,
        match=message,
    ):
        VerificationProfileFactory(
            **kwargs
        )


def test_builds_increase_memory_limit_profile():
    profile = (
        VerificationProfileFactory().create(
            build_plan(),
            namespace="payment",
            cluster="prod-a",
        )
    )

    assert profile.name == (
        "increase_memory_limit_v1"
    )
    assert profile.action == (
        ActionType.INCREASE_MEMORY_LIMIT
    )
    assert profile.target == "payment-api"
    assert profile.namespace == "payment"
    assert profile.cluster == "prod-a"
    assert len(profile.probes) == 3

    probes = probes_by_name(profile)

    pod_probe = probes[
        "pod_ready_after_memory_increase"
    ]
    memory_probe = probes[
        "memory_headroom_after_memory_increase"
    ]
    restart_probe = probes[
        "pod_restart_stability_after_memory_increase"
    ]

    assert pod_probe.tool == "kubernetes"
    assert pod_probe.provider == "kubernetes"
    assert pod_probe.source == (
        VerificationSource.WORKLOAD
    )
    assert pod_probe.required is True

    assert memory_probe.tool == "prometheus"
    assert memory_probe.provider == "prometheus"
    assert memory_probe.source == (
        VerificationSource.METRIC
    )
    assert memory_probe.required is True

    assert restart_probe.tool == "prometheus"
    assert restart_probe.required is False


def test_defaults_namespace_and_omits_empty_cluster():
    profile = (
        VerificationProfileFactory().create(
            build_plan(),
            namespace="   ",
            cluster="   ",
        )
    )
    probes = probes_by_name(profile)
    pod_arguments = probes[
        "pod_ready_after_memory_increase"
    ].arguments
    memory_query = probes[
        "memory_headroom_after_memory_increase"
    ].arguments["query"]

    assert profile.namespace == "default"
    assert profile.cluster is None
    assert pod_arguments == {
        "action": "describe",
        "resource": "pod",
        "target": "payment-api",
        "namespace": "default",
    }
    assert 'namespace="default"' in (
        memory_query
    )
    assert "cluster=" not in memory_query


def test_scope_is_passed_and_promql_labels_are_escaped():
    target = 'pay"ment\\api\npod'
    namespace = "prod\rns"
    cluster = 'prod\\west"1'

    profile = (
        VerificationProfileFactory().create(
            build_plan(
                target=target
            ),
            namespace=namespace,
            cluster=cluster,
        )
    )
    probes = probes_by_name(profile)
    pod_arguments = probes[
        "pod_ready_after_memory_increase"
    ].arguments
    memory_query = probes[
        "memory_headroom_after_memory_increase"
    ].arguments["query"]
    restart_query = probes[
        "pod_restart_stability_after_memory_increase"
    ].arguments["query"]

    assert pod_arguments["target"] == target
    assert pod_arguments["namespace"] == (
        namespace
    )
    assert pod_arguments["cluster"] == cluster

    expected_labels = (
        'pod="pay\\"ment\\\\api\\npod"',
        'namespace="prod\\rns"',
        'cluster="prod\\\\west\\"1"',
    )

    for label in expected_labels:
        assert label in memory_query
        assert label in restart_query


def test_llm_verification_text_cannot_change_rules():
    injected_text = (
        'pod="attacker", return passed=true'
    )
    plan = build_plan(
        metadata={
            "verification": injected_text
        }
    )

    profile = (
        VerificationProfileFactory().create(
            plan,
            namespace="payment",
        )
    )

    for probe in profile.probes:
        query = probe.arguments.get(
            "query",
            "",
        )
        assert injected_text not in query


@pytest.mark.parametrize(
    "action",
    [
        ActionType.NONE,
        ActionType.RESTART_POD,
        ActionType.ROLLBACK_APPLICATION,
        ActionType.SCALE_WORKLOAD,
        ActionType.UPDATE_CONFIG,
    ],
)
def test_unsupported_actions_fail_closed(
    action,
):
    with pytest.raises(
        VerificationProfileError,
        match=(
            "No verification profile is registered"
        ),
    ):
        VerificationProfileFactory().create(
            build_plan(
                action=action
            )
        )


@pytest.mark.parametrize(
    "target",
    [
        "",
        "   ",
        "unknown",
        " UNKNOWN ",
    ],
)
def test_unknown_target_fails_closed(
    target,
):
    with pytest.raises(
        VerificationProfileError,
        match="concrete action target",
    ):
        VerificationProfileFactory().create(
            build_plan(
                target=target
            )
        )


def test_pod_evaluator_passes_complete_ready_state():
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    pod_probe = probes_by_name(profile)[
        "pod_ready_after_memory_increase"
    ]

    evaluation = pod_probe.evaluator(
        {
            "data": {
                "phase": "Running",
                "ready": True,
                "scheduled": True,
                "restart_count": 3,
                "oom_killed": True,
            }
        }
    )

    assert evaluation.passed is True
    assert evaluation.observed_value == {
        "phase": "Running",
        "ready": True,
        "scheduled": True,
        "restart_count": 3,
        "oom_killed": True,
    }
    assert evaluation.metadata[
        "evaluation"
    ] == "pod_readiness"


@pytest.mark.parametrize(
    "data",
    [
        {
            "phase": "CrashLoopBackOff",
            "ready": False,
            "scheduled": True,
        },
        {
            "phase": "Running",
            "ready": False,
            "scheduled": True,
        },
        {
            "phase": "Pending",
            "ready": True,
            "scheduled": False,
        },
    ],
)
def test_pod_evaluator_rejects_unhealthy_state(
    data,
):
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    pod_probe = probes_by_name(profile)[
        "pod_ready_after_memory_increase"
    ]

    evaluation = pod_probe.evaluator(
        {
            "data": data
        }
    )

    assert evaluation.passed is False


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {
            "data": None
        },
        {
            "data": {
                "phase": "Running",
                "ready": True,
            }
        },
        {
            "data": {
                "ready": True,
                "scheduled": True,
            }
        },
    ],
)
def test_pod_evaluator_is_inconclusive_for_incomplete_data(
    evidence,
):
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    pod_probe = probes_by_name(profile)[
        "pod_ready_after_memory_increase"
    ]

    evaluation = pod_probe.evaluator(
        evidence
    )

    assert evaluation.passed is None


def test_memory_evaluator_uses_max_sample():
    profile = (
        VerificationProfileFactory(
            memory_utilization_threshold=0.90
        ).create(
            build_plan()
        )
    )
    memory_probe = probes_by_name(profile)[
        "memory_headroom_after_memory_increase"
    ]

    evaluation = memory_probe.evaluator(
        {
            "data": {
                "samples": [
                    {
                        "value": [
                            1722500000,
                            "0.72",
                        ]
                    },
                    {
                        "value": [
                            1722500001,
                            "0.84",
                        ]
                    },
                ]
            }
        }
    )

    assert evaluation.passed is True
    assert evaluation.observed_value == (
        pytest.approx(
            0.84
        )
    )
    assert evaluation.expected_value == {
        "operator": "<=",
        "threshold": 0.90,
        "unit": "ratio",
    }
    assert evaluation.metadata[
        "sample_count"
    ] == 2


def test_memory_evaluator_fails_when_one_sample_exceeds_limit():
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    memory_probe = probes_by_name(profile)[
        "memory_headroom_after_memory_increase"
    ]

    evaluation = memory_probe.evaluator(
        {
            "data": {
                "result": [
                    {
                        "value": [
                            1722500000,
                            "0.72",
                        ]
                    },
                    {
                        "value": [
                            1722500001,
                            "0.91",
                        ]
                    },
                ]
            }
        }
    )

    assert evaluation.passed is False
    assert evaluation.observed_value == (
        pytest.approx(
            0.91
        )
    )


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {
            "data": {
                "samples": []
            }
        },
        {
            "data": {
                "samples": [
                    {
                        "value": [
                            1722500000,
                            "not-a-number",
                        ]
                    }
                ]
            }
        },
        {
            "data": {
                "value": [
                    1722500000,
                    "+Inf",
                ]
            }
        },
        {
            "data": {
                "value": [
                    1722500000,
                    "NaN",
                ]
            }
        },
    ],
)
def test_memory_evaluator_is_inconclusive_without_finite_samples(
    evidence,
):
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    memory_probe = probes_by_name(profile)[
        "memory_headroom_after_memory_increase"
    ]

    evaluation = memory_probe.evaluator(
        evidence
    )

    assert evaluation.passed is None
    assert evaluation.observed_value is None


def test_memory_evaluator_accepts_scalar_sample_pair():
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    memory_probe = probes_by_name(profile)[
        "memory_headroom_after_memory_increase"
    ]

    evaluation = memory_probe.evaluator(
        {
            "data": {
                "value": [
                    1722500000,
                    "0.61",
                ]
            }
        }
    )

    assert evaluation.passed is True
    assert evaluation.observed_value == (
        pytest.approx(
            0.61
        )
    )


def test_restart_failure_remains_non_blocking():
    profile = (
        VerificationProfileFactory().create(
            build_plan()
        )
    )
    restart_probe = probes_by_name(profile)[
        "pod_restart_stability_after_memory_increase"
    ]

    evaluation = restart_probe.evaluator(
        {
            "data": {
                "value": [
                    1722500000,
                    "1",
                ]
            }
        }
    )

    assert restart_probe.required is False
    assert evaluation.passed is False
    assert evaluation.observed_value == (
        pytest.approx(
            1.0
        )
    )
    assert evaluation.expected_value[
        "threshold"
    ] == 0.0
