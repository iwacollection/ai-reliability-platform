from __future__ import annotations

from datetime import UTC, datetime

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

from services.agent_runtime.app.conversation import (
    BaseChatOpsChannelAdapter,
    ChatOpsConversationGateway,
    ChatOpsConversationRef,
    ChatOpsInboundMessage,
    ChatOpsOutboundMessage,
    ConversationIncidentContext,
    ConversationIntent,
    ConversationOrchestrator,
    ConversationReplyMode,
    ConversationTurnRequest,
    DictConversationIncidentContextProvider,
    SQLiteConversationSessionStore,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


NOW = datetime(
    2026,
    8,
    11,
    11,
    30,
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


def context(
    incident_id: str,
) -> ConversationIncidentContext:
    return ConversationIncidentContext(
        incident_id=incident_id,
        status="investigating",
        title="checkout-api / PodOOMKilled",
    )


def ref(
    *,
    channel="feishu",
    tenant="tenant-a",
    conversation="group-100",
    thread="thread-200",
):
    return ChatOpsConversationRef(
        channel=channel,
        tenant_id=tenant,
        conversation_id=conversation,
        thread_id=thread,
    )


@pytest.mark.asyncio
async def test_sqlite_conversation_binding_survives_store_restart(
    tmp_path,
):
    db = (
        tmp_path
        / "conversation_sessions.db"
    )

    first = SQLiteConversationSessionStore(
        db_path=db
    )

    await first.update(
        conversation_id="chatops:abc123",
        incident_id="INC-1001",
        intent=ConversationIntent.STATUS,
    )

    second = SQLiteConversationSessionStore(
        db_path=db
    )

    session = await second.get(
        "chatops:abc123"
    )

    assert session is not None
    assert session.incident_id == "INC-1001"
    assert session.turn_count == 1


@pytest.mark.asyncio
async def test_orchestrator_reuses_durable_incident_binding_after_restart(
    tmp_path,
):
    db = (
        tmp_path
        / "conversation_sessions.db"
    )

    provider = (
        DictConversationIncidentContextProvider(
            {
                "INC-1001": context(
                    "INC-1001"
                ),
            }
        )
    )

    first = ConversationOrchestrator(
        provider=provider,
        sessions=(
            SQLiteConversationSessionStore(
                db_path=db
            )
        ),
    )

    await first.handle(
        ConversationTurnRequest(
            conversation_id="chatops:thread-a",
            incident_id="INC-1001",
            text="现在状态怎么样？",
        )
    )

    second = ConversationOrchestrator(
        provider=provider,
        sessions=(
            SQLiteConversationSessionStore(
                db_path=db
            )
        ),
    )

    reply = await second.handle(
        ConversationTurnRequest(
            conversation_id="chatops:thread-a",
            text="根因是什么？",
        )
    )

    assert reply.incident_id == "INC-1001"
    assert reply.mode == (
        ConversationReplyMode.READ_ONLY
    )


def test_chatops_binding_key_is_stable_and_scoped():
    first = ref()
    same = ref()

    assert (
        first.binding_key()
        == same.binding_key()
    )

    assert (
        first.binding_key()
        != ref(
            tenant="tenant-b"
        ).binding_key()
    )

    assert (
        first.binding_key()
        != ref(
            thread="thread-201"
        ).binding_key()
    )

    assert (
        first.binding_key()
        != ref(
            channel="dingtalk"
        ).binding_key()
    )

    assert first.binding_key().startswith(
        "chatops:"
    )

    assert "group-100" not in (
        first.binding_key()
    )


@pytest.mark.asyncio
async def test_chatops_gateway_binds_incident_then_reuses_thread_context(
    tmp_path,
):
    db = (
        tmp_path
        / "conversation_sessions.db"
    )

    provider = (
        DictConversationIncidentContextProvider(
            {
                "INC-1001": context(
                    "INC-1001"
                ),
            }
        )
    )

    first_gateway = ChatOpsConversationGateway(
        orchestrator=ConversationOrchestrator(
            provider=provider,
            sessions=(
                SQLiteConversationSessionStore(
                    db_path=db
                )
            ),
        )
    )

    thread = ref()

    first = await first_gateway.handle(
        ChatOpsInboundMessage(
            conversation=thread,
            message_id="msg-1",
            external_actor_id="user-1",
            incident_id="INC-1001",
            text="现在状态怎么样？",
        )
    )

    assert isinstance(
        first,
        ChatOpsOutboundMessage,
    )

    assert first.reply.incident_id == (
        "INC-1001"
    )

    # Recreate both store and Orchestrator, simulating a Runtime process restart.
    second_gateway = ChatOpsConversationGateway(
        orchestrator=ConversationOrchestrator(
            provider=provider,
            sessions=(
                SQLiteConversationSessionStore(
                    db_path=db
                )
            ),
        )
    )

    second = await second_gateway.handle(
        ChatOpsInboundMessage(
            conversation=thread,
            message_id="msg-2",
            external_actor_id="user-1",
            text="根因是什么？",
        )
    )

    assert second.reply.incident_id == (
        "INC-1001"
    )

    assert second.reply_to_message_id == (
        "msg-2"
    )


@pytest.mark.asyncio
async def test_chatops_write_intent_remains_nonexecuting():
    provider = (
        DictConversationIncidentContextProvider(
            {
                "INC-1001": context(
                    "INC-1001"
                ),
            }
        )
    )

    gateway = ChatOpsConversationGateway(
        orchestrator=ConversationOrchestrator(
            provider=provider
        )
    )

    output = await gateway.handle(
        ChatOpsInboundMessage(
            conversation=ref(),
            message_id="msg-write",
            external_actor_id="user-untrusted",
            incident_id="INC-1001",
            text="批准执行",
        )
    )

    assert output.reply.mode == (
        ConversationReplyMode
        .WRITE_ACTION_REQUIRED
    )

    assert output.reply.write_operation == (
        "approval.approve"
    )


@pytest.mark.asyncio
async def test_runtime_chatops_binding_survives_runtime_restart(
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

    incident = await (
        runtime_one.incident_store.save(
            IncidentState(
                reason="ChatOps durable binding test"
            )
        )
    )

    thread = ref(
        channel="slack",
        tenant="workspace-a",
        conversation="channel-ops",
        thread="incident-thread",
    )

    first = await runtime_one.chatops.handle(
        ChatOpsInboundMessage(
            conversation=thread,
            message_id="msg-runtime-1",
            incident_id=str(
                incident.id
            ),
            text="现在状态怎么样？",
        )
    )

    assert first.reply.incident_id == str(
        incident.id
    )

    runtime_two = AgentRuntime()

    second = await runtime_two.chatops.handle(
        ChatOpsInboundMessage(
            conversation=thread,
            message_id="msg-runtime-2",
            text="现在状态怎么样？",
        )
    )

    assert second.reply.incident_id == str(
        incident.id
    )

    assert isinstance(
        runtime_two.conversation_sessions,
        SQLiteConversationSessionStore,
    )


class FakeAdapter(
    BaseChatOpsChannelAdapter
):
    def normalize_inbound(
        self,
        payload,
    ):
        return ChatOpsInboundMessage(
            conversation=ref(),
            message_id=payload[
                "message_id"
            ],
            text=payload[
                "text"
            ],
        )

    def render_outbound(
        self,
        message,
    ):
        return {
            "reply_to": (
                message.reply_to_message_id
            ),
            "sections": [
                section.model_dump(
                    mode="json"
                )
                for section
                in message.reply.sections
            ],
        }


def test_channel_adapter_contract_is_transform_only():
    adapter = FakeAdapter()

    inbound = adapter.normalize_inbound(
        {
            "message_id": "msg-1",
            "text": "help",
        }
    )

    assert isinstance(
        inbound,
        ChatOpsInboundMessage,
    )


def test_chatops_contract_has_no_network_or_runtime_write_authority():
    from pathlib import Path

    import services.agent_runtime.app.conversation.chatops as module

    source = Path(
        module.__file__
    ).read_text(
        encoding="utf-8"
    )

    forbidden = [
        "httpx",
        "requests.",
        "aiohttp",
        "ActionRuntime",
        "ApprovalService",
        "KubernetesProductionExecutor",
        ".approve(",
        ".reject(",
        ".resume(",
        ".execute(",
        "def send(",
        "async def send(",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []
