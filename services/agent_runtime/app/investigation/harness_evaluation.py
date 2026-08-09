from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from services.agent_runtime.app.investigation.evaluation_metrics import (
    aggregate_rca_comparisons,
)


class InvestigationHarnessEvaluation(
    BaseModel
):
    """
    Bounded per-Harness-case Investigation evaluation.

    The source comparison is validated through the same Metrics integrity
    boundary used by Scenario evaluation.

    This model deliberately excludes:

    - RCA root-cause text
    - Investigation root-cause text
    - raw evidence
    - prompts
    - LLM output
    - Tool responses
    - credentials
    - provider details

    It has no decision authority.
    """

    schema_version: str = "v1"

    shadow_mode: bool = True

    read_only: bool = True

    decision_influence: bool = False

    comparison_present: bool = False

    comparison_valid: bool = False

    comparison_status: str | None = None

    comparable: bool = False

    matched: bool | None = None

    investigation_concluded: bool = False

    confidence_delta: float | None = None

    investigation_evidence_count: int | None = None

    trusted_evidence_count: int | None = None

    conclusion_evidence_count: int | None = None

    trusted_evidence_ratio: float | None = None


def build_investigation_harness_evaluation(
    comparison_snapshot: Any,
) -> InvestigationHarnessEvaluation:
    """
    Convert one Runtime comparison snapshot into a bounded Harness summary.

    No nested comparison field is trusted directly.

    Instead, the existing Evaluation Metrics integrity validator processes the
    snapshot first. Only metrics derived from one valid sample are exposed.
    """

    comparison_present = isinstance(
        comparison_snapshot,
        Mapping,
    )

    metrics = aggregate_rca_comparisons(
        [
            comparison_snapshot
        ]
    )

    comparison_valid = (
        metrics.valid_snapshot_count == 1
        and metrics.invalid_snapshot_count == 0
    )

    if not comparison_valid:
        return InvestigationHarnessEvaluation(
            comparison_present=(
                comparison_present
            ),
            comparison_valid=False,
        )

    comparison_status = None

    if len(
        metrics.status_counts
    ) == 1:
        comparison_status = next(
            iter(
                metrics.status_counts
            )
        )

    comparable = (
        metrics.comparable_count == 1
    )

    matched: bool | None = None

    if comparable:
        if metrics.matched_count == 1:
            matched = True

        elif metrics.mismatched_count == 1:
            matched = False

    investigation_concluded = (
        metrics.investigation_conclusion_count
        == 1
    )

    return InvestigationHarnessEvaluation(
        comparison_present=(
            comparison_present
        ),
        comparison_valid=True,
        comparison_status=(
            comparison_status
        ),
        comparable=comparable,
        matched=matched,
        investigation_concluded=(
            investigation_concluded
        ),
        confidence_delta=(
            metrics.mean_confidence_delta
        ),
        investigation_evidence_count=(
            _single_count(
                metrics.mean_investigation_evidence_count
            )
        ),
        trusted_evidence_count=(
            _single_count(
                metrics.mean_trusted_evidence_count
            )
        ),
        conclusion_evidence_count=(
            _single_count(
                metrics.mean_conclusion_evidence_count
            )
        ),
        trusted_evidence_ratio=(
            metrics.trusted_evidence_ratio
        ),
    )


def _single_count(
    value: float | None,
) -> int | None:
    """
    Metrics means are exact integer counts when aggregation contains exactly
    one valid Investigation conclusion.
    """

    if value is None:
        return None

    if value < 0:
        return None

    if not value.is_integer():
        return None

    return int(
        value
    )


__all__ = [
    "InvestigationHarnessEvaluation",
    "build_investigation_harness_evaluation",
]
