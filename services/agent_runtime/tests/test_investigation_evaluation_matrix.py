import pytest

from services.agent_runtime.app.evaluation.scenario.factory import (
    create_scenario_registry,
)
from services.agent_runtime.app.evaluation.scenario.investigation_matrix import (
    create_investigation_evaluation_scenario_registry,
)
from services.agent_runtime.app.investigation.evaluation_fixture_runtime import (
    create_investigation_evaluation_runtime,
)
from services.agent_runtime.app.investigation.evaluation_matrix import (
    InvestigationEvaluationMatrixRunner,
)
from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)


def test_matrix_registry_is_separate_from_default_registry():
    default_registry = (
        create_scenario_registry()
    )

    matrix_registry = (
        create_investigation_evaluation_scenario_registry()
    )

    default_names = {
        item.name
        for item
        in default_registry.list()
    }

    matrix_names = {
        item.name
        for item
        in matrix_registry.list()
    }

    assert "pod_oom_killed" in (
        default_names
    )

    assert len(
        matrix_names
    ) == 5

    assert "pod_oom_killed" not in (
        matrix_names
    )

    assert matrix_names == {
        "matrix_oom_killed",
        "matrix_memory_pressure",
        "matrix_restart_storm",
        "matrix_memory_saturation",
        (
            "matrix_deployment_"
            "regression_unproven"
        ),
    }


def test_evaluation_runtime_enables_shadow_with_one_shared_gateway(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    runtime = (
        create_investigation_evaluation_runtime()
    )

    assert (
        runtime.investigation_settings.enabled
        is True
    )

    assert (
        runtime.investigation_coordinator
        is not None
    )

    assert (
        runtime.registry.get(
            "noise"
        ).llm_gateway
        is runtime.llm_gateway
    )

    assert (
        runtime.registry.get(
            "rca"
        ).llm_gateway
        is runtime.llm_gateway
    )

    assert (
        runtime.registry.get(
            "healing"
        ).llm_gateway
        is runtime.llm_gateway
    )

    adapter = (
        runtime
        .investigation_coordinator
        .reasoner
        .investigation_llm
    )

    assert isinstance(
        adapter,
        InvestigationLLMGatewayAdapter,
    )

    assert (
        adapter.llm_gateway
        is runtime.llm_gateway
    )


def test_evaluation_runtime_uses_real_read_only_tool_classes(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    runtime = (
        create_investigation_evaluation_runtime()
    )

    kubernetes = (
        runtime.tools.registry.get(
            "kubernetes"
        )
    )

    prometheus = (
        runtime.tools.registry.get(
            "prometheus"
        )
    )

    assert isinstance(
        kubernetes,
        KubernetesTool,
    )

    assert isinstance(
        prometheus,
        PrometheusTool,
    )

    assert (
        kubernetes.allow_dry_run_fallback
        is False
    )

    assert (
        prometheus.allow_mock_fallback
        is False
    )


@pytest.mark.asyncio
async def test_five_scenario_matrix_produces_expected_agent_metrics(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    runtime = (
        create_investigation_evaluation_runtime()
    )

    # Prove this Matrix never crosses into remediation execution.
    async def forbidden_action(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Evaluation Matrix called ActionRuntime"
        )

    monkeypatch.setattr(
        runtime.action_runtime,
        "execute",
        forbidden_action,
    )

    registry = (
        create_investigation_evaluation_scenario_registry()
    )

    runner = (
        InvestigationEvaluationMatrixRunner(
            runtime=runtime,
            registry=registry,
        )
    )

    matrix = await runner.run()

    assert matrix[
        "schema_version"
    ] == "v1"

    assert matrix[
        "fixture_mode"
    ] is True

    assert matrix[
        "read_only"
    ] is True

    assert matrix[
        "decision_influence"
    ] is False

    assert matrix[
        "scenario_count"
    ] == 5

    assert matrix[
        "expectations_passed"
    ] is True

    actual_statuses = {
        item[
            "scenario"
        ]: item[
            "actual_status"
        ]
        for item
        in matrix[
            "expectations"
        ]
    }

    assert actual_statuses == {
        "matrix_oom_killed": (
            "matched"
        ),
        "matrix_memory_pressure": (
            "mismatched"
        ),
        "matrix_restart_storm": (
            "matched"
        ),
        "matrix_memory_saturation": (
            "mismatched"
        ),
        (
            "matrix_deployment_"
            "regression_unproven"
        ): (
            "investigation_no_conclusion"
        ),
    }

    assert all(
        item["action"] is None
        for item
        in matrix["results"]
    )

    report = matrix[
        "investigation_report"
    ]

    metrics = report.metrics

    assert report.scenario_count == 5

    assert (
        report.comparison_present_count
        == 5
    )

    assert (
        report.missing_comparison_count
        == 0
    )

    assert metrics.total_samples == 5

    assert (
        metrics.valid_snapshot_count
        == 5
    )

    assert (
        metrics.invalid_snapshot_count
        == 0
    )

    assert (
        metrics.comparable_count
        == 4
    )

    assert metrics.matched_count == 2

    assert (
        metrics.mismatched_count
        == 2
    )

    assert (
        metrics.investigation_no_conclusion_count
        == 1
    )

    assert (
        metrics.comparison_coverage
        == 0.8
    )

    assert (
        metrics.investigation_conclusion_rate
        == 0.8
    )

    assert (
        metrics.agreement_rate
        == 0.5
    )

    assert (
        metrics.mismatch_rate
        == 0.5
    )

    assert (
        metrics.confidence_uplift_rate
        == 1.0
    )

    assert (
        metrics.mean_confidence_delta
        == 0.09
    )

    assert (
        metrics.trusted_evidence_ratio
        == 1.0
    )


@pytest.mark.asyncio
async def test_deployment_regression_fails_closed_without_revision_probe(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    runtime = (
        create_investigation_evaluation_runtime()
    )

    registry = (
        create_investigation_evaluation_scenario_registry()
    )

    runner = (
        InvestigationEvaluationMatrixRunner(
            runtime=runtime,
            registry=registry,
        )
    )

    matrix = await runner.run()

    target = next(
        item
        for item
        in matrix[
            "results"
        ]
        if item[
            "scenario"
        ]
        == (
            "matrix_deployment_"
            "regression_unproven"
        )
    )

    shadow = target[
        "context"
    ].metadata[
        "investigation_shadow"
    ]

    comparison = target[
        "context"
    ].metadata[
        "investigation_rca_comparison"
    ]

    assert shadow[
        "stop_reason"
    ] == "insufficient_evidence"

    assert shadow[
        "conclusion"
    ] is None

    assert comparison[
        "comparison_status"
    ] == (
        "investigation_no_conclusion"
    )

    assert comparison[
        "decision_influence"
    ] is False
