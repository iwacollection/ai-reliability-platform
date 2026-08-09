from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepStatus,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionNotFoundError,
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionConflictError,
    InvestigationSessionStore,
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


def _decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause="container memory pressure",
                confidence=0.5,
                missing_evidence=["pod state"],
            )
        ],
        rationale_summary="collect pod state",
        stop=False,
        next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
    )


def _next_state(
    claimed,
    *,
    now: datetime,
) -> InvestigationState:
    decision = _decision()
    return InvestigationState.model_validate(
        {
            **claimed.state.model_dump(mode="python"),
            "iteration_count": 1,
            "hypotheses": decision.hypotheses,
            "decision_summaries": [decision.rationale_summary],
            "updated_at": now,
        }
    )


async def _created_service(tmp_path):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "sessions.db"
        )
    )
    created = await service.create_or_get(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )
    return service, created.session


@pytest.mark.asyncio
async def test_create_and_read_replay(tmp_path):
    incident_id = uuid4()
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )
    service = InvestigationSessionService(store)

    first = await service.create_or_get(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )
    replay = await service.create_or_get(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )

    assert first.created is True
    assert replay.replayed is True
    assert await service.get(first.session.session_id) == first.session
    assert await service.get_by_run(
        incident_id=incident_id,
        run_key="automatic-shadow-v1",
    ) == first.session
    assert await service.list_by_incident(incident_id) == [first.session]


@pytest.mark.asyncio
async def test_require_missing_session_is_bounded(tmp_path):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "sessions.db"
        )
    )

    with pytest.raises(
        InvestigationSessionNotFoundError,
        match="not found",
    ):
        await service.require(uuid4())


@pytest.mark.asyncio
async def test_exact_claim_replay_grants_one_call(tmp_path):
    service, session = await _created_service(tmp_path)
    digest = canonical_digest({"state": session.state})

    first = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )
    replay = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )

    assert first.applied is True
    assert first.call_granted is True
    assert replay.replayed is True
    assert replay.call_granted is False
    assert replay.step == first.step


@pytest.mark.asyncio
async def test_cross_instance_concurrent_claim_has_one_call_grant(tmp_path):
    db_path = tmp_path / "sessions.db"
    creator = InvestigationSessionService(
        InvestigationSessionStore(db_path)
    )
    created = await creator.create_or_get(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )
    digest = canonical_digest({"state": created.session.state})
    services = [
        InvestigationSessionService(
            InvestigationSessionStore(db_path)
        )
        for _ in range(8)
    ]

    results = await asyncio.gather(
        *[
            service.claim_step(
                created.session.session_id,
                kind=InvestigationStepKind.REASONER,
                request_digest=digest,
                claimant="runtime-worker-1",
            )
            for service in services
        ]
    )

    assert sum(item.call_granted for item in results) == 1
    assert len({item.step.step_id for item in results}) == 1


@pytest.mark.asyncio
async def test_claim_identity_conflict_fails_closed(tmp_path):
    service, session = await _created_service(tmp_path)
    digest = canonical_digest({"state": session.state})
    await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )

    with pytest.raises(
        InvestigationSessionConflictError,
        match="idempotency conflict",
    ):
        await service.claim_step(
            session.session_id,
            kind=InvestigationStepKind.REASONER,
            request_digest=digest,
            claimant="runtime-worker-2",
        )


@pytest.mark.asyncio
async def test_exact_completion_replay_is_idempotent(tmp_path):
    service, session = await _created_service(tmp_path)
    now = datetime.now(UTC)
    digest = canonical_digest({"state": session.state})
    claim = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
        now=now,
    )
    decision = _decision()
    next_state = _next_state(
        claim.session,
        now=now + timedelta(seconds=1),
    )

    first = await service.complete_step(
        session.session_id,
        step_id=claim.step.step_id,
        request_digest=digest,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(seconds=1),
    )
    replay = await service.complete_step(
        session.session_id,
        step_id=claim.step.step_id,
        request_digest=digest,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(minutes=1),
    )

    assert first.applied is True
    assert replay.replayed is True
    assert replay.session == first.session
    assert replay.step == first.step
    assert replay.session.status == InvestigationSessionStatus.PAUSED


@pytest.mark.asyncio
async def test_concurrent_completion_applies_once(tmp_path):
    db_path = tmp_path / "sessions.db"
    creator = InvestigationSessionService(
        InvestigationSessionStore(db_path)
    )
    created = await creator.create_or_get(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
    )
    now = datetime.now(UTC)
    digest = canonical_digest({"state": created.session.state})
    claim = await creator.claim_step(
        created.session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
        now=now,
    )
    decision = _decision()
    next_state = _next_state(
        claim.session,
        now=now + timedelta(seconds=1),
    )
    services = [
        InvestigationSessionService(
            InvestigationSessionStore(db_path)
        )
        for _ in range(8)
    ]

    results = await asyncio.gather(
        *[
            service.complete_step(
                created.session.session_id,
                step_id=claim.step.step_id,
                request_digest=digest,
                outcome=InvestigationStepStatus.SUCCEEDED,
                next_state=next_state,
                decision=decision,
                now=now + timedelta(seconds=1),
            )
            for service in services
        ]
    )

    assert sum(item.applied for item in results) == 1
    assert all(item.session == results[0].session for item in results)


@pytest.mark.asyncio
async def test_completion_conflict_does_not_replace_persisted_outcome(tmp_path):
    service, session = await _created_service(tmp_path)
    now = datetime.now(UTC)
    digest = canonical_digest({"state": session.state})
    claim = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
        now=now,
    )
    decision = _decision()
    next_state = _next_state(
        claim.session,
        now=now + timedelta(seconds=1),
    )
    persisted = await service.complete_step(
        session.session_id,
        step_id=claim.step.step_id,
        request_digest=digest,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(seconds=1),
    )

    with pytest.raises(
        InvestigationSessionConflictError,
        match="idempotency conflict",
    ):
        await service.complete_step(
            session.session_id,
            step_id=claim.step.step_id,
            request_digest=digest,
            outcome=InvestigationStepStatus.FAILED,
            next_state=next_state,
            failure_code="reasoner.failed",
        )

    assert await service.get(session.session_id) == persisted.session


@pytest.mark.asyncio
async def test_indeterminate_completion_blocks_new_claim(tmp_path):
    service, session = await _created_service(tmp_path)
    now = datetime.now(UTC)
    digest = canonical_digest({"state": session.state})
    claim = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
        now=now,
    )
    next_state = claim.session.state.model_copy(
        update={
            "updated_at": now + timedelta(seconds=1),
        }
    )
    result = await service.complete_step(
        session.session_id,
        step_id=claim.step.step_id,
        request_digest=digest,
        outcome=InvestigationStepStatus.INDETERMINATE,
        next_state=next_state,
        failure_code="reasoner.outcome_unknown",
        now=now + timedelta(seconds=1),
    )

    assert result.session.status == InvestigationSessionStatus.INDETERMINATE
    assert result.session.automatic_resume_blocked is True
    with pytest.raises(
        ValueError,
        match="cannot claim",
    ):
        await service.claim_step(
            session.session_id,
            kind=InvestigationStepKind.REASONER,
            request_digest=canonical_digest({"next": True}),
            claimant="runtime-worker-1",
        )


@pytest.mark.asyncio
async def test_completed_claim_replay_never_grants_another_call(tmp_path):
    service, session = await _created_service(tmp_path)
    now = datetime.now(UTC)
    digest = canonical_digest({"state": session.state})
    claim = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
        now=now,
    )
    decision = _decision()
    next_state = _next_state(
        claim.session,
        now=now + timedelta(seconds=1),
    )
    await service.complete_step(
        session.session_id,
        step_id=claim.step.step_id,
        request_digest=digest,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now + timedelta(seconds=1),
    )

    replay = await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )

    assert replay.call_granted is False
    assert replay.step.status == InvestigationStepStatus.SUCCEEDED
