from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.investigation.engine_shadow_evaluation_models import (
    INVESTIGATION_SHADOW_SEMANTIC_REVIEW_ACKNOWLEDGEMENT,
    InvestigationEngineShadowPromotionPolicy,
    InvestigationEngineShadowPromotionReason,
    InvestigationEngineShadowPromotionStatus,
    InvestigationEngineShadowSemanticReviewEvidence,
)
from services.agent_runtime.app.investigation.engine_shadow_evaluation_service import (
    InvestigationEngineShadowEvaluationError,
    InvestigationEngineShadowEvaluationService,
)
from services.agent_runtime.app.investigation.engine_shadow_evaluation_store import (
    InvestigationEngineShadowEvaluationStore,
)
from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowGateDecision,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationStepKind,
    InvestigationStepStatus,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)

INCIDENT_ID = UUID("00000000-0000-4000-8000-000000000851")
NOW = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)


def decision() -> InvestigationEngineShadowGateDecision:
    return InvestigationEngineShadowGateDecision(
        allowed=True,
        code=InvestigationEngineShadowGateCode.ALLOWED,
        sample_rate=0.01,
        max_concurrent_sessions=1,
        matrix_digest="a" * 64,
        release_digest="b" * 64,
    )


def policy() -> InvestigationEngineShadowPromotionPolicy:
    return InvestigationEngineShadowPromotionPolicy(
        max_distinct_inputs=20,
        minimum_matched_pairs=10,
        minimum_semantic_reviewed_pairs=10,
    )


def session_service(path) -> InvestigationSessionService:
    return InvestigationSessionService(InvestigationSessionStore(path))


def initial_state(index: int) -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message=f"sensitive alert {index}",
            event_occurred_at=NOW + timedelta(seconds=index),
            resource=f"payment-api-{index:02d}",
            namespace="payment",
            cluster="prod-a",
        ),
        started_at=NOW,
        updated_at=NOW,
    )


def terminal_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause="bounded memory pressure",
                confidence=0.5,
                missing_evidence=["more evidence"],
            )
        ],
        rationale_summary="bounded investigation stopped safely",
        stop=True,
        stop_reason=InvestigationStopReason.INSUFFICIENT_EVIDENCE,
    )


async def create_terminal_session(
    service: InvestigationSessionService,
    *,
    index: int,
    prefix: str,
):
    created = await service.create_or_get(
        incident_id=INCIDENT_ID,
        run_key=f"{prefix}-{index:02d}",
        initial_state=initial_state(index),
        created_by=prefix,
        now=NOW + timedelta(minutes=index),
    )
    request_digest = canonical_digest({"state": created.session.state})
    claimed = await service.claim_step(
        created.session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=request_digest,
        claimant=f"{prefix}-worker",
        now=NOW + timedelta(minutes=index, seconds=1),
    )
    outcome = terminal_decision()
    next_state = InvestigationState.model_validate(
        {
            **claimed.session.state.model_dump(mode="python"),
            "status": InvestigationStatus.CONCLUDED,
            "iteration_count": 1,
            "hypotheses": outcome.hypotheses,
            "decision_summaries": [outcome.rationale_summary],
            "stop_reason": outcome.stop_reason,
            "updated_at": NOW + timedelta(minutes=index, seconds=2),
        }
    )
    completed = await service.complete_step(
        created.session.session_id,
        step_id=claimed.step.step_id,
        request_digest=request_digest,
        outcome=InvestigationStepStatus.SUCCEEDED,
        next_state=next_state,
        decision=outcome,
        now=NOW + timedelta(minutes=index, seconds=2),
    )
    return completed.session


async def services_with_pairs(tmp_path, count=10):
    primary = session_service(tmp_path / "primary.db")
    shadow = session_service(tmp_path / "shadow.db")
    for index in range(count):
        await create_terminal_session(primary, index=index, prefix="primary")
        await create_terminal_session(shadow, index=index, prefix="shadow")
    return primary, shadow


def evaluation_service(
    tmp_path,
    primary,
    shadow,
    *,
    store_name="evaluations.db",
):
    return InvestigationEngineShadowEvaluationService(
        store=InvestigationEngineShadowEvaluationStore(tmp_path / store_name),
        primary_service=primary,
        shadow_service=shadow,
        decision=decision(),
        policy=policy(),
        utc_clock=lambda: NOW + timedelta(hours=1),
    )


def review(snapshot, *, equivalent=10):
    return InvestigationEngineShadowSemanticReviewEvidence(
        source_window_digest=snapshot.source_window_digest,
        reviewer_id="shadow-reviewer-1",
        reviewed_pairs=10,
        semantically_equivalent_pairs=equivalent,
        review_evidence_digest="c" * 64,
        reviewed_at=NOW + timedelta(minutes=59),
        acknowledgement=(
            INVESTIGATION_SHADOW_SEMANTIC_REVIEW_ACKNOWLEDGEMENT
        ),
    )


def test_promotion_policy_is_bounded_and_fail_closed():
    assert InvestigationEngineShadowPromotionPolicy().minimum_matched_pairs == 20
    with pytest.raises(ValidationError, match="matched-pair threshold"):
        InvestigationEngineShadowPromotionPolicy(
            max_distinct_inputs=20,
            minimum_matched_pairs=21,
        )


@pytest.mark.asyncio
async def test_insufficient_window_is_durable_no_go_and_exact_replay(tmp_path):
    primary, shadow = await services_with_pairs(tmp_path, count=2)
    service = evaluation_service(tmp_path, primary, shadow)

    first = await service.evaluate(INCIDENT_ID)
    replay = await service.evaluate(INCIDENT_ID)

    assert first.created is True
    assert replay.created is False
    assert replay.snapshot == first.snapshot
    assert first.snapshot.promotion_status == (
        InvestigationEngineShadowPromotionStatus.NO_GO
    )
    assert (
        InvestigationEngineShadowPromotionReason.INSUFFICIENT_MATCHED_PAIRS
        in first.snapshot.promotion_reasons
    )
    assert first.snapshot.recommended_sample_rate is None


@pytest.mark.asyncio
async def test_protocol_parity_requires_semantic_review(tmp_path):
    primary, shadow = await services_with_pairs(tmp_path)
    service = evaluation_service(tmp_path, primary, shadow)

    result = await service.evaluate(INCIDENT_ID)

    assert result.snapshot.matched_pairs == 10
    assert result.snapshot.protocol_matched_pairs == 10
    assert result.snapshot.protocol_mismatched_pairs == 0
    assert result.snapshot.promotion_status == (
        InvestigationEngineShadowPromotionStatus.SEMANTIC_REVIEW_REQUIRED
    )
    assert result.snapshot.promotion_reasons == (
        InvestigationEngineShadowPromotionReason.SEMANTIC_REVIEW_MISSING,
    )
    assert result.snapshot.applies_configuration is False
    assert result.snapshot.primary_result_influence is False


@pytest.mark.asyncio
async def test_bound_semantic_review_can_only_recommend_one_sample_step(tmp_path):
    primary, shadow = await services_with_pairs(tmp_path)
    service = evaluation_service(tmp_path, primary, shadow)
    unreviewed = await service.evaluate(INCIDENT_ID)

    reviewed = await service.evaluate(
        INCIDENT_ID,
        semantic_review=review(unreviewed.snapshot),
    )
    snapshots = await service.store.list_recent_by_incident(INCIDENT_ID)

    assert reviewed.created is True
    assert reviewed.snapshot.evaluation_id != unreviewed.snapshot.evaluation_id
    assert reviewed.snapshot.source_window_digest == (
        unreviewed.snapshot.source_window_digest
    )
    assert reviewed.snapshot.promotion_status == (
        InvestigationEngineShadowPromotionStatus.PROMOTION_READY
    )
    assert reviewed.snapshot.recommended_sample_rate == 0.02
    assert reviewed.snapshot.applies_configuration is False
    assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_semantic_mismatch_and_stale_review_fail_closed(tmp_path):
    primary, shadow = await services_with_pairs(tmp_path)
    service = evaluation_service(tmp_path, primary, shadow)
    unreviewed = await service.evaluate(INCIDENT_ID)

    mismatch = await service.evaluate(
        INCIDENT_ID,
        semantic_review=review(unreviewed.snapshot, equivalent=9),
    )
    assert mismatch.snapshot.promotion_status == (
        InvestigationEngineShadowPromotionStatus.NO_GO
    )
    assert mismatch.snapshot.promotion_reasons == (
        InvestigationEngineShadowPromotionReason.SEMANTIC_MISMATCH,
    )

    stale = review(unreviewed.snapshot).model_copy(
        update={"source_window_digest": "d" * 64}
    )
    with pytest.raises(InvestigationEngineShadowEvaluationError, match="stale"):
        await service.evaluate(INCIDENT_ID, semantic_review=stale)


@pytest.mark.asyncio
async def test_cross_instance_claims_one_immutable_snapshot(tmp_path):
    primary, shadow = await services_with_pairs(tmp_path)
    db_path = tmp_path / "shared_evaluations.db"
    services = [
        InvestigationEngineShadowEvaluationService(
            store=InvestigationEngineShadowEvaluationStore(db_path),
            primary_service=primary,
            shadow_service=shadow,
            decision=decision(),
            policy=policy(),
            utc_clock=lambda: NOW + timedelta(hours=1),
        )
        for _ in range(6)
    ]

    results = await asyncio.gather(
        *(service.evaluate(INCIDENT_ID) for service in services)
    )

    assert sum(result.created for result in results) == 1
    assert len({result.snapshot.evaluation_id for result in results}) == 1


@pytest.mark.asyncio
async def test_snapshot_storage_is_bounded_sanitized_and_connections_close(
    tmp_path,
):
    primary, shadow = await services_with_pairs(tmp_path)
    service = evaluation_service(tmp_path, primary, shadow)
    result = await service.evaluate(INCIDENT_ID)

    with closing(sqlite3.connect(service.store.db_path)) as connection:
        stored = connection.execute(
            "SELECT snapshot_data FROM investigation_shadow_evaluations"
        ).fetchone()[0]
    for forbidden in (
        "sensitive alert",
        "payment-api",
        "run_key",
        "request_digest",
        "claimant",
        "rationale_summary",
    ):
        assert forbidden not in stored
    assert result.snapshot.evaluated_distinct_primary_inputs == 10
    assert result.snapshot.evaluated_distinct_shadow_inputs == 10

    renamed = tmp_path / "evaluations-renamed.db"
    service.store.db_path.replace(renamed)
    assert renamed.is_file()


@pytest.mark.asyncio
async def test_session_store_failure_is_sanitized_and_writes_no_snapshot(
    tmp_path,
    monkeypatch,
):
    primary, shadow = await services_with_pairs(tmp_path, count=1)
    service = evaluation_service(tmp_path, primary, shadow)
    secret = "sqlite:///private/session.db?credential=must-not-leak"

    async def explode(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(shadow, "list_recent_by_incident", explode)

    with pytest.raises(
        InvestigationEngineShadowEvaluationError,
        match="Shadow evaluation is unavailable",
    ) as captured:
        await service.evaluate(INCIDENT_ID)

    assert secret not in str(captured.value)
    assert await service.store.list_recent_by_incident(INCIDENT_ID) == []
