from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepRecord,
    InvestigationStepStatus,
    build_investigation_session,
    canonical_digest,
    claim_investigation_step,
    complete_investigation_step,
    investigation_session_input_digest,
)


def _state(
    *,
    resource: str = "payment-api-abc",
) -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            resource=resource,
            namespace="payment",
            cluster="prod-a",
        )
    )


def _decision(
    *,
    stop: bool = False,
) -> InvestigationDecision:
    hypothesis = IncidentHypothesis(
        hypothesis_id="h1",
        cause="container memory pressure",
        confidence=(
            0.9
            if stop
            else 0.5
        ),
        supporting_evidence_ids=(
            ["evidence-1"]
            if stop
            else []
        ),
        missing_evidence=(
            []
            if stop
            else ["pod state"]
        ),
    )
    return InvestigationDecision(
        hypotheses=[hypothesis],
        rationale_summary=(
            "trusted evidence is sufficient"
            if stop
            else "collect pod state"
        ),
        stop=stop,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
            if stop
            else None
        ),
        next_probe=(
            None
            if stop
            else InvestigationProbe.KUBERNETES_POD_STATE
        ),
        conclusion=(
            InvestigationConclusion(
                root_cause="container memory pressure",
                confidence=0.9,
                evidence_ids=["evidence-1"],
            )
            if stop
            else None
        ),
    )


def _session_and_claim(
    *,
    kind: InvestigationStepKind = (
        InvestigationStepKind.REASONER
    ),
    probe: InvestigationProbe | None = None,
):
    now = datetime.now(UTC)
    session = build_investigation_session(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
        now=now,
    )
    claimed = claim_investigation_step(
        session,
        kind=kind,
        request_digest=canonical_digest(
            {"state": session.state}
        ),
        claimant="runtime-worker-1",
        probe=probe,
        now=now + timedelta(seconds=1),
    )
    return session, claimed, now


def test_session_identity_is_deterministic_and_input_bound():
    incident_id = uuid4()
    now = datetime.now(UTC)

    first = build_investigation_session(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
        initial_state=_state(),
        now=now,
    )
    replay = build_investigation_session(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
        initial_state=_state(),
        now=now + timedelta(minutes=1),
    )
    conflict = build_investigation_session(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
        initial_state=_state(
            resource="different-pod"
        ),
        now=now,
    )

    assert first.session_id == replay.session_id
    assert first.input_digest == replay.input_digest
    assert first.session_id == conflict.session_id
    assert first.input_digest != conflict.input_digest
    assert first.state.investigation_id == str(
        first.session_id
    )


def test_session_input_digest_excludes_volatile_timestamps():
    first = _state()
    second = first.model_copy(
        update={
            "investigation_id": "another-id",
            "started_at": (
                first.started_at
                + timedelta(hours=1)
            ),
            "updated_at": (
                first.updated_at
                + timedelta(hours=1)
            ),
        }
    )

    assert investigation_session_input_digest(
        first
    ) == investigation_session_input_digest(
        second
    )


def test_reasoner_claim_and_success_are_replayable():
    session, claimed, now = _session_and_claim()
    decision = _decision()
    next_state = InvestigationState.model_validate(
        {
            **claimed.state.model_dump(
                mode="python"
            ),
            "iteration_count": 1,
            "hypotheses": decision.hypotheses,
            "decision_summaries": [
                decision.rationale_summary
            ],
            "updated_at": (
                now + timedelta(seconds=2)
            ),
        }
    )
    completed = complete_investigation_step(
        claimed,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(seconds=2),
    )

    assert session.status == InvestigationSessionStatus.READY
    assert claimed.status == InvestigationSessionStatus.RUNNING
    assert completed.status == InvestigationSessionStatus.PAUSED
    assert completed.version == 2
    assert completed.steps[-1].decision == decision
    assert completed.steps[-1].output_digest == canonical_digest(
        decision
    )
    assert completed.automatic_resume_blocked is False


def test_probe_success_persists_only_bounded_evidence():
    _, claimed, now = _session_and_claim(
        kind=InvestigationStepKind.PROBE,
        probe=InvestigationProbe.KUBERNETES_POD_STATE,
    )
    evidence = EvidenceItem(
        evidence_id="evidence-1",
        probe=InvestigationProbe.KUBERNETES_POD_STATE,
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=0.95,
        observed_at=now,
        cluster="prod-a",
        cluster_verified=True,
        facts={
            "phase": "Running",
        },
    )
    next_state = InvestigationState.model_validate(
        {
            **claimed.state.model_dump(
                mode="python"
            ),
            "tool_call_count": 1,
            "attempted_probes": [
                InvestigationProbe.KUBERNETES_POD_STATE
            ],
            "evidence": [evidence],
            "updated_at": (
                now + timedelta(seconds=2)
            ),
        }
    )
    completed = complete_investigation_step(
        claimed,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        evidence=evidence,
        now=now + timedelta(seconds=2),
    )

    assert completed.steps[-1].evidence == evidence
    assert completed.steps[-1].decision is None
    assert set(
        type(
            completed.steps[-1]
        ).model_fields
    ).isdisjoint(
        {
            "prompt",
            "credential",
            "raw_response",
            "tool_arguments",
        }
    )


def test_indeterminate_step_blocks_automatic_resume():
    _, claimed, now = _session_and_claim()
    indeterminate = complete_investigation_step(
        claimed,
        outcome=(
            InvestigationStepStatus.INDETERMINATE
        ),
        next_state=claimed.state,
        failure_code="GatewayOutcomeUnknown",
        now=now + timedelta(seconds=2),
    )

    assert (
        indeterminate.status
        == InvestigationSessionStatus.INDETERMINATE
    )
    assert indeterminate.automatic_resume_blocked is True

    with pytest.raises(
        ValueError,
        match="cannot claim",
    ):
        claim_investigation_step(
            indeterminate,
            kind=InvestigationStepKind.REASONER,
            request_digest=canonical_digest(
                {"state": indeterminate.state}
            ),
            claimant="runtime-worker-1",
        )


def test_terminal_synthesis_maps_to_completed_session():
    _, claimed, now = _session_and_claim()
    decision = _decision(
        stop=True
    )
    next_state = InvestigationState.model_validate(
        {
            **claimed.state.model_dump(
                mode="python"
            ),
            "status": InvestigationStatus.CONCLUDED,
            "iteration_count": 1,
            "hypotheses": decision.hypotheses,
            "decision_summaries": [
                decision.rationale_summary
            ],
            "stop_reason": decision.stop_reason,
            "conclusion": decision.conclusion,
            "updated_at": (
                now + timedelta(seconds=2)
            ),
        }
    )
    completed = complete_investigation_step(
        claimed,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(seconds=2),
    )

    assert completed.status == InvestigationSessionStatus.COMPLETED
    assert completed.automatic_resume_blocked is True


def test_invalid_step_result_and_naive_clock_fail_closed():
    now = datetime.now(UTC)

    with pytest.raises(
        ValidationError,
        match="requires output_digest",
    ):
        InvestigationStepRecord(
            step_id=uuid4(),
            sequence=1,
            kind=InvestigationStepKind.REASONER,
            status=InvestigationStepStatus.SUCCEEDED,
            claimant="runtime-worker-1",
            request_digest="0" * 64,
            decision=_decision(),
            claimed_at=now,
            completed_at=now,
        )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        build_investigation_session(
            incident_id=uuid4(),
            run_key="automatic-shadow-v1",
            initial_state=_state(),
            now=datetime.now(),
        )


def test_models_are_frozen_and_reject_extra_fields():
    session = build_investigation_session(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )

    with pytest.raises(
        ValidationError,
    ):
        session.version = 99

    payload = session.model_dump(
        mode="python"
    )
    payload["llm_gateway"] = "forbidden"

    with pytest.raises(
        ValidationError,
        match="Extra inputs",
    ):
        InvestigationSessionRecord.model_validate(
            payload
        )
