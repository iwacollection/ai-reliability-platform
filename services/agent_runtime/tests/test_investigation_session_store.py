from __future__ import annotations

import asyncio
import sqlite3

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from pydantic import ValidationError

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
    build_investigation_session,
    canonical_digest,
    claim_investigation_step,
    complete_investigation_step,
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


def _session(
    *,
    incident_id: UUID | None = None,
    run_key: str = "automatic-shadow-v1",
    resource: str = "payment-api-abc",
    now: datetime | None = None,
):
    return build_investigation_session(
        incident_id=(
            incident_id
            or uuid4()
        ),
        run_key=run_key,
        initial_state=_state(
            resource=resource
        ),
        now=now,
    )


def _claim(
    session,
    *,
    now: datetime,
):
    return claim_investigation_step(
        session,
        kind=InvestigationStepKind.REASONER,
        request_digest=canonical_digest(
            {"state": session.state}
        ),
        claimant="runtime-worker-1",
        now=now,
    )


def _pause(
    claimed,
    *,
    now: datetime,
):
    decision = InvestigationDecision(
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
        next_probe=(
            InvestigationProbe.KUBERNETES_POD_STATE
        ),
    )
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
            "updated_at": now,
        }
    )
    return complete_investigation_step(
        claimed,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=decision,
        now=now,
    )


@pytest.mark.asyncio
async def test_create_replay_survives_store_restart(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = _session()

    first = InvestigationSessionStore(
        db_path
    )
    created = await first.create_or_get(
        session
    )

    restarted = InvestigationSessionStore(
        db_path
    )
    replay = await restarted.create_or_get(
        session
    )
    loaded = await restarted.get(
        session.session_id
    )

    assert created.created is True
    assert replay.replayed is True
    assert replay.session == created.session
    assert loaded == created.session


@pytest.mark.asyncio
async def test_cross_instance_concurrent_create_has_one_winner(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = _session()
    stores = [
        InvestigationSessionStore(
            db_path
        )
        for _ in range(8)
    ]

    results = await asyncio.gather(
        *[
            store.create_or_get(
                session
            )
            for store in stores
        ]
    )

    assert sum(
        item.created
        for item in results
    ) == 1
    assert all(
        item.session == session
        for item in results
    )


@pytest.mark.asyncio
async def test_same_run_key_with_changed_scope_fails_closed(tmp_path):
    incident_id = uuid4()
    original = _session(
        incident_id=incident_id,
        resource="payment-api-abc",
    )
    conflict = _session(
        incident_id=incident_id,
        resource="different-pod",
    )
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )

    await store.create_or_get(
        original
    )

    with pytest.raises(
        InvestigationSessionConflictError,
        match="idempotency conflict",
    ):
        await store.create_or_get(
            conflict
        )

    assert await store.get(
        original.session_id
    ) == original


@pytest.mark.asyncio
async def test_cross_instance_cas_allows_one_step_claim(tmp_path):
    db_path = tmp_path / "sessions.db"
    now = datetime.now(UTC)
    session = _session(
        now=now
    )
    claimed = _claim(
        session,
        now=now + timedelta(seconds=1),
    )
    first = InvestigationSessionStore(
        db_path
    )
    second = InvestigationSessionStore(
        db_path
    )
    await first.create_or_get(
        session
    )

    results = await asyncio.gather(
        first.compare_and_swap(
            claimed,
            expected_version=0,
        ),
        second.compare_and_swap(
            claimed,
            expected_version=0,
        ),
        return_exceptions=True,
    )

    assert sum(
        not isinstance(
            item,
            Exception,
        )
        for item in results
    ) == 1
    assert sum(
        isinstance(
            item,
            InvestigationSessionConflictError,
        )
        for item in results
    ) == 1

    persisted = await first.get(
        session.session_id
    )
    assert persisted == claimed
    assert len(
        persisted.steps
    ) == 1


@pytest.mark.asyncio
async def test_claim_completion_is_atomic_and_replayable(tmp_path):
    now = datetime.now(UTC)
    session = _session(
        now=now
    )
    claimed = _claim(
        session,
        now=now + timedelta(seconds=1),
    )
    paused = _pause(
        claimed,
        now=now + timedelta(seconds=2),
    )
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )

    await store.create_or_get(
        session
    )
    await store.compare_and_swap(
        claimed,
        expected_version=0,
    )
    persisted = await store.compare_and_swap(
        paused,
        expected_version=1,
    )

    assert persisted.status == InvestigationSessionStatus.PAUSED
    assert persisted.version == 2
    assert (
        persisted.steps[-1].status
        == InvestigationStepStatus.SUCCEEDED
    )
    assert persisted.steps[-1].decision is not None

    with pytest.raises(
        InvestigationSessionConflictError,
        match="version conflict",
    ):
        await store.compare_and_swap(
            paused,
            expected_version=1,
        )


@pytest.mark.asyncio
async def test_non_append_only_step_ledger_is_rejected(tmp_path):
    now = datetime.now(UTC)
    session = _session(
        now=now
    )
    claimed = _claim(
        session,
        now=now + timedelta(seconds=1),
    )
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )
    await store.create_or_get(
        session
    )

    forged = claimed.model_copy(
        update={
            "steps": (),
        }
    )

    with pytest.raises(
        ValidationError,
    ):
        await store.compare_and_swap(
            forged,
            expected_version=0,
        )

    assert await store.get(
        session.session_id
    ) == session


@pytest.mark.asyncio
async def test_indeterminate_session_cannot_advance_automatically(tmp_path):
    now = datetime.now(UTC)
    session = _session(
        now=now
    )
    claimed = _claim(
        session,
        now=now + timedelta(seconds=1),
    )
    indeterminate = complete_investigation_step(
        claimed,
        outcome=InvestigationStepStatus.INDETERMINATE,
        next_state=claimed.state,
        failure_code="GatewayOutcomeUnknown",
        now=now + timedelta(seconds=2),
    )
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )
    await store.create_or_get(
        session
    )
    await store.compare_and_swap(
        claimed,
        expected_version=0,
    )
    await store.compare_and_swap(
        indeterminate,
        expected_version=1,
    )

    forged_next_version = indeterminate.model_copy(
        update={
            "version": 3,
            "updated_at": (
                now + timedelta(seconds=3)
            ),
        }
    )

    with pytest.raises(
        InvestigationSessionConflictError,
        match="blocked",
    ):
        await store.compare_and_swap(
            forged_next_version,
            expected_version=2,
        )


@pytest.mark.asyncio
async def test_list_by_incident_is_ordered_and_scoped(tmp_path):
    incident_id = uuid4()
    other_incident_id = uuid4()
    now = datetime.now(UTC)
    first = _session(
        incident_id=incident_id,
        run_key="run-1",
        now=now,
    )
    second = _session(
        incident_id=incident_id,
        run_key="run-2",
        now=now + timedelta(seconds=1),
    )
    other = _session(
        incident_id=other_incident_id,
        run_key="run-1",
        now=now,
    )
    store = InvestigationSessionStore(
        tmp_path / "sessions.db"
    )

    await store.create_or_get(
        second
    )
    await store.create_or_get(
        other
    )
    await store.create_or_get(
        first
    )

    assert await store.list_by_incident(
        incident_id
    ) == [
        first,
        second,
    ]


def test_schema_has_unique_run_identity_and_version_column(tmp_path):
    db_path = tmp_path / "sessions.db"
    InvestigationSessionStore(
        db_path
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(investigation_sessions)"
            ).fetchall()
        }
        indexes = connection.execute(
            "PRAGMA index_list(investigation_sessions)"
        ).fetchall()

    assert {
        "session_id",
        "incident_id",
        "run_key",
        "input_digest",
        "status",
        "version",
        "session_data",
        "created_at",
        "updated_at",
    }.issubset(columns)
    assert any(
        row[2] == 1
        for row in indexes
    )
