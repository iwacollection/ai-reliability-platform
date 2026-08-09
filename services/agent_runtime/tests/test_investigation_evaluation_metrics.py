import pytest

from services.agent_runtime.app.investigation.evaluation_metrics import (
    aggregate_rca_comparisons,
)


def sample(
    *,
    status: str,
    rca_available: bool = True,
    investigation_available: bool = True,
    rca_confidence: float | None = 0.8,
    investigation_confidence: float | None = 0.9,
    confidence_delta: float | None = None,
    evidence_count: int = 2,
    trusted_evidence_count: int = 2,
    conclusion_evidence_count: int = 2,
):
    comparable = status in {
        "matched",
        "mismatched",
    }

    if comparable and confidence_delta is None:
        if (
            rca_confidence is not None
            and investigation_confidence
            is not None
        ):
            confidence_delta = round(
                (
                    investigation_confidence
                    - rca_confidence
                ),
                6,
            )

    return {
        "schema_version": "v1",
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "available": comparable,
        "comparison_status": status,
        "rca": {
            "available": rca_available,
            "root_cause": (
                "rca root cause"
                if rca_available
                else None
            ),
            "confidence": (
                rca_confidence
                if rca_available
                else None
            ),
            "evidence_count": 1,
        },
        "investigation": {
            "available": (
                investigation_available
            ),
            "status": (
                "concluded"
                if investigation_available
                else "exhausted"
            ),
            "stop_reason": (
                "sufficient_evidence"
                if investigation_available
                else "max_iterations"
            ),
            "root_cause": (
                "investigation root cause"
                if investigation_available
                else None
            ),
            "confidence": (
                investigation_confidence
                if investigation_available
                else None
            ),
            "evidence_count": (
                evidence_count
            ),
            "trusted_evidence_count": (
                trusted_evidence_count
            ),
            "conclusion_evidence_count": (
                conclusion_evidence_count
            ),
        },
        "comparison": {
            "exact_match": (
                True
                if status == "matched"
                else (
                    False
                    if status == "mismatched"
                    else None
                )
            ),
            "normalized_text_match": (
                True
                if status == "matched"
                else (
                    False
                    if status == "mismatched"
                    else None
                )
            ),
            "confidence_delta": (
                confidence_delta
                if comparable
                else None
            ),
        },
    }


def test_aggregate_core_comparison_metrics():
    metrics = aggregate_rca_comparisons(
        [
            sample(
                status="matched",
                rca_confidence=0.80,
                investigation_confidence=0.90,
                confidence_delta=0.10,
                evidence_count=3,
                trusted_evidence_count=3,
                conclusion_evidence_count=2,
            ),
            sample(
                status="mismatched",
                rca_confidence=0.80,
                investigation_confidence=0.60,
                confidence_delta=-0.20,
                evidence_count=4,
                trusted_evidence_count=3,
                conclusion_evidence_count=3,
            ),
            sample(
                status="investigation_no_conclusion",
                investigation_available=False,
            ),
            sample(
                status="investigation_orchestration_failed",
                investigation_available=False,
            ),
        ]
    )

    assert metrics.total_samples == 4

    assert metrics.valid_snapshot_count == 4
    assert metrics.invalid_snapshot_count == 0

    assert metrics.comparable_count == 2

    assert metrics.matched_count == 1
    assert metrics.mismatched_count == 1

    assert metrics.comparison_coverage == 0.5

    assert metrics.agreement_rate == 0.5
    assert metrics.mismatch_rate == 0.5

    assert (
        metrics.investigation_conclusion_count
        == 2
    )

    assert (
        metrics.investigation_conclusion_rate
        == 0.5
    )

    assert metrics.rca_availability_rate == 1.0

    assert (
        metrics.orchestration_failure_rate
        == 0.25
    )

    assert (
        metrics.confidence_comparable_count
        == 2
    )

    assert (
        metrics.investigation_higher_confidence_count
        == 1
    )

    assert (
        metrics.investigation_lower_confidence_count
        == 1
    )

    assert metrics.confidence_uplift_rate == 0.5

    assert metrics.mean_confidence_delta == -0.05

    assert (
        metrics.mean_absolute_confidence_delta
        == 0.15
    )

    assert (
        metrics.mean_investigation_evidence_count
        == 3.5
    )

    assert (
        metrics.mean_trusted_evidence_count
        == 3.0
    )

    assert (
        metrics.mean_conclusion_evidence_count
        == 2.5
    )

    assert metrics.trusted_evidence_ratio == pytest.approx(
        6 / 7,
        abs=0.000001,
    )

    assert metrics.status_counts == {
        "investigation_no_conclusion": 1,
        "investigation_orchestration_failed": 1,
        "matched": 1,
        "mismatched": 1,
    }


def test_invalid_snapshots_are_counted_in_coverage_denominator():
    valid = sample(
        status="matched",
    )

    invalid = {
        **valid,
        "decision_influence": True,
    }

    metrics = aggregate_rca_comparisons(
        [
            valid,
            invalid,
            None,
        ]
    )

    assert metrics.total_samples == 3

    assert metrics.valid_snapshot_count == 1
    assert metrics.invalid_snapshot_count == 2

    assert metrics.comparison_coverage == pytest.approx(
        1 / 3,
        abs=0.000001,
    )


def test_zero_denominators_are_none():
    metrics = aggregate_rca_comparisons(
        []
    )

    assert metrics.total_samples == 0

    assert metrics.comparison_coverage is None
    assert metrics.agreement_rate is None
    assert metrics.mismatch_rate is None

    assert (
        metrics.investigation_conclusion_rate
        is None
    )

    assert metrics.rca_availability_rate is None

    assert (
        metrics.orchestration_failure_rate
        is None
    )

    assert metrics.confidence_uplift_rate is None

    assert metrics.mean_confidence_delta is None

    assert (
        metrics.mean_trusted_evidence_count
        is None
    )

    assert metrics.trusted_evidence_ratio is None


def test_rca_unavailable_is_measured_separately():
    metrics = aggregate_rca_comparisons(
        [
            sample(
                status="rca_unavailable",
                rca_available=False,
                investigation_available=True,
            )
        ]
    )

    assert metrics.valid_snapshot_count == 1

    assert metrics.rca_available_count == 0
    assert metrics.rca_unavailable_count == 1

    assert (
        metrics.investigation_conclusion_count
        == 1
    )

    assert metrics.comparable_count == 0
    assert metrics.comparison_coverage == 0.0

    assert metrics.agreement_rate is None


def test_metrics_never_retain_root_cause_or_raw_evidence():
    secret = (
        "https://user:secret-token@"
        "private.example.invalid"
    )

    value = sample(
        status="matched",
    )

    value["rca"]["root_cause"] = secret
    value["rca"]["raw_secret"] = secret

    value["investigation"][
        "root_cause"
    ] = secret

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    serialized = str(
        metrics.model_dump(
            mode="json"
        )
    )

    assert secret not in serialized


def test_matched_requires_available_comparison():
    value = sample(
        status="matched",
    )

    value["available"] = False

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1
    assert metrics.comparable_count == 0


def test_matched_requires_available_rca_and_investigation():
    value = sample(
        status="matched",
        rca_available=False,
        investigation_available=True,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_impossible_evidence_counts_are_invalid():
    value = sample(
        status="matched",
        evidence_count=1,
        trusted_evidence_count=2,
        conclusion_evidence_count=2,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1

    assert (
        metrics.mean_trusted_evidence_count
        is None
    )

    assert metrics.trusted_evidence_ratio is None


def test_conclusion_evidence_cannot_exceed_trusted_evidence():
    value = sample(
        status="matched",
        evidence_count=3,
        trusted_evidence_count=1,
        conclusion_evidence_count=2,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_unknown_status_is_invalid():
    value = sample(
        status="investigation_no_conclusion",
        investigation_available=False,
    )

    value[
        "comparison_status"
    ] = "unexpected_future_status"

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1

    assert metrics.status_counts == {}


@pytest.mark.parametrize(
    "bad_delta",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        99.0,
        -99.0,
    ],
)
def test_invalid_confidence_delta_invalidates_snapshot(
    bad_delta,
):
    value = sample(
        status="matched",
        confidence_delta=0.1,
    )

    value["comparison"][
        "confidence_delta"
    ] = bad_delta

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1

    assert (
        metrics.confidence_comparable_count
        == 0
    )


def test_confidence_delta_must_match_source_confidences():
    value = sample(
        status="matched",
        rca_confidence=0.8,
        investigation_confidence=0.9,
        confidence_delta=0.5,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_valid_confidence_delta_passes():
    value = sample(
        status="matched",
        rca_confidence=0.8,
        investigation_confidence=0.9,
        confidence_delta=0.1,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 1
    assert metrics.invalid_snapshot_count == 0

    assert metrics.comparable_count == 1

    assert metrics.mean_confidence_delta == 0.1


def test_orchestration_failure_cannot_claim_conclusion():
    value = sample(
        status="investigation_orchestration_failed",
        rca_available=True,
        investigation_available=True,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1

    assert (
        metrics.investigation_conclusion_count
        == 0
    )


def test_investigation_no_conclusion_requires_available_rca():
    value = sample(
        status="investigation_no_conclusion",
        rca_available=False,
        investigation_available=False,
    )

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_exact_match_cannot_conflict_with_normalized_match():
    value = sample(
        status="mismatched",
    )

    value["comparison"][
        "exact_match"
    ] = True

    value["comparison"][
        "normalized_text_match"
    ] = False

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_invalid_source_confidence_is_rejected():
    value = sample(
        status="matched",
    )

    value["rca"][
        "confidence"
    ] = float("nan")

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_unavailable_status_cannot_publish_comparison_result():
    value = sample(
        status="investigation_no_conclusion",
        investigation_available=False,
    )

    value["comparison"][
        "exact_match"
    ] = False

    metrics = aggregate_rca_comparisons(
        [
            value
        ]
    )

    assert metrics.valid_snapshot_count == 0
    assert metrics.invalid_snapshot_count == 1


def test_minimal_comparison_failed_snapshot_is_valid():
    metrics = aggregate_rca_comparisons(
        [
            {
                "schema_version": "v1",
                "shadow_mode": True,
                "read_only": True,
                "decision_influence": False,
                "available": False,
                "comparison_status": (
                    "comparison_failed"
                ),
                "failure_code": (
                    "RuntimeError"
                ),
            }
        ]
    )

    assert metrics.valid_snapshot_count == 1
    assert metrics.invalid_snapshot_count == 0

    assert metrics.comparison_failed_count == 1
    assert metrics.comparable_count == 0
