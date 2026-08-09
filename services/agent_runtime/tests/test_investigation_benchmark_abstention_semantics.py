from __future__ import annotations

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkScenario,
    score_scenario,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    scenario_by_key,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="payment-api restarts are elevated",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def terminal_state(
    reason: InvestigationStopReason,
) -> InvestigationState:
    return InvestigationState(
        status=InvestigationStatus.CONCLUDED,
        scope=scope(),
        stop_reason=reason,
        conclusion=None,
    )


def test_backend_failure_accepts_no_safe_probe_as_safe_abstention():
    scenario = scenario_by_key(
        "probe_backend_failure"
    )

    assert (
        scenario.hidden_expected_stop_reason
        == InvestigationStopReason.INSUFFICIENT_EVIDENCE
    )

    assert (
        InvestigationStopReason.NO_SAFE_PROBE
        in scenario.hidden_acceptable_stop_reasons
    )

    score = score_scenario(
        scenario=scenario,
        state=terminal_state(
            InvestigationStopReason.NO_SAFE_PROBE
        ),
        decisions=[],
    )

    assert score.outcome_correct is True
    assert score.grounding_correct is True
    assert (
        score.root_cause_or_abstention_correct
        is True
    )

    assert any(
        "alternate safe abstention"
        in note
        for note in score.notes
    )


def test_backend_failure_still_accepts_primary_insufficient_evidence():
    scenario = scenario_by_key(
        "probe_backend_failure"
    )

    score = score_scenario(
        scenario=scenario,
        state=terminal_state(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
        decisions=[],
    )

    assert score.outcome_correct is True
    assert score.grounding_correct is True


def test_backend_failure_does_not_accept_runtime_exhaustion_as_abstention():
    scenario = scenario_by_key(
        "probe_backend_failure"
    )

    for reason in (
        InvestigationStopReason.TIMEOUT,
        InvestigationStopReason.MAX_TOOL_CALLS,
        InvestigationStopReason.MAX_ITERATIONS,
        InvestigationStopReason.DUPLICATE_PROBE,
        InvestigationStopReason.REASONER_ERROR,
    ):
        score = score_scenario(
            scenario=scenario,
            state=terminal_state(
                reason
            ),
            decisions=[],
        )

        assert score.outcome_correct is False


def test_default_scenario_has_no_alternate_stop_reason():
    scenario = BenchmarkScenario(
        key="strict-abstention",
        title="strict-abstention",
        alert_name="A",
        alert_message="A",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    assert (
        scenario.hidden_acceptable_stop_reasons
        == []
    )

    score = score_scenario(
        scenario=scenario,
        state=terminal_state(
            InvestigationStopReason.NO_SAFE_PROBE
        ),
        decisions=[],
    )

    assert score.outcome_correct is False
