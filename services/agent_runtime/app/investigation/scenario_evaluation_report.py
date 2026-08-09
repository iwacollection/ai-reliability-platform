from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from services.agent_runtime.app.investigation.evaluation_metrics import (
    InvestigationEvaluationMetrics,
    aggregate_rca_comparisons,
)


_ALLOWED_COMPARISON_STATUSES = {
    "matched",
    "mismatched",
    "rca_unavailable",
    "investigation_no_conclusion",
    "investigation_orchestration_failed",
    "comparison_failed",
}

_MAX_SCENARIO_NAME_LENGTH = 256


class InvestigationScenarioEvaluationSample(
    BaseModel
):
    """
    Bounded per-scenario Shadow evaluation summary.

    Root-cause text, raw evidence, prompts, Tool responses and provider
    details are deliberately excluded.
    """

    scenario: str

    comparison_present: bool

    comparison_status: str | None = None


class InvestigationShadowEvaluationReport(
    BaseModel
):
    """
    Aggregate read-only report for Scenario Replay Investigation Shadow.

    This report is evaluation-only.

    It has no authority over:
    - RCA
    - Healing
    - Approval
    - Action
    - Verification
    - Incident lifecycle
    """

    schema_version: str = "v1"

    shadow_mode: bool = True

    read_only: bool = True

    decision_influence: bool = False

    scenario_count: int = 0

    comparison_present_count: int = 0

    missing_comparison_count: int = 0

    samples: list[
        InvestigationScenarioEvaluationSample
    ] = Field(
        default_factory=list
    )

    metrics: InvestigationEvaluationMetrics = Field(
        default_factory=(
            InvestigationEvaluationMetrics
        )
    )


def build_investigation_scenario_evaluation_report(
    replay_results: Iterable[Any],
) -> InvestigationShadowEvaluationReport:
    """
    Build one bounded Shadow report from ScenarioReplayEngine results.

    Every replay result contributes one Metrics denominator entry.

    Missing or malformed comparison snapshots are passed to the Metrics
    Aggregator as invalid samples rather than silently discarded. This keeps
    comparison coverage honest.

    The report never copies RCA root-cause text or Investigation evidence.
    """

    items = list(
        replay_results
    )

    comparisons: list[Any] = []

    samples: list[
        InvestigationScenarioEvaluationSample
    ] = []

    comparison_present_count = 0

    for item in items:

        scenario_name = (
            _scenario_name(
                item.get(
                    "scenario"
                )
            )
            if isinstance(
                item,
                Mapping,
            )
            else "unknown"
        )

        comparison = (
            _comparison_from_replay_result(
                item
            )
        )

        comparisons.append(
            comparison
        )

        comparison_present = isinstance(
            comparison,
            Mapping,
        )

        if comparison_present:
            comparison_present_count += 1

        samples.append(
            InvestigationScenarioEvaluationSample(
                scenario=scenario_name,
                comparison_present=(
                    comparison_present
                ),
                comparison_status=(
                    _comparison_status(
                        comparison
                    )
                ),
            )
        )

    metrics = aggregate_rca_comparisons(
        comparisons
    )

    return InvestigationShadowEvaluationReport(
        scenario_count=len(
            items
        ),
        comparison_present_count=(
            comparison_present_count
        ),
        missing_comparison_count=(
            len(
                items
            )
            - comparison_present_count
        ),
        samples=samples,
        metrics=metrics,
    )


def _comparison_from_replay_result(
    item: Any,
) -> Any:
    if not isinstance(
        item,
        Mapping,
    ):
        return None

    context = item.get(
        "context"
    )

    metadata = getattr(
        context,
        "metadata",
        None,
    )

    if not isinstance(
        metadata,
        Mapping,
    ):
        return None

    return metadata.get(
        "investigation_rca_comparison"
    )


def _scenario_name(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        return "unknown"

    normalized = value.strip()

    if not normalized:
        return "unknown"

    return normalized[
        :_MAX_SCENARIO_NAME_LENGTH
    ]


def _comparison_status(
    value: Any,
) -> str | None:
    if not isinstance(
        value,
        Mapping,
    ):
        return None

    status = value.get(
        "comparison_status"
    )

    if status not in (
        _ALLOWED_COMPARISON_STATUSES
    ):
        return None

    return status


__all__ = [
    "InvestigationScenarioEvaluationSample",
    "InvestigationShadowEvaluationReport",
    "build_investigation_scenario_evaluation_report",
]
