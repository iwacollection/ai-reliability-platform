from __future__ import annotations

import copy

import pytest

from services.agent_runtime.app.conversation.chatops import (
    ChatOpsInboundMessage,
    ChatOpsOutboundMessage,
)
from services.agent_runtime.app.conversation.feishu import (
    FEISHU_CARD_ACTION_EVENT,
    FEISHU_MESSAGE_EVENT,
    FeishuActorAttestationRegistry,
    FeishuChatOpsActorVerifier,
    FeishuChatOpsAdapter,
    FeishuLongConnectionTrustBoundary,
    FeishuPayloadError,
    FeishuUntrustedTransportError,
)
from services.agent_runtime.app.conversation.models import (
    ConversationIntent,
    ConversationReplyMode,
    ConversationReplyPlan,
    ConversationReplySection,
)
from services.agent_runtime.app.conversation.write_bridge import (
    ChatOpsWriteOutcome,
    ChatOpsWriteStatus,
)


INCIDENT_ID = (
    "7f0d8f0a-9e8a-4b78-9b62-"
    "486f7039e142"
)


def components():
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
    verifier = FeishuChatOpsActorVerifier(
        attestations
    )
    return trust, adapter, verifier


def message_event(
    *,
    sender_type="user",
    message_type="text",
    text="@_user_1 根因是什么？",
    root_id="om_root",
    parent_id="",
    chat_type="group",
):
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt-msg-1",
            "event_type": (
                FEISHU_MESSAGE_EVENT
            ),
            "tenant_key": "tenant-a",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": "ou_user_1",
                },
                "sender_type": sender_type,
                "tenant_key": "tenant-a",
            },
            "message": {
                "message_id": "om_message_1",
                "root_id": root_id,
                "parent_id": parent_id,
                "chat_id": "oc_sre_group",
                "chat_type": chat_type,
                "message_type": message_type,
                "content": (
                    '{"text": '
                    + repr(text).replace(
                        "'",
                        '"',
                    )
                    + "}"
                ),
                "mentions": [
                    {
                        "key": "@_user_1",
                    }
                ],
            },
        },
    }


def card_action(
    *,
    action_name="approval.approve",
    incident_id=INCIDENT_ID,
):
    value = {
        "ai_sre_action": action_name,
    }

    if incident_id is not None:
        value[
            "incident_id"
        ] = incident_id

    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt-card-1",
            "event_type": (
                FEISHU_CARD_ACTION_EVENT
            ),
            "tenant_key": "tenant-a",
        },
        "event": {
            "operator": {
                "open_id": "ou_user_1",
                "tenant_key": "tenant-a",
            },
            "context": {
                "open_chat_id": "oc_sre_group",
                "open_message_id": (
                    "om_card_message_1"
                ),
            },
            "action": {
                "value": value,
            },
        },
    }


def trusted(
    trust,
    payload,
):
    return trust.accept(
        payload
    )


def test_raw_payload_cannot_bypass_long_connection_trust_boundary():
    _, adapter, _ = components()

    with pytest.raises(
        FeishuUntrustedTransportError
    ):
        adapter.normalize_inbound(
            message_event()
        )


@pytest.mark.asyncio
async def test_trusted_message_normalizes_and_attests_actor():
    trust, adapter, verifier = (
        components()
    )

    inbound = adapter.normalize_inbound(
        trusted(
            trust,
            message_event(),
        )
    )

    assert isinstance(
        inbound,
        ChatOpsInboundMessage,
    )
    assert (
        inbound.conversation.channel
        == "feishu"
    )
    assert (
        inbound.conversation.tenant_id
        == "tenant-a"
    )
    assert (
        inbound.conversation.conversation_id
        == "oc_sre_group"
    )
    assert (
        inbound.conversation.thread_id
        == "om_root"
    )
    assert (
        inbound.external_actor_id
        == "ou_user_1"
    )
    assert inbound.text == "根因是什么？"

    actor = await verifier.verify(
        inbound
    )

    assert actor.channel == "feishu"
    assert actor.tenant_id == "tenant-a"
    assert (
        actor.external_actor_id
        == "ou_user_1"
    )
    assert (
        actor.verification_method
        == "feishu_official_sdk_long_connection"
    )


@pytest.mark.asyncio
async def test_modified_normalized_message_loses_attestation():
    trust, adapter, verifier = (
        components()
    )

    inbound = adapter.normalize_inbound(
        trusted(
            trust,
            message_event(),
        )
    )

    changed = inbound.model_copy(
        update={
            "text": "批准执行",
        }
    )

    with pytest.raises(
        Exception
    ):
        await verifier.verify(
            changed
        )


def test_message_requires_human_text_sender():
    trust, adapter, _ = components()

    with pytest.raises(
        FeishuPayloadError
    ):
        adapter.normalize_inbound(
            trusted(
                trust,
                message_event(
                    sender_type="bot",
                ),
            )
        )

    with pytest.raises(
        FeishuPayloadError
    ):
        adapter.normalize_inbound(
            trusted(
                trust,
                message_event(
                    message_type="image",
                ),
            )
        )


def test_group_thread_falls_back_to_parent_then_message_id():
    trust, adapter, _ = components()

    parent = adapter.normalize_inbound(
        trusted(
            trust,
            message_event(
                root_id="",
                parent_id="om_parent",
            ),
        )
    )
    assert (
        parent.conversation.thread_id
        == "om_parent"
    )

    top_level = (
        adapter.normalize_inbound(
            trusted(
                trust,
                message_event(
                    root_id="",
                    parent_id="",
                ),
            )
        )
    )
    assert (
        top_level.conversation.thread_id
        == "om_message_1"
    )


def test_p2p_message_has_no_thread_id():
    trust, adapter, _ = components()

    inbound = adapter.normalize_inbound(
        trusted(
            trust,
            message_event(
                chat_type="p2p",
                root_id="",
            ),
        )
    )

    assert (
        inbound.conversation.thread_id
        is None
    )


def test_explicit_incident_id_can_be_extracted_from_text():
    trust, adapter, _ = components()

    inbound = adapter.normalize_inbound(
        trusted(
            trust,
            message_event(
                text=(
                    "incident_id: "
                    + INCIDENT_ID
                    + " 现在状态怎么样？"
                )
            ),
        )
    )

    assert (
        inbound.incident_id
        == INCIDENT_ID
    )


def test_card_write_action_is_allowlisted_and_requires_incident():
    trust, adapter, _ = components()

    inbound = adapter.normalize_inbound(
        trusted(
            trust,
            card_action(),
        )
    )

    assert inbound.text == "批准执行"
    assert inbound.incident_id == INCIDENT_ID
    assert (
        inbound.conversation.thread_id
        == "om_card_message_1"
    )

    with pytest.raises(
        FeishuPayloadError
    ):
        adapter.normalize_inbound(
            trusted(
                trust,
                card_action(
                    incident_id=None,
                ),
            )
        )

    with pytest.raises(
        FeishuPayloadError
    ):
        adapter.normalize_inbound(
            trusted(
                trust,
                card_action(
                    action_name=(
                        "arbitrary.shell"
                    ),
                ),
            )
        )


def test_render_outbound_uses_card_v2_and_allowlisted_buttons_only():
    _, adapter, _ = components()

    reply = ConversationReplyPlan(
        conversation_id=(
            "chatops:test"
        ),
        incident_id=INCIDENT_ID,
        intent=ConversationIntent.STATUS,
        mode=(
            ConversationReplyMode.READ_ONLY
        ),
        sections=(
            ConversationReplySection(
                key="status",
                title="当前状态",
                lines=(
                    "Incident 已确认",
                ),
            ),
        ),
        suggested_actions=(
            "show_rca",
            "request_remediation",
            "not_allowed",
        ),
    )

    outbound = ChatOpsOutboundMessage(
        conversation=(
            adapter.normalize_inbound(
                trusted(
                    adapter.trust_boundary,
                    message_event(),
                )
            ).conversation
        ),
        reply_to_message_id=(
            "om_message_1"
        ),
        reply=reply,
    )

    rendered = adapter.render_outbound(
        outbound
    )

    assert rendered[
        "msg_type"
    ] == "interactive"
    assert rendered[
        "card"
    ][
        "schema"
    ] == "2.0"

    actions = [
        item
        for element
        in rendered["card"]["body"]["elements"]
        if element["tag"] == "action"
        for item in element["actions"]
    ]

    names = {
        item["value"][
            "ai_sre_action"
        ]
        for item in actions
    }

    assert names == {
        "show_rca",
        "action.resume",
    }


def test_render_approved_write_outcome_can_offer_resume():
    _, adapter, _ = components()

    outcome = ChatOpsWriteOutcome(
        success=True,
        status=(
            ChatOpsWriteStatus.APPROVED
        ),
        operation="approve",
        incident_id=INCIDENT_ID,
        approval_id="approval-1",
        operator_id="operator-a",
        message="Approval approved.",
    )

    card = adapter.render_write_outcome(
        outcome
    )

    assert card["schema"] == "2.0"

    actions = [
        item
        for element
        in card["body"]["elements"]
        if element["tag"] == "action"
        for item in element["actions"]
    ]

    assert [
        item["value"][
            "ai_sre_action"
        ]
        for item in actions
    ] == [
        "action.resume"
    ]


def test_feishu_core_has_no_network_or_runtime_write_authority():
    from pathlib import Path

    import services.agent_runtime.app.conversation.feishu as module

    source = Path(
        module.__file__
    ).read_text(
        encoding="utf-8"
    )

    forbidden = [
        "lark_oapi",
        "httpx",
        "requests.",
        "aiohttp",
        "ApprovalService",
        "ActionRuntime",
        "KubernetesProductionExecutor",
        ".approve(",
        ".reject(",
        ".resume(",
        ".execute(",
        "app_secret",
        "tenant_access_token",
    ]

    assert [
        value
        for value in forbidden
        if value in source
    ] == []
