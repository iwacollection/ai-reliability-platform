from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.conversation.models import (
    ConversationReplyMode,
    ConversationTurnRequest,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.persistence_models import (
    build_incident_analysis_record,
)
from services.agent_runtime.app.investigation.store import (
    IncidentAnalysisStore,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


NOW = datetime(
    2026,
    8,
    11,
    11,
    0,
    tzinfo=UTC,
)


def event() -> StandardEvent:
    return StandardEvent(
        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message="pod was OOMKilled",
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name="checkout-api-abc123",
                namespace="checkout",
                cluster="prod-us-03",
            )
        ],
    )


def investigation_state() -> InvestigationState:
    evidence = EvidenceItem(
        evidence_id="ev-pod",
        probe=(
            InvestigationProbe
            .KUBERNETES_POD_STATE
        ),
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        cluster="prod-us-03",
        cluster_verified=True,
        facts={
            "oom_killed": True,
            "restart_count": 7,
        },
    )

    hypothesis = IncidentHypothesis(
        hypothesis_id="h-memory",
        cause=(
            "Container exceeded its memory limit"
        ),
        confidence=0.91,
        supporting_evidence_ids=[
            evidence.evidence_id
        ],
        missing_evidence=[],
    )

    return InvestigationState(
        investigation_id="inv-1001",
        status=InvestigationStatus.CONCLUDED,
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="pod was OOMKilled",
            event_occurred_at=NOW,
            resource="checkout-api-abc123",
            namespace="checkout",
            cluster="prod-us-03",
        ),
        started_at=NOW,
        updated_at=NOW,
        iteration_count=2,
        tool_call_count=1,
        hypotheses=[
            hypothesis
        ],
        evidence=[
            evidence
        ],
        attempted_probes=[
            InvestigationProbe
            .KUBERNETES_POD_STATE
        ],
        decision_summaries=[
            "Trusted pod evidence supports memory exhaustion"
        ],
        stop_reason=(
            InvestigationStopReason
            .SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "Container exceeded its memory limit"
            ),
            confidence=0.91,
            evidence_ids=[
                evidence.evidence_id
            ],
        ),
    )


@pytest.mark.asyncio
async def test_incident_analysis_store_survives_restart_and_merges_sources(
    tmp_path,
):
    db = (
        tmp_path
        / "incident_analysis.db"
    )

    incident_id = uuid4()

    first = build_incident_analysis_record(
        incident_id=incident_id,
        event=event(),
        request_id="req-1001",
        primary_rca={
            "root_cause": (
                "Deployment reduced the memory limit"
            ),
            "confidence": 0.96,
            "evidence": [
                "OOMKilled",
                "limit changed 1Gi -> 512Mi",
            ],
        },
        now=NOW,
    )

    store_one = IncidentAnalysisStore(
        db_path=db
    )

    await store_one.upsert(
        first
    )

    # A new store instance simulates a Runtime restart.
    store_two = IncidentAnalysisStore(
        db_path=db
    )

    restarted = await store_two.get(
        incident_id
    )

    assert restarted is not None
    assert restarted.primary_rca is not None
    assert (
        restarted.primary_rca.root_cause
        == "Deployment reduced the memory limit"
    )
    assert restarted.investigation is None

    enriched = build_incident_analysis_record(
        incident_id=incident_id,
        event=event(),
        request_id="req-1001",
        investigation_snapshot=(
            investigation_state()
        ),
        existing=restarted,
        now=NOW,
    )

    await store_two.upsert(
        enriched
    )

    store_three = IncidentAnalysisStore(
        db_path=db
    )

    final = await store_three.get(
        incident_id
    )

    assert final is not None
    assert final.primary_rca is not None
    assert final.investigation is not None
    assert (
        final.investigation.conclusion
        is not None
    )
    assert (
        final.investigation.evidence[
            0
        ].cluster_verified
        is True
    )


@pytest.mark.asyncio
async def test_runtime_persists_analysis_and_conversation_reloads_after_restart(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    for name in (
        "PROMETHEUS_URL",
        "KUBERNETES_API_URL",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    monkeypatch.setenv(
        "PROMETHEUS_ALLOW_MOCK_FALLBACK",
        "true",
    )

    monkeypatch.setenv(
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "true",
    )

    runtime_one = AgentRuntime()

    incident = IncidentState(
        status=IncidentStatus.CONFIRMED,
        reason="RCA complete; awaiting approval",
    )

    incident = await (
        runtime_one.incident_store.save(
            incident
        )
    )

    context = AgentContext(
        request_id="req-chatops-1",
        event=event(),
        incident=incident,
        memory=runtime_one.memory,
        tools=runtime_one.tools,
        skills=runtime_one.skills,
        approval=runtime_one.approval,
        metadata={
            "investigation_shadow": (
                investigation_state()
                .model_dump(
                    mode="json"
                )
            )
        },
        variables={
            "rca": {
                "root_cause": (
                    "Deployment reduced the memory limit"
                ),
                "confidence": 0.96,
                "evidence": [
                    "OOMKilled",
                    "limit changed 1Gi -> 512Mi",
                ],
            }
        },
    )

    await runtime_one._persist_incident_analysis(
        context
    )

    plan = ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="checkout-api",
        namespace="checkout",
        cluster="prod-us-03",
    )

    approval = await (
        runtime_one.approval
        .create_approval(
            action=plan,
            reason="medium risk",
            incident_id=incident.id,
        )
    )

    approved = await (
        runtime_one.approval.approve(
            approval.id,
            operator_id="operator-1",
            idempotency_key="approval-key-1",
            reason="approved for test",
        )
    )

    claim = await (
        runtime_one.action_execution_service
        .claim(
            approval_id=approved.id,
            operator_id="operator-1",
            idempotency_key="execution-key-1",
            action=approved.action,
            incident_id=incident.id,
        )
    )

    assert claim.created is True

    verification = await (
        runtime_one.verification
        .create_verification(
            incident_id=incident.id,
            action=plan.type.value,
            target=plan.target,
            metadata={
                "cluster": plan.cluster,
                "namespace": plan.namespace,
            },
        )
    )

    assert (
        verification.status.value
        == "pending"
    )

    # Recreate the Runtime against the same SQLite files.
    runtime_two = AgentRuntime()

    projected = await (
        runtime_two
        .conversation_context_provider
        .get(
            str(
                incident.id
            )
        )
    )

    assert projected is not None
    assert projected.status == "confirmed"
    assert (
        projected.root_cause
        == "Deployment reduced the memory limit"
    )
    assert (
        projected.root_cause_confidence
        == pytest.approx(
            0.96
        )
    )
    assert projected.approval_status == "approved"
    assert (
        projected.action_execution_status
        == "running"
    )
    assert (
        projected.verification_status
        == "pending"
    )

    assert any(
        item.cluster_verified
        for item in projected.evidence
    )

    assert (
        projected.hypotheses[
            0
        ].confidence
        == pytest.approx(
            0.91
        )
    )

    reply = await runtime_two.conversation.handle(
        ConversationTurnRequest(
            conversation_id="chat-thread-1",
            incident_id=str(
                incident.id
            ),
            text="现在状态怎么样？",
        )
    )

    assert reply.mode == (
        ConversationReplyMode.READ_ONLY
    )

    reply_text = str(
        reply.model_dump()
    )

    assert "confirmed" in reply_text
    assert "approved" in reply_text
    assert "running" in reply_text
    assert "pending" in reply_text

    rca_reply = await runtime_two.conversation.handle(
        ConversationTurnRequest(
            conversation_id="chat-thread-1",
            text="根因是什么？",
        )
    )

    rca_text = str(
        rca_reply.model_dump()
    )

    assert (
        "Deployment reduced the memory limit"
        in rca_text
    )


@pytest.mark.asyncio
async def test_analysis_persistence_failure_cannot_fail_primary_workflow(
    monkeypatch,
    tmp_path,
):
    runtime = object.__new__(
        AgentRuntime
    )

    runtime.incident_analysis_store = (
        IncidentAnalysisStore(
            db_path=(
                tmp_path
                / "broken_analysis.db"
            )
        )
    )

    async def broken_get(
        incident_id,
    ):
        raise RuntimeError(
            "secret backend details"
        )

    monkeypatch.setattr(
        runtime.incident_analysis_store,
        "get",
        broken_get,
    )

    context = AgentContext(
        event=event(),
        incident=IncidentState(),
        metadata={},
    )

    await runtime._persist_incident_analysis(
        context
    )

    snapshot = context.metadata[
        "incident_analysis_persistence"
    ]

    assert snapshot[
        "status"
    ] == "failed"

    assert snapshot[
        "failure_code"
    ] == "RuntimeError"

    assert "secret" not in str(
        snapshot
    )


def test_conversation_runtime_provider_has_no_write_authority():
    from pathlib import Path
    import services.agent_runtime.app.conversation.runtime_provider as module

    source = Path(
        module.__file__
    ).read_text(
        encoding="utf-8"
    )

    forbidden = [
        ".approve(",
        ".reject(",
        ".resume(",
        ".execute(",
        ".update(",
        ".save(",
        "KubernetesProductionExecutor",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []
