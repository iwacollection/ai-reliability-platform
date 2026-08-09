from __future__ import annotations

import hashlib
import json

from abc import ABC, abstractmethod
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
)

from services.agent_runtime.app.conversation.models import (
    ConversationReplyPlan,
    ConversationTurnRequest,
)
from services.agent_runtime.app.conversation.orchestrator import (
    ConversationOrchestrator,
)


ShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
    ),
]

MessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=4096,
    ),
]


class ChatOpsConversationRef(BaseModel):
    """
    External channel/thread identity.

    The opaque binding_key is what ConversationSessionStore persists. Raw
    workspace/conversation/thread IDs do not become the SQLite primary key.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    channel: ShortText
    tenant_id: ShortText | None = None
    conversation_id: ShortText
    thread_id: ShortText | None = None

    def binding_key(
        self,
    ) -> str:
        payload = json.dumps(
            [
                self.channel,
                self.tenant_id or "",
                self.conversation_id,
                self.thread_id or "",
            ],
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        digest = hashlib.sha256(
            payload.encode(
                "utf-8"
            )
        ).hexdigest()

        return (
            "chatops:"
            + digest
        )


class ChatOpsInboundMessage(BaseModel):
    """
    Channel-neutral inbound message.

    external_actor_id is an untrusted external reference only. It does not
    confer Runtime authentication/RBAC identity or write authority.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    conversation: ChatOpsConversationRef
    message_id: ShortText
    external_actor_id: ShortText | None = None
    text: MessageText
    incident_id: ShortText | None = None


class ChatOpsOutboundMessage(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    conversation: ChatOpsConversationRef
    reply_to_message_id: ShortText
    reply: ConversationReplyPlan


class BaseChatOpsChannelAdapter(ABC):
    """
    Pure transformation contract for one channel implementation.

    v1 intentionally has no send(), HTTP client, SDK client, webhook listener,
    authentication mutation, Approval or Action method.
    """

    @abstractmethod
    def normalize_inbound(
        self,
        payload: Any,
    ) -> ChatOpsInboundMessage:
        raise NotImplementedError

    @abstractmethod
    def render_outbound(
        self,
        message: ChatOpsOutboundMessage,
    ) -> Any:
        raise NotImplementedError


class ChatOpsConversationGateway:
    """
    Thin bridge from a normalized channel message to ConversationOrchestrator.

    It performs no network I/O and no write action. A write-capable user intent
    still returns ConversationReplyMode.WRITE_ACTION_REQUIRED from the existing
    Orchestrator.
    """

    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
    ) -> None:
        if not isinstance(
            orchestrator,
            ConversationOrchestrator,
        ):
            raise TypeError(
                "ChatOps Conversation Orchestrator is invalid"
            )

        self.orchestrator = (
            orchestrator
        )

    async def handle(
        self,
        message: ChatOpsInboundMessage,
    ) -> ChatOpsOutboundMessage:
        if not isinstance(
            message,
            ChatOpsInboundMessage,
        ):
            raise TypeError(
                "ChatOps inbound message is invalid"
            )

        reply = await self.orchestrator.handle(
            ConversationTurnRequest(
                conversation_id=(
                    message.conversation
                    .binding_key()
                ),
                incident_id=(
                    message.incident_id
                ),
                text=message.text,
            )
        )

        return ChatOpsOutboundMessage(
            conversation=(
                message.conversation
            ),
            reply_to_message_id=(
                message.message_id
            ),
            reply=reply,
        )


__all__ = [
    "BaseChatOpsChannelAdapter",
    "ChatOpsConversationGateway",
    "ChatOpsConversationRef",
    "ChatOpsInboundMessage",
    "ChatOpsOutboundMessage",
]
