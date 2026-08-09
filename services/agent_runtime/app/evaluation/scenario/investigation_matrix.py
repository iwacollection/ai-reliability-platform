from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)
from services.agent_runtime.app.evaluation.scenario.registry import (
    ScenarioRegistry,
)


def create_investigation_evaluation_scenario_registry(
) -> ScenarioRegistry:
    """
    Create the dedicated Investigation evaluation matrix.

    This registry is separate from create_scenario_registry(), so the
    existing replay/regression scenario contract remains unchanged.
    """

    registry = ScenarioRegistry()

    for scenario in (
        _oom_killed(),
        _memory_pressure(),
        _restart_storm(),
        _memory_saturation(),
        _deployment_regression_unproven(),
    ):
        registry.register(
            scenario
        )

    return registry


def _base_scenario(
    *,
    name: str,
    description: str,
    resource: str,
    expected_comparison_status: str,
) -> ScenarioDefinition:
    return ScenarioDefinition(
        name=name,
        description=description,
        event={
            "alertname": (
                "PodMemoryIncident"
            ),
            "severity": "critical",
            "resource": resource,
            "namespace": "payment",
            "cluster": "production-a",
        },
        expected={},
        metadata={
            "category": (
                "investigation_matrix"
            ),
            "fixture_mode": True,
            "expected_comparison_status": (
                expected_comparison_status
            ),
        },
    )


def _oom_killed(
) -> ScenarioDefinition:
    return _base_scenario(
        name="matrix_oom_killed",
        description=(
            "Container exceeded its memory limit "
            "and was OOMKilled."
        ),
        resource="payment-api",
        expected_comparison_status="matched",
    )


def _memory_pressure(
) -> ScenarioDefinition:
    return _base_scenario(
        name="matrix_memory_pressure",
        description=(
            "Container is repeatedly exhausting "
            "available memory headroom."
        ),
        resource="memory-pressure-api",
        expected_comparison_status=(
            "mismatched"
        ),
    )


def _restart_storm(
) -> ScenarioDefinition:
    return _base_scenario(
        name="matrix_restart_storm",
        description=(
            "Pod is experiencing a restart storm "
            "after repeated OOMKilled events."
        ),
        resource="restart-storm-api",
        expected_comparison_status="matched",
    )


def _memory_saturation(
) -> ScenarioDefinition:
    return _base_scenario(
        name="matrix_memory_saturation",
        description=(
            "Container memory is persistently near "
            "its configured limit without an OOM."
        ),
        resource="memory-saturation-api",
        expected_comparison_status=(
            "mismatched"
        ),
    )


def _deployment_regression_unproven(
) -> ScenarioDefinition:
    return _base_scenario(
        name=(
            "matrix_deployment_regression_unproven"
        ),
        description=(
            "A recent deployment may have increased "
            "memory usage, but revision history is "
            "not available to Investigation probes."
        ),
        resource="deployment-regression-api",
        expected_comparison_status=(
            "investigation_no_conclusion"
        ),
    )


__all__ = [
    "create_investigation_evaluation_scenario_registry",
]
