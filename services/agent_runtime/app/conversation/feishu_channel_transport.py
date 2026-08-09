from __future__ import annotations

import hashlib
import json

from typing import Any

from lark_channel import (
    CardActionEvent,
    Events,
    InboundMessage,
)

from services.agent_runtime.app.conversation.chatops import (
    ChatOpsConversationGateway,
    ChatOpsInboundMessage,
)
from services.agent_runtime.app.conversation.feishu import (
    FEISHU_CARD_ACTION_EVENT,
    FEISHU_MESSAGE_EVENT,
    FeishuChatOpsAdapter,
    FeishuPayloadError,
)
from services.agent_runtime.app.conversation.write_bridge import (
    ChatOpsAuthenticatedWriteBridge,
    ChatOpsWriteStatus,
)


class FeishuChannelTransportError(RuntimeError):
    """Base fail-closed error for the official standalone Channel transport."""


class FeishuChannelTransportPayloadError(
    FeishuChannelTransportError
):
    """An official Channel SDK event cannot enter the ChatOps core safely."""


class FeishuChannelTransportSendError(
    FeishuChannelTransportError
):
    """A rendered ChatOps reply could not be sent safely."""


class FeishuOfficialChannelTransport:
    """
    Adapter between the standalone ``lark-channel-sdk`` and AI SRE ChatOps.

    This object deliberately does NOT own credentials and does NOT connect the
    Channel SDK. A later live-runner stage may construct ``FeishuChannel`` and
    call its lifecycle methods explicitly.

    Trust flow:

        lark_channel typed event
            -> this transport
            -> FeishuLongConnectionTrustBoundary
            -> FeishuChatOpsAdapter
            -> ChatOpsConversationGateway
               or explicit ChatOpsAuthenticatedWriteBridge
            -> Feishu Card 2.0
            -> injected channel.send()

    v1 is single-app scoped. The standalone SDK's normalized handler models do
    not expose the tenant key used by our older raw-event envelope, therefore
    normalized live messages intentionally enter ChatOps with tenant_id=None.
    A tenant-aware multi-app transport must be added explicitly rather than
    guessed from unrelated identifiers.
    """

    def __init__(
        self,
        *,
        channel: Any,
        adapter: FeishuChatOpsAdapter,
        gateway: ChatOpsConversationGateway,
        write_bridge: (
            ChatOpsAuthenticatedWriteBridge
            | None
        ) = None,
    ) -> None:
        if not callable(
            getattr(
                channel,
                "on",
                None,
            )
        ):
            raise TypeError(
                "Feishu Channel transport requires channel.on"
            )

        if not callable(
            getattr(
                channel,
                "send",
                None,
            )
        ):
            raise TypeError(
                "Feishu Channel transport requires channel.send"
            )

        if not isinstance(
            adapter,
            FeishuChatOpsAdapter,
        ):
            raise TypeError(
                "Feishu Channel transport requires FeishuChatOpsAdapter"
            )

        if not isinstance(
            gateway,
            ChatOpsConversationGateway,
        ):
            raise TypeError(
                "Feishu Channel transport requires ChatOpsConversationGateway"
            )

        if (
            write_bridge is not None
            and not isinstance(
                write_bridge,
                ChatOpsAuthenticatedWriteBridge,
            )
        ):
            raise TypeError(
                "Feishu Channel write bridge is invalid"
            )

        self.channel = channel
        self.adapter = adapter
        self.gateway = gateway
        self.write_bridge = write_bridge
        self._registered = False

    @property
    def registered(
        self,
    ) -> bool:
        return self._registered

    def register(
        self,
    ) -> "FeishuOfficialChannelTransport":
        """
        Register handlers only.

        This method never starts WebSocket/network lifecycle and is safe to call
        during explicit application assembly before the separate live runner
        decides whether to connect.
        """

        if self._registered:
            return self

        self.channel.on(
            Events.MESSAGE,
            self.handle_message,
        )
        self.channel.on(
            Events.CARD_ACTION,
            self.handle_card_action,
        )

        self._registered = True
        return self

    def normalize_message(
        self,
        event: InboundMessage,
    ) -> ChatOpsInboundMessage:
        """
        Convert the official SDK's normalized human text message back through
        the existing Feishu Core trust boundary.

        We intentionally use ``body_text`` so the bot's own @-mention is not
        treated as part of the command text.
        """

        if not isinstance(
            event,
            InboundMessage,
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel message type is invalid"
            )

        if (
            event.sender_type
            != "user"
            or event.sender_is_bot
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel ChatOps accepts human user messages only"
            )

        if event.raw_content_type != "text":
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel ChatOps v1 supports text messages only"
            )

        message_id = self._required_text(
            event.message_id,
            "Feishu Channel message_id",
        )
        chat_id = self._required_text(
            event.chat_id,
            "Feishu Channel chat_id",
        )
        actor_id = self._required_text(
            event.sender_id,
            "Feishu Channel sender open_id",
        )

        chat_type = self._required_text(
            event.chat_type,
            "Feishu Channel chat_type",
        )

        if chat_type not in {
            "p2p",
            "group",
            "topic",
        }:
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel chat_type is unsupported"
            )

        text = self._message_text(
            event.body_text
        )

        root_id = ""
        parent_id = ""

        if chat_type in {
            "group",
            "topic",
        }:
            thread_id = getattr(
                event.conversation,
                "thread_id",
                None,
            )

            if isinstance(
                thread_id,
                str,
            ):
                root_id = thread_id

            parent = event.reply_to_message_id

            if isinstance(
                parent,
                str,
            ):
                parent_id = parent

        raw = {
            "schema": "2.0",
            "header": {
                "event_id": (
                    self._stable_id(
                        "message",
                        message_id,
                        chat_id,
                        actor_id,
                    )
                ),
                "event_type": (
                    FEISHU_MESSAGE_EVENT
                ),
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": actor_id,
                    },
                    "sender_type": "user",
                },
                "message": {
                    "message_id": (
                        message_id
                    ),
                    "root_id": root_id,
                    "parent_id": (
                        parent_id
                    ),
                    "chat_id": chat_id,
                    "chat_type": (
                        "p2p"
                        if chat_type
                        == "p2p"
                        else "group"
                    ),
                    "message_type": (
                        "text"
                    ),
                    "content": (
                        json.dumps(
                            {
                                "text": text,
                            },
                            ensure_ascii=False,
                            separators=(
                                ",",
                                ":",
                            ),
                        )
                    ),
                    # body_text already removed the current bot's mention.
                    "mentions": [],
                },
            },
        }

        try:
            trusted = (
                self.adapter
                .trust_boundary
                .accept(
                    raw
                )
            )

            return (
                self.adapter
                .normalize_inbound(
                    trusted
                )
            )

        except FeishuPayloadError:
            raise

        except Exception as exc:
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel message normalization failed"
            ) from exc

    def normalize_card_action(
        self,
        event: CardActionEvent,
    ) -> ChatOpsInboundMessage:
        """
        Convert the official SDK card callback through the existing Feishu Core
        allowlist. This transport never duplicates or relaxes action policy.
        """

        if not isinstance(
            event,
            CardActionEvent,
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel card action type is invalid"
            )

        message_id = self._required_text(
            event.message_id,
            "Feishu Channel card message_id",
        )
        chat_id = self._required_text(
            event.chat_id,
            "Feishu Channel card chat_id",
        )
        actor_id = self._required_text(
            getattr(
                event.operator,
                "open_id",
                None,
            ),
            "Feishu Channel card operator open_id",
        )

        value = getattr(
            event.action,
            "value",
            None,
        )

        if not isinstance(
            value,
            dict,
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel card action value is invalid"
            )

        event_id = self._stable_id(
            "card",
            message_id,
            chat_id,
            actor_id,
            self._canonical_json(
                value
            ),
        )

        raw = {
            "schema": "2.0",
            "header": {
                "event_id": event_id,
                "event_type": (
                    FEISHU_CARD_ACTION_EVENT
                ),
            },
            "event": {
                "operator": {
                    "open_id": actor_id,
                },
                "context": {
                    "open_chat_id": (
                        chat_id
                    ),
                    "open_message_id": (
                        message_id
                    ),
                },
                "action": {
                    "value": dict(
                        value
                    ),
                },
            },
        }

        try:
            trusted = (
                self.adapter
                .trust_boundary
                .accept(
                    raw
                )
            )

            return (
                self.adapter
                .normalize_inbound(
                    trusted
                )
            )

        except FeishuPayloadError:
            raise

        except Exception as exc:
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel card normalization failed"
            ) from exc

    async def handle_message(
        self,
        event: InboundMessage,
    ) -> Any:
        inbound = self.normalize_message(
            event
        )

        thread_id = getattr(
            event.conversation,
            "thread_id",
            None,
        )

        return await self._dispatch(
            inbound,
            reply_to=event.message_id,
            reply_in_thread=(
                isinstance(
                    thread_id,
                    str,
                )
                and bool(
                    thread_id
                )
            ),
        )

    async def handle_card_action(
        self,
        event: CardActionEvent,
    ) -> Any:
        inbound = (
            self.normalize_card_action(
                event
            )
        )

        return await self._dispatch(
            inbound,
            reply_to=event.message_id,
            reply_in_thread=False,
        )

    async def _dispatch(
        self,
        inbound: ChatOpsInboundMessage,
        *,
        reply_to: str,
        reply_in_thread: bool,
    ) -> Any:
        if self.write_bridge is not None:
            outcome = await (
                self.write_bridge
                .handle(
                    inbound
                )
            )

            if (
                outcome.status
                != ChatOpsWriteStatus
                .NO_WRITE_INTENT
            ):
                card = (
                    self.adapter
                    .render_write_outcome(
                        outcome
                    )
                )

                return await self._send_card(
                    inbound=inbound,
                    card=card,
                    reply_to=reply_to,
                    reply_in_thread=(
                        reply_in_thread
                    ),
                )

        outbound = await (
            self.gateway
            .handle(
                inbound
            )
        )

        rendered = (
            self.adapter
            .render_outbound(
                outbound
            )
        )

        card = rendered.get(
            "card"
        )

        if not isinstance(
            card,
            dict,
        ):
            raise FeishuChannelTransportSendError(
                "Feishu Channel rendered card is invalid"
            )

        return await self._send_card(
            inbound=inbound,
            card=card,
            reply_to=reply_to,
            reply_in_thread=(
                reply_in_thread
            ),
        )

    async def _send_card(
        self,
        *,
        inbound: ChatOpsInboundMessage,
        card: dict[str, Any],
        reply_to: str,
        reply_in_thread: bool,
    ) -> Any:
        chat_id = (
            inbound.conversation
            .conversation_id
        )

        result = await self.channel.send(
            chat_id,
            {
                "card": card,
            },
            {
                "reply_to": reply_to,
                "reply_in_thread": (
                    reply_in_thread
                ),
                "receive_id_type": (
                    "chat_id"
                ),
                "uuid": self._stable_id(
                    "send",
                    inbound.conversation
                    .binding_key(),
                    inbound.message_id,
                    reply_to,
                ),
            },
        )

        success = getattr(
            result,
            "success",
            None,
        )

        if success is False:
            raise FeishuChannelTransportSendError(
                "Feishu Channel send returned failure"
            )

        return result

    @staticmethod
    def _message_text(
        value: Any,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel body_text is invalid"
            )

        normalized = " ".join(
            value.split()
        )

        if (
            not normalized
            or len(
                normalized
            ) > 4096
            or "\x00"
            in normalized
        ):
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel body_text is invalid"
            )

        return normalized

    @staticmethod
    def _required_text(
        value: Any,
        label: str,
    ) -> str:
        if (
            not isinstance(
                value,
                str,
            )
            or not value
            or value
            != value.strip()
            or len(value) > 256
            or "\x00" in value
        ):
            raise FeishuChannelTransportPayloadError(
                label
                + " is invalid"
            )

        return value

    @staticmethod
    def _canonical_json(
        value: Any,
    ) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise FeishuChannelTransportPayloadError(
                "Feishu Channel card action value is not serializable"
            ) from exc

    @staticmethod
    def _stable_id(
        *parts: str,
    ) -> str:
        value = json.dumps(
            list(parts),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            value.encode(
                "utf-8"
            )
        ).hexdigest()


__all__ = [
    "FeishuChannelTransportError",
    "FeishuChannelTransportPayloadError",
    "FeishuChannelTransportSendError",
    "FeishuOfficialChannelTransport",
]
