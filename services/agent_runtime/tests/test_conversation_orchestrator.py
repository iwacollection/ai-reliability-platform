from __future__ import annotations

import pytest

from services.agent_runtime.app.conversation import (
    ConversationEvidenceView,
    ConversationHypothesisView,
    ConversationIncidentContext,
    ConversationIntent,
    ConversationOrchestrator,
    ConversationReplyMode,
    ConversationTurnRequest,
    DeterministicConversationIntentClassifier,
    DictConversationIncidentContextProvider,
)


def context(incident_id="INC-1001"):
    return ConversationIncidentContext(
        incident_id=incident_id,
        status="waiting_approval",
        title="payment-api PodOOMKilled",
        root_cause=(
            "Memory limit regression caused OOMKilled"
        ),
        root_cause_confidence=0.94,
        evidence=(
            ConversationEvidenceView(
                evidence_id="ev-1",
                source="kubernetes",
                summary="Container terminated with OOMKilled",
                trusted=True,
                cluster_verified=True,
            ),
            ConversationEvidenceView(
                evidence_id="ev-2",
                source="prometheus",
                summary="Memory utilization reached 99%",
                trusted=True,
                cluster_verified=True,
            ),
        ),
        hypotheses=(
            ConversationHypothesisView(
                cause="Memory limit regression",
                confidence=0.94,
            ),
            ConversationHypothesisView(
                cause="Memory leak",
                confidence=0.21,
            ),
        ),
        recommended_action=(
            "Restore memory limit from 512Mi to 1Gi"
        ),
        action_risk="medium",
        approval_status="pending",
    )


def orchestrator():
    provider = DictConversationIncidentContextProvider(
        {
            "INC-1001": context(),
            "INC-2002": context("INC-2002"),
        }
    )
    return ConversationOrchestrator(
        provider=provider
    )


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("现在状态怎么样？", ConversationIntent.STATUS),
        ("根因是什么？", ConversationIntent.RCA),
        ("为什么这么判断？", ConversationIntent.EVIDENCE),
        ("有哪些证据？", ConversationIntent.EVIDENCE),
        ("下一步怎么办？", ConversationIntent.NEXT_STEP),
        ("验证结果怎么样？", ConversationIntent.VERIFICATION),
        ("帮我修一下", ConversationIntent.REMEDIATE),
        ("批准执行", ConversationIntent.APPROVE),
        ("拒绝", ConversationIntent.REJECT),
        ("help", ConversationIntent.HELP),
    ],
)
def test_deterministic_intent_classifier(
    text,
    intent,
):
    classifier = (
        DeterministicConversationIntentClassifier()
    )
    assert classifier.classify(text) == intent


@pytest.mark.asyncio
async def test_first_turn_requires_incident_binding():
    value = orchestrator()
    reply = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-1",
            text="根因是什么？",
        )
    )
    assert reply.mode == (
        ConversationReplyMode.NEEDS_INCIDENT
    )
    assert reply.incident_id is None


@pytest.mark.asyncio
async def test_explicit_incident_binding_is_reused_by_follow_up_turns():
    value = orchestrator()

    first = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-1",
            incident_id="INC-1001",
            text="现在状态怎么样？",
        )
    )

    second = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-1",
            text="根因是什么？",
        )
    )

    assert first.incident_id == "INC-1001"
    assert second.incident_id == "INC-1001"

    session = await value.sessions.get("chat-1")
    assert session is not None
    assert session.incident_id == "INC-1001"
    assert session.turn_count == 2


@pytest.mark.asyncio
async def test_explicit_new_incident_rebinds_conversation():
    value = orchestrator()

    await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-1",
            incident_id="INC-1001",
            text="状态",
        )
    )

    reply = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-1",
            incident_id="INC-2002",
            text="状态",
        )
    )
    assert reply.incident_id == "INC-2002"


@pytest.mark.asyncio
async def test_rca_reply_uses_existing_context_without_llm():
    value = orchestrator()

    reply = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-2",
            incident_id="INC-1001",
            text="根因是什么？",
        )
    )

    assert reply.intent == ConversationIntent.RCA
    assert reply.mode == ConversationReplyMode.READ_ONLY

    text = str(reply.model_dump())
    assert (
        "Memory limit regression caused OOMKilled"
        in text
    )
    assert "94%" in text


@pytest.mark.asyncio
async def test_evidence_reply_preserves_sources():
    value = orchestrator()

    reply = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-3",
            incident_id="INC-1001",
            text="证据是什么？",
        )
    )

    text = str(reply.model_dump())
    assert "OOMKilled" in text
    assert "99%" in text
    assert "kubernetes" in text
    assert "prometheus" in text


@pytest.mark.asyncio
async def test_write_intents_return_explicit_nonexecuting_boundary():
    value = orchestrator()

    for text, expected_operation in (
        ("批准执行", "approval.approve"),
        ("拒绝", "approval.reject"),
        ("帮我修一下", "action.resume"),
    ):
        reply = await value.handle(
            ConversationTurnRequest(
                conversation_id=(
                    "chat-write-"
                    + expected_operation
                ),
                incident_id="INC-1001",
                text=text,
            )
        )

        assert reply.mode == (
            ConversationReplyMode
            .WRITE_ACTION_REQUIRED
        )
        assert (
            reply.write_operation
            == expected_operation
        )


@pytest.mark.asyncio
async def test_unknown_incident_is_explicit():
    value = orchestrator()

    reply = await value.handle(
        ConversationTurnRequest(
            conversation_id="chat-4",
            incident_id="INC-404",
            text="状态",
        )
    )

    assert reply.mode == (
        ConversationReplyMode
        .INCIDENT_NOT_FOUND
    )


def test_conversation_core_has_no_runtime_write_authority():
    from pathlib import Path
    import services.agent_runtime.app.conversation.orchestrator as module

    source = Path(module.__file__).read_text(
        encoding="utf-8"
    )

    forbidden = [
        "ActionRuntime",
        "ApprovalService",
        "KubernetesProductionExecutor",
        ".approve(",
        ".reject(",
        ".resume(",
        ".execute(",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []
