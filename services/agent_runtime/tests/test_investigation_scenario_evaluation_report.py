from types import SimpleNamespace

import pytest

from services.agent_runtime.app.evaluation.runner import (
    ScenarioRunner,
)
from services.agent_runtime.app.investigation.scenario_evaluation_report import (
    build_investigation_scenario_evaluation_report,
)


def comparison(
    *,
    status: str,
    rca_confidence: float = 0.8,
    investigation_confidence: float = 0.9,
):
    available = status in {
        "matched",
        "mismatched",
    }

    investigation_available = (
        status
        not in {
            "investigation_no_conclusion",
            "investigation_orchestration_failed",
        }
    )

    rca_available = (
        status
        != "rca_unavailable"
    )

    confidence_delta = (
        round(
            (
                investigation_confidence
                - rca_confidence
            ),
            6,
        )
        if available
        else None
    )

    return {
        "schema_version": "v1",
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "available": available,
        "comparison_status": status,
        "rca": {
            "available": rca_available,
            "root_cause": (
                "legacy RCA"
                if rca_available
                else None
            ),
            "confidence": (
                rca_confidence
                if rca_available
                else None
            ),
            "evidence_count": 2,
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
                "shadow RCA"
                if investigation_available
                else None
            ),
            "confidence": (
                investigation_confidence
                if investigation_available
                else None
            ),
            "evidence_count": (
                3
                if investigation_available
                else 0
            ),
            "trusted_evidence_count": (
                3
                if investigation_available
                else 0
            ),
            "conclusion_evidence_count": (
                2
                if investigation_available
                else 0
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
            ),
        },
    }


def replay_result(
    *,
    scenario: str,
    comparison_snapshot=None,
):
    metadata = {}

    if comparison_snapshot is not None:
        metadata[
            "investigation_rca_comparison"
        ] = comparison_snapshot

    return {
        "scenario": scenario,
        "results": [],
        "context": SimpleNamespace(
            metadata=metadata
        ),
        "action": None,
    }


def test_report_aggregates_replay_comparisons_without_raw_rca_text():
    secret = (
        "https://user:secret-token@"
        "private.example.invalid"
    )

    matched = comparison(
        status="matched",
        rca_confidence=0.8,
        investigation_confidence=0.9,
    )

    matched["rca"][
        "root_cause"
    ] = secret

    matched["investigation"][
        "root_cause"
    ] = secret

    mismatched = comparison(
        status="mismatched",
        rca_confidence=0.8,
        investigation_confidence=0.6,
    )

    results = [
        replay_result(
            scenario="matched-case",
            comparison_snapshot=matched,
        ),
        replay_result(
            scenario="mismatched-case",
            comparison_snapshot=mismatched,
        ),
        replay_result(
            scenario="missing-shadow-case",
            comparison_snapshot=None,
        ),
    ]

    report = (
        build_investigation_scenario_evaluation_report(
            results
        )
    )

    assert report.schema_version == "v1"
    assert report.shadow_mode is True
    assert report.read_only is True
    assert report.decision_influence is False

    assert report.scenario_count == 3

    assert (
        report.comparison_present_count
        == 2
    )

    assert (
        report.missing_comparison_count
        == 1
    )

    assert report.metrics.total_samples == 3

    assert (
        report.metrics.valid_snapshot_count
        == 2
    )

    assert (
        report.metrics.invalid_snapshot_count
        == 1
    )

    assert (
        report.metrics.comparable_count
        == 2
    )

    assert report.metrics.matched_count == 1
    assert report.metrics.mismatched_count == 1

    assert (
        report.metrics.comparison_coverage
        == pytest.approx(
            2 / 3,
            abs=0.000001,
        )
    )

    assert (
        report.metrics.agreement_rate
        == 0.5
    )

    assert (
        report.metrics.mismatch_rate
        == 0.5
    )

    assert (
        report.metrics.mean_confidence_delta
        == -0.05
    )

    assert report.samples[
        0
    ].comparison_status == "matched"

    assert report.samples[
        1
    ].comparison_status == "mismatched"

    assert report.samples[
        2
    ].comparison_present is False

    assert report.samples[
        2
    ].comparison_status is None

    serialized = str(
        report.model_dump(
            mode="json"
        )
    )

    assert secret not in serialized


def test_unknown_comparison_status_is_not_exposed():
    bad = comparison(
        status="matched"
    )

    bad[
        "comparison_status"
    ] = "secret-provider-value"

    report = (
        build_investigation_scenario_evaluation_report(
            [
                replay_result(
                    scenario="bad-status-case",
                    comparison_snapshot=bad,
                )
            ]
        )
    )

    assert report.scenario_count == 1

    assert (
        report.samples[0]
        .comparison_present
        is True
    )

    assert (
        report.samples[0]
        .comparison_status
        is None
    )

    assert (
        report.metrics.invalid_snapshot_count
        == 1
    )


class FakeScenario:
    def __init__(
        self,
        name: str,
    ):
        self.name = name


class FakeRegistry:
    def __init__(
        self,
        names,
    ):
        self._scenarios = [
            FakeScenario(
                name
            )
            for name in names
        ]

    def list(
        self,
    ):
        return list(
            self._scenarios
        )

    def get(
        self,
        name,
    ):
        for scenario in self._scenarios:
            if scenario.name == name:
                return scenario

        raise KeyError(
            name
        )


class FakeReplayEngine:
    def __init__(
        self,
    ):
        self.calls = []

    async def replay(
        self,
        scenario,
    ):
        self.calls.append(
            scenario.name
        )

        if scenario.name == "case-a":
            snapshot = comparison(
                status="matched",
                rca_confidence=0.8,
                investigation_confidence=0.9,
            )

        else:
            snapshot = comparison(
                status=(
                    "investigation_no_conclusion"
                ),
            )

        return replay_result(
            scenario=scenario.name,
            comparison_snapshot=snapshot,
        )


@pytest.mark.asyncio
async def test_runner_adds_report_without_changing_run_all_contract():
    registry = FakeRegistry(
        [
            "case-a",
            "case-b",
        ]
    )

    replay_engine = (
        FakeReplayEngine()
    )

    runner = ScenarioRunner(
        registry,
        replay_engine,
    )

    original_results = await (
        runner.run_all()
    )

    assert isinstance(
        original_results,
        list,
    )

    assert [
        item["scenario"]
        for item in original_results
    ] == [
        "case-a",
        "case-b",
    ]

    wrapped = await (
        runner.run_all_with_investigation_report()
    )

    assert set(
        wrapped.keys()
    ) == {
        "results",
        "investigation_report",
    }

    assert [
        item["scenario"]
        for item in wrapped[
            "results"
        ]
    ] == [
        "case-a",
        "case-b",
    ]

    report = wrapped[
        "investigation_report"
    ]

    assert report.scenario_count == 2

    assert (
        report.metrics.total_samples
        == 2
    )

    assert (
        report.metrics.valid_snapshot_count
        == 2
    )

    assert (
        report.metrics.matched_count
        == 1
    )

    assert (
        report.metrics.investigation_no_conclusion_count
        == 1
    )


def test_report_has_zero_decision_authority():
    report = (
        build_investigation_scenario_evaluation_report(
            []
        )
    )

    assert report.shadow_mode is True
    assert report.read_only is True
    assert report.decision_influence is False

    serialized = str(
        report.model_dump(
            mode="json"
        )
    )

    for forbidden in (
        "approval_id",
        "action_plan",
        "healing_action",
        "verification_result",
    ):
        assert forbidden not in serialized
