from __future__ import annotations

from importlib.metadata import version
from types import SimpleNamespace

import pytest

from lark_channel import (
    CardActionEvent,
    CardActionPayload,
    Conversation,
    EventOperator,
    Events,
    Identity,
    InboundMessage,
    ReplyRef,
)

from services.agent_runtime.app.conversation.chatops import (
    ChatOpsConversationGateway,
)
from services.agent_runtime.app.conversation.feishu import (
    FeishuActorAttestationRegistry,
    FeishuChatOpsActorVerifier,
    FeishuChatOpsAdapter,
    FeishuLongConnectionTrustBoundary,
    FeishuPayloadError,
)
from services.agent_runtime.app.conversation.feishu_channel_transport import (
    FeishuChannelTransportPayloadError,
    FeishuOfficialChannelTransport,
)
from services.agent_runtime.app.conversation.models import (
    ConversationIncidentContext,
)
from services.agent_runtime.app.conversation.orchestrator import (
    ConversationOrchestrator,
)
from services.agent_runtime.app.conversation.provider import (
    DictConversationIncidentContextProvider,
)


INCIDENT_ID = (
    "7f0d8f0a-9e8a-4b78-"
    "9b62-486f7039e142"
)


class FakeChannel:
    def __init__(self) -> None:
        self.handlers = {}
        self.sent = []

    def on(
        self,
        event,
        handler,
    ) -> None:
        self.handlers[
            event
        ] = handler

    async def send(
        self,
        to,
        message,
        opts=None,
    ):
        self.sent.append(
            {
                "to": to,
                "message": message,
                "opts": opts,
            }
        )

        return SimpleNamespace(
            success=True,
            message_id="om-sent-1",
        )


def build_components():
    trust = (
        FeishuLongConnectionTrustBoundary()
    )
    attestations = (
        FeishuActorAttestationRegistry()
    )
    adapter = FeishuChatOpsAdapter(
        trust_boundary=trust,
        attestations=attestations,
    )

    provider = (
        DictConversationIncidentContextProvider(
            {
                INCIDENT_ID: (
                    ConversationIncidentContext(
                        incident_id=(
                            INCIDENT_ID
                        ),
                        status=(
                            "waiting_approval"
                        ),
                        title=(
                            "payment-api PodOOMKilled"
                        ),
                        root_cause=(
                            "Memory limit regression"
                        ),
                        root_cause_confidence=(
                            0.94
                        ),
                    )
                )
            }
        )
    )

    orchestrator = (
        ConversationOrchestrator(
            provider=provider
        )
    )

    gateway = (
        ChatOpsConversationGateway(
            orchestrator=orchestrator
        )
    )

    channel = FakeChannel()

    transport = (
        FeishuOfficialChannelTransport(
            channel=channel,
            adapter=adapter,
            gateway=gateway,
        )
    )

    verifier = (
        FeishuChatOpsActorVerifier(
            attestations
        )
    )

    return (
        transport,
        channel,
        verifier,
    )


def sdk_message(
    *,
    text="根因是什么？",
    chat_type="group",
    thread_id="omt-thread-1",
    reply_to=None,
    sender_type="user",
    is_bot=False,
    raw_content_type="text",
):
    reply = (
        ReplyRef(
            message_id=reply_to
        )
        if reply_to
        else None
    )

    return InboundMessage(
        id="om-message-1",
        create_time=1786470000,
        conversation=(
            Conversation(
                chat_id=(
                    "oc-sre-group"
                ),
                chat_type=chat_type,
                thread_id=thread_id,
            )
        ),
        sender=(
            Identity(
                open_id="ou-operator-1",
                is_bot=is_bot,
                sender_type=(
                    sender_type
                ),
            )
        ),
        reply=reply,
        content_text=text,
        raw_content_type=(
            raw_content_type
        ),
        body_text=text,
    )


def sdk_card_action(
    *,
    action_name="show_status",
    incident_id=INCIDENT_ID,
):
    value = {
        "ai_sre_action": action_name,
    }

    if incident_id is not None:
        value[
            "incident_id"
        ] = incident_id

    return CardActionEvent(
        message_id=(
            "om-card-1"
        ),
        chat_id="oc-sre-group",
        operator=(
            EventOperator(
                open_id=(
                    "ou-operator-1"
                )
            )
        ),
        action=(
            CardActionPayload(
                value=value
            )
        ),
    )


def test_installed_official_channel_sdk_contract():
    assert (
        version(
            "lark-channel-sdk"
        )
        == "1.2.0"
    )

    assert Events.MESSAGE == "message"
    assert (
        Events.CARD_ACTION
        == "cardAction"
    )


def test_register_only_installs_handlers_and_is_idempotent():
    transport, channel, _ = (
        build_components()
    )

    returned = transport.register()

    assert returned is transport
    assert transport.registered is True
    assert set(
        channel.handlers
    ) == {
        Events.MESSAGE,
        Events.CARD_ACTION,
    }

    message_handler = (
        channel.handlers[
            Events.MESSAGE
        ]
    )
    card_handler = (
        channel.handlers[
            Events.CARD_ACTION
        ]
    )

    transport.register()

    assert (
        channel.handlers[
            Events.MESSAGE
        ]
        is message_handler
    )
    assert (
        channel.handlers[
            Events.CARD_ACTION
        ]
        is card_handler
    )


@pytest.mark.asyncio
async def test_sdk_message_crosses_existing_trust_boundary_and_attests_actor():
    transport, _, verifier = (
        build_components()
    )

    inbound = (
        transport.normalize_message(
            sdk_message()
        )
    )

    assert (
        inbound.conversation.channel
        == "feishu"
    )
    assert (
        inbound.conversation.tenant_id
        is None
    )
    assert (
        inbound.conversation.conversation_id
        == "oc-sre-group"
    )
    assert (
        inbound.conversation.thread_id
        == "omt-thread-1"
    )
    assert (
        inbound.external_actor_id
        == "ou-operator-1"
    )
    assert inbound.text == "根因是什么？"

    actor = await verifier.verify(
        inbound
    )

    assert actor.channel == "feishu"
    assert (
        actor.external_actor_id
        == "ou-operator-1"
    )
    assert (
        actor.verification_method
        == "feishu_official_sdk_long_connection"
    )


def test_sdk_topic_maps_to_existing_group_thread_contract():
    transport, _, _ = (
        build_components()
    )

    inbound = (
        transport.normalize_message(
            sdk_message(
                chat_type="topic",
                thread_id=(
                    "omt-topic-1"
                ),
            )
        )
    )

    assert (
        inbound.conversation.thread_id
        == "omt-topic-1"
    )


@pytest.mark.parametrize(
    "event",
    [
        sdk_message(
            sender_type="bot",
            is_bot=True,
        ),
        sdk_message(
            sender_type="system",
        ),
        sdk_message(
            raw_content_type="image",
        ),
        sdk_message(
            text="   ",
        ),
    ],
)
def test_sdk_message_fails_closed_for_nonhuman_or_unsupported_input(
    event,
):
    transport, _, _ = (
        build_components()
    )

    with pytest.raises(
        FeishuChannelTransportPayloadError
    ):
        transport.normalize_message(
            event
        )


def test_sdk_card_action_reuses_existing_core_allowlist():
    transport, _, _ = (
        build_components()
    )

    inbound = (
        transport.normalize_card_action(
            sdk_card_action()
        )
    )

    assert inbound.text == (
        "现在状态怎么样？"
    )
    assert inbound.incident_id == (
        INCIDENT_ID
    )

    with pytest.raises(
        FeishuPayloadError
    ):
        transport.normalize_card_action(
            sdk_card_action(
                action_name=(
                    "arbitrary.shell"
                )
            )
        )


def test_sdk_write_card_requires_incident_through_existing_core():
    transport, _, _ = (
        build_components()
    )

    with pytest.raises(
        FeishuPayloadError
    ):
        transport.normalize_card_action(
            sdk_card_action(
                action_name=(
                    "approval.approve"
                ),
                incident_id=None,
            )
        )


@pytest.mark.asyncio
async def test_read_only_message_dispatch_renders_and_sends_card_v2():
    transport, channel, _ = (
        build_components()
    )

    message = sdk_message(
        text=(
            "incident_id: "
            + INCIDENT_ID
            + " 根因是什么？"
        ),
    )

    await transport.handle_message(
        message
    )

    assert len(
        channel.sent
    ) == 1

    sent = channel.sent[
        0
    ]

    assert sent["to"] == (
        "oc-sre-group"
    )
    assert (
        sent["message"][
            "card"
        ][
            "schema"
        ]
        == "2.0"
    )
    assert (
        sent["opts"][
            "reply_to"
        ]
        == "om-message-1"
    )
    assert (
        sent["opts"][
            "reply_in_thread"
        ]
        is True
    )
    assert (
        sent["opts"][
            "receive_id_type"
        ]
        == "chat_id"
    )


@pytest.mark.asyncio
async def test_read_only_card_action_dispatch_uses_same_gateway():
    transport, channel, _ = (
        build_components()
    )

    await (
        transport.handle_card_action(
            sdk_card_action(
                action_name=(
                    "show_status"
                )
            )
        )
    )

    assert len(
        channel.sent
    ) == 1

    assert (
        channel.sent[
            0
        ][
            "message"
        ][
            "card"
        ][
            "schema"
        ]
        == "2.0"
    )


def test_transport_source_has_no_live_credentials_or_auto_connect():
    from pathlib import Path

    import services.agent_runtime.app.conversation.feishu_channel_transport as module

    source = Path(
        module.__file__
    ).read_text(
        encoding="utf-8"
    )

    required = [
        "from lark_channel import",
        "Events.MESSAGE",
        "Events.CARD_ACTION",
        "FeishuChatOpsAdapter",
        "ChatOpsConversationGateway",
        "ChatOpsAuthenticatedWriteBridge",
    ]

    assert [
        item
        for item in required
        if item not in source
    ] == []

    forbidden = [
        "lark_oapi",
        "os.environ",
        "LARK_APP_ID",
        "LARK_APP_SECRET",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        ".connect(",
        "FeishuChannel(",
        "ApprovalService",
        "ActionRuntime",
        "KubernetesProductionExecutor",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []
