from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)


NOW = datetime(
    2026,
    8,
    9,
    15,
    0,
    tzinfo=UTC,
)


def hypothesis() -> IncidentHypothesis:
    return IncidentHypothesis(
        hypothesis_id="memory_limit_too_low",
        cause="Container memory limit is too low",
        confidence=0.5,
        missing_evidence=[
            "kubernetes pod state"
        ],
    )


def test_state_is_always_shadow_and_read_only():
    state = InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="Pod restarted",
            resource="payment-api-abc",
            namespace="payment",
            cluster="production-a",
        )
    )

    assert state.shadow_mode is True
    assert state.read_only is True

    with pytest.raises(ValidationError):
        InvestigationState(
            shadow_mode=False,
            scope=state.scope,
        )


def test_continuing_decision_requires_one_probe():
    decision = InvestigationDecision(
        hypotheses=[hypothesis()],
        rationale_summary=(
            "Pod termination state is still missing"
        ),
        next_probe=(
            InvestigationProbe.KUBERNETES_POD_STATE
        ),
    )

    assert decision.stop is False
    assert decision.conclusion is None

    with pytest.raises(
        ValidationError,
        match="requires a probe",
    ):
        InvestigationDecision(
            hypotheses=[hypothesis()],
            rationale_summary="Need more evidence",
        )


def test_terminal_sufficient_evidence_requires_conclusion():
    with pytest.raises(
        ValidationError,
        match="requires a conclusion",
    ):
        InvestigationDecision(
            hypotheses=[hypothesis()],
            rationale_summary="Evidence is enough",
            stop=True,
            stop_reason=(
                InvestigationStopReason.SUFFICIENT_EVIDENCE
            ),
        )

    decision = InvestigationDecision(
        hypotheses=[hypothesis()],
        rationale_summary="OOM evidence is confirmed",
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "Container exceeded its memory limit"
            ),
            confidence=0.94,
            evidence_ids=["k8s-1"],
        ),
    )

    assert decision.next_probe is None
    assert decision.conclusion is not None


def test_unknown_probe_is_rejected():
    with pytest.raises(ValidationError):
        InvestigationDecision.model_validate(
            {
                "hypotheses": [
                    hypothesis().model_dump()
                ],
                "rationale_summary": (
                    "Attempt a write"
                ),
                "stop": False,
                "next_probe": "kubernetes_patch",
            }
        )


def test_reasoner_cannot_claim_internal_stop_reason():
    with pytest.raises(
        ValidationError,
        match="internal stop reason",
    ):
        InvestigationDecision(
            hypotheses=[hypothesis()],
            rationale_summary="Claim coordinator timeout",
            stop=True,
            stop_reason=(
                InvestigationStopReason.TIMEOUT
            ),
        )


def test_trusted_evidence_requires_production_signal():
    with pytest.raises(
        ValidationError,
        match="production signal",
    ):
        EvidenceItem(
            probe=(
                InvestigationProbe.KUBERNETES_POD_STATE
            ),
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=False,
            reliability=1.0,
            observed_at=NOW,
            facts={
                "oom_killed": True
            },
        )


def test_failed_evidence_has_bounded_error_code_only():
    evidence = EvidenceItem(
        evidence_id="error-1",
        probe=(
            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT
        ),
        source="investigation_probe",
        success=False,
        trusted=False,
        production_signal=False,
        reliability=0.0,
        observed_at=NOW,
        error_code="PrometheusQueryError",
    )

    payload = evidence.model_dump(
        mode="json"
    )

    assert payload["error_code"] == (
        "PrometheusQueryError"
    )
    assert "error" not in payload
    assert "message" not in payload
