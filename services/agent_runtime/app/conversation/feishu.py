from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from services.agent_runtime.app.conversation.chatops import (
    BaseChatOpsChannelAdapter,
    ChatOpsConversationRef,
    ChatOpsInboundMessage,
    ChatOpsOutboundMessage,
)
from services.agent_runtime.app.conversation.identity import (
    BaseChatOpsActorVerifier,
    ChatOpsActorVerificationError,
    ChatOpsVerifiedActor,
)
from services.agent_runtime.app.conversation.models import (
    ConversationReplyPlan,
)
from services.agent_runtime.app.conversation.write_bridge import (
    ChatOpsWriteOutcome,
)


FEISHU_CHANNEL = "feishu"
FEISHU_MESSAGE_EVENT = "im.message.receive_v1"
FEISHU_CARD_ACTION_EVENT = "card.action.trigger"

_TRUST_METHOD = "feishu_official_sdk_long_connection"

_INCIDENT_PATTERN = re.compile(
    r"(?:incident(?:_id)?|事故)\s*[:=]\s*"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12})"
)


class FeishuChatOpsError(RuntimeError):
    """Base fail-closed Feishu ChatOps adapter error."""


class FeishuUntrustedTransportError(
    FeishuChatOpsError
):
    """Raw payload bypassed the trusted official-SDK transport boundary."""


class FeishuPayloadError(
    FeishuChatOpsError
):
    """Trusted Feishu payload is malformed or outside the v1 contract."""


class FeishuUnsupportedEventError(
    FeishuChatOpsError
):
    """Trusted Feishu event type is unsupported by this adapter."""


@dataclass(frozen=True)
class FeishuTrustedLongConnectionCallback:
    """
    Opaque payload wrapper created only at the official-SDK long-connection
    boundary. The adapter refuses arbitrary raw dictionaries.
    """

    payload: dict[str, Any]
    verification_method: str = _TRUST_METHOD


class FeishuLongConnectionTrustBoundary:
    """
    Explicit protocol boundary for the official Feishu Channel transport.

    The Feishu Core itself does not open a network connection. An external
    transport must call accept() before a callback may enter
    FeishuChatOpsAdapter.
    """

    def accept(
        self,
        payload: Any,
    ) -> FeishuTrustedLongConnectionCallback:
        if not isinstance(
            payload,
            dict,
        ):
            raise FeishuPayloadError(
                "Feishu callback payload must be an object"
            )

        header = self._mapping(
            payload.get("header"),
            "Feishu callback header",
        )

        event_type = self._required_text(
            header.get("event_type"),
            "Feishu event_type",
            128,
        )

        if event_type not in {
            FEISHU_MESSAGE_EVENT,
            FEISHU_CARD_ACTION_EVENT,
        }:
            raise FeishuUnsupportedEventError(
                "Feishu event type is unsupported"
            )

        return FeishuTrustedLongConnectionCallback(
            payload=deepcopy(payload)
        )

    @staticmethod
    def _mapping(
        value: Any,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(
            value,
            dict,
        ):
            raise FeishuPayloadError(
                f"{label} is invalid"
            )

        return value

    @staticmethod
    def _required_text(
        value: Any,
        label: str,
        max_length: int,
    ) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > max_length
            or "\x00" in value
        ):
            raise FeishuPayloadError(
                f"{label} is invalid"
            )

        return value


@dataclass(frozen=True)
class _FeishuActorAttestation:
    fingerprint: str
    channel: str
    tenant_id: str | None
    actor_id: str
    verification_method: str
    expires_at_monotonic: float


class FeishuActorAttestationRegistry:
    """
    Bounded short-lived proof that a normalized ChatOps message crossed the
    trusted Feishu transport boundary.

    The existing ChatOpsAuthenticatedWriteBridge still performs Runtime
    authentication and RBAC. This registry grants neither role nor permission.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_entries: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(
                ttl_seconds,
                (int, float),
            )
            or ttl_seconds <= 0
            or ttl_seconds > 900
        ):
            raise ValueError(
                "Feishu attestation TTL is invalid"
            )

        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries <= 0
            or max_entries > 100000
        ):
            raise ValueError(
                "Feishu attestation max_entries is invalid"
            )

        if not callable(clock):
            raise TypeError(
                "Feishu attestation clock is invalid"
            )

        self.ttl_seconds = float(
            ttl_seconds
        )
        self.max_entries = max_entries
        self._clock = clock
        self._items: dict[
            str,
            _FeishuActorAttestation,
        ] = {}
        self._lock = threading.Lock()

    def attest(
        self,
        message: ChatOpsInboundMessage,
        *,
        verification_method: str,
    ) -> None:
        if not isinstance(
            message,
            ChatOpsInboundMessage,
        ):
            raise TypeError(
                "Feishu attestation requires ChatOpsInboundMessage"
            )

        actor_id = message.external_actor_id

        if actor_id is None:
            raise FeishuPayloadError(
                "Feishu actor identity is missing"
            )

        if (
            not isinstance(
                verification_method,
                str,
            )
            or not verification_method
            or verification_method
            != verification_method.strip()
            or len(verification_method) > 256
            or "\x00" in verification_method
        ):
            raise FeishuPayloadError(
                "Feishu verification method is invalid"
            )

        fingerprint = (
            self.message_fingerprint(
                message
            )
        )

        now = float(
            self._clock()
        )

        value = _FeishuActorAttestation(
            fingerprint=fingerprint,
            channel=(
                message.conversation.channel
            ),
            tenant_id=(
                message.conversation.tenant_id
            ),
            actor_id=actor_id,
            verification_method=(
                verification_method
            ),
            expires_at_monotonic=(
                now + self.ttl_seconds
            ),
        )

        with self._lock:
            self._prune_locked(now)

            if (
                len(self._items)
                >= self.max_entries
                and fingerprint
                not in self._items
            ):
                oldest = min(
                    self._items,
                    key=lambda key: (
                        self._items[
                            key
                        ].expires_at_monotonic,
                        key,
                    ),
                )
                self._items.pop(
                    oldest,
                    None,
                )

            self._items[
                fingerprint
            ] = value

    def verify(
        self,
        message: ChatOpsInboundMessage,
    ) -> _FeishuActorAttestation:
        if not isinstance(
            message,
            ChatOpsInboundMessage,
        ):
            raise ChatOpsActorVerificationError(
                "Feishu actor verification message is invalid"
            )

        fingerprint = (
            self.message_fingerprint(
                message
            )
        )

        now = float(
            self._clock()
        )

        with self._lock:
            self._prune_locked(now)
            value = self._items.get(
                fingerprint
            )

        if value is None:
            raise ChatOpsActorVerificationError(
                "Feishu message has no trusted transport attestation"
            )

        if (
            value.channel
            != message.conversation.channel
            or value.tenant_id
            != message.conversation.tenant_id
            or value.actor_id
            != message.external_actor_id
        ):
            raise ChatOpsActorVerificationError(
                "Feishu actor attestation does not match message identity"
            )

        return value

    def _prune_locked(
        self,
        now: float,
    ) -> None:
        expired = [
            key
            for key, value
            in self._items.items()
            if (
                value.expires_at_monotonic
                <= now
            )
        ]

        for key in expired:
            self._items.pop(
                key,
                None,
            )

    @staticmethod
    def message_fingerprint(
        message: ChatOpsInboundMessage,
    ) -> str:
        payload = json.dumps(
            {
                "binding": (
                    message.conversation
                    .binding_key()
                ),
                "message_id": (
                    message.message_id
                ),
                "actor": (
                    message.external_actor_id
                ),
                "text": message.text,
                "incident_id": (
                    message.incident_id
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()


class FeishuChatOpsActorVerifier(
    BaseChatOpsActorVerifier
):
    """Bridge a trusted Feishu attestation into the existing identity layer."""

    def __init__(
        self,
        attestations: FeishuActorAttestationRegistry,
    ) -> None:
        if not isinstance(
            attestations,
            FeishuActorAttestationRegistry,
        ):
            raise TypeError(
                "Feishu actor verifier requires attestation registry"
            )

        self.attestations = attestations

    async def verify(
        self,
        message: ChatOpsInboundMessage,
    ) -> ChatOpsVerifiedActor:
        value = self.attestations.verify(
            message
        )

        return ChatOpsVerifiedActor(
            channel=value.channel,
            tenant_id=value.tenant_id,
            external_actor_id=value.actor_id,
            verification_method=(
                value.verification_method
            ),
        )


class FeishuChatOpsAdapter(
    BaseChatOpsChannelAdapter
):
    """
    Pure Feishu protocol adapter.

    Supported inbound contracts:
    - im.message.receive_v1 human text messages
    - card.action.trigger allowlisted card actions

    This class performs no network I/O, token exchange, Runtime authentication,
    Approval mutation, Action execution or Verification write.
    """

    _CARD_ACTION_TEXT = {
        "show_status": (
            "现在状态怎么样？"
        ),
        "show_rca": (
            "根因是什么？"
        ),
        "show_evidence": (
            "有哪些证据？"
        ),
        "what_next": (
            "下一步怎么办？"
        ),
        "approval.approve": (
            "批准执行"
        ),
        "approval.reject": (
            "拒绝"
        ),
        "action.resume": (
            "执行修复"
        ),
    }

    _SUGGESTION_TO_ACTION = {
        "show_status": "show_status",
        "show_rca": "show_rca",
        "show_evidence": (
            "show_evidence"
        ),
        "what_next": "what_next",
        "request_remediation": (
            "action.resume"
        ),
    }

    _WRITE_ACTIONS = {
        "approval.approve",
        "approval.reject",
        "action.resume",
    }

    def __init__(
        self,
        *,
        trust_boundary: (
            FeishuLongConnectionTrustBoundary
        ),
        attestations: (
            FeishuActorAttestationRegistry
        ),
    ) -> None:
        if not isinstance(
            trust_boundary,
            FeishuLongConnectionTrustBoundary,
        ):
            raise TypeError(
                "Feishu adapter requires long-connection trust boundary"
            )

        if not isinstance(
            attestations,
            FeishuActorAttestationRegistry,
        ):
            raise TypeError(
                "Feishu adapter requires actor attestation registry"
            )

        self.trust_boundary = (
            trust_boundary
        )
        self.attestations = (
            attestations
        )

    def normalize_inbound(
        self,
        payload: Any,
    ) -> ChatOpsInboundMessage:
        if not isinstance(
            payload,
            FeishuTrustedLongConnectionCallback,
        ):
            raise FeishuUntrustedTransportError(
                "Feishu adapter accepts only trusted official-SDK "
                "long-connection callbacks"
            )

        raw = deepcopy(
            payload.payload
        )

        header = self._mapping(
            raw.get("header"),
            "Feishu header",
        )

        event_type = self._required_text(
            header.get("event_type"),
            "Feishu event_type",
            128,
        )

        if event_type == FEISHU_MESSAGE_EVENT:
            message = self._message_event(
                raw
            )
        elif event_type == FEISHU_CARD_ACTION_EVENT:
            message = self._card_action(
                raw
            )
        else:
            raise FeishuUnsupportedEventError(
                "Feishu event type is unsupported"
            )

        self.attestations.attest(
            message,
            verification_method=(
                payload.verification_method
            ),
        )

        return message

    def render_outbound(
        self,
        message: ChatOpsOutboundMessage,
    ) -> dict[str, Any]:
        if not isinstance(
            message,
            ChatOpsOutboundMessage,
        ):
            raise TypeError(
                "Feishu outbound rendering requires ChatOpsOutboundMessage"
            )

        return {
            "receive_id": (
                message.conversation
                .conversation_id
            ),
            "receive_id_type": "chat_id",
            "reply_to_message_id": (
                message.reply_to_message_id
            ),
            "msg_type": "interactive",
            "card": self.render_reply_plan(
                message.reply
            ),
        }

    def render_reply_plan(
        self,
        reply: ConversationReplyPlan,
    ) -> dict[str, Any]:
        if not isinstance(
            reply,
            ConversationReplyPlan,
        ):
            raise TypeError(
                "Feishu reply renderer requires ConversationReplyPlan"
            )

        elements: list[
            dict[str, Any]
        ] = []

        for section in reply.sections:
            text = "\n".join(
                section.lines
            )

            if text:
                elements.append(
                    {
                        "tag": "markdown",
                        "content": (
                            "**"
                            + section.title
                            + "**\n"
                            + text
                        ),
                    }
                )

        buttons = self._buttons(
            actions=(
                reply.suggested_actions
            ),
            incident_id=(
                reply.incident_id
            ),
        )

        if buttons:
            elements.append(
                {
                    "tag": "action",
                    "actions": buttons,
                }
            )

        if not elements:
            elements.append(
                {
                    "tag": "markdown",
                    "content": (
                        "AI SRE Agent 已处理该消息。"
                    ),
                }
            )

        return self._card(
            title="AI SRE Agent",
            elements=elements,
        )

    def render_write_outcome(
        self,
        outcome: ChatOpsWriteOutcome,
    ) -> dict[str, Any]:
        if not isinstance(
            outcome,
            ChatOpsWriteOutcome,
        ):
            raise TypeError(
                "Feishu write renderer requires ChatOpsWriteOutcome"
            )

        lines = [
            outcome.message,
            "状态: " + outcome.status.value,
        ]

        if outcome.operator_id:
            lines.append(
                "Operator: "
                + outcome.operator_id
            )

        if outcome.execution_status:
            lines.append(
                "Execution: "
                + outcome.execution_status
            )

        if outcome.verification_status:
            lines.append(
                "Verification: "
                + outcome.verification_status
            )

        elements: list[
            dict[str, Any]
        ] = [
            {
                "tag": "markdown",
                "content": "\n".join(
                    lines
                ),
            }
        ]

        if (
            outcome.status.value
            == "approved"
        ):
            buttons = self._buttons(
                actions=(
                    "action.resume",
                ),
                incident_id=(
                    outcome.incident_id
                ),
            )

            if buttons:
                elements.append(
                    {
                        "tag": "action",
                        "actions": buttons,
                    }
                )

        return self._card(
            title=(
                "AI SRE Agent · 操作结果"
            ),
            elements=elements,
        )

    def _message_event(
        self,
        raw: dict[str, Any],
    ) -> ChatOpsInboundMessage:
        header = self._mapping(
            raw.get("header"),
            "Feishu header",
        )

        event = self._mapping(
            raw.get("event"),
            "Feishu message event",
        )

        sender = self._mapping(
            event.get("sender"),
            "Feishu sender",
        )

        sender_type = self._required_text(
            sender.get("sender_type"),
            "Feishu sender_type",
            64,
        )

        if sender_type != "user":
            raise FeishuPayloadError(
                "Feishu ChatOps accepts only human user messages"
            )

        sender_id = self._mapping(
            sender.get("sender_id"),
            "Feishu sender_id",
        )

        actor_id = self._required_text(
            sender_id.get("open_id"),
            "Feishu sender open_id",
            256,
        )

        tenant_id = self._optional_text(
            header.get("tenant_key")
            or sender.get("tenant_key"),
            "Feishu tenant_key",
            256,
        )

        message = self._mapping(
            event.get("message"),
            "Feishu message",
        )

        message_id = self._required_text(
            message.get("message_id"),
            "Feishu message_id",
            256,
        )

        chat_id = self._required_text(
            message.get("chat_id"),
            "Feishu chat_id",
            256,
        )

        chat_type = self._required_text(
            message.get("chat_type"),
            "Feishu chat_type",
            64,
        )

        if chat_type not in {
            "group",
            "p2p",
        }:
            raise FeishuPayloadError(
                "Feishu chat_type is unsupported"
            )

        message_type = self._required_text(
            message.get("message_type"),
            "Feishu message_type",
            64,
        )

        if message_type != "text":
            raise FeishuPayloadError(
                "Feishu ChatOps v1 supports text messages only"
            )

        text = self._message_text(
            message
        )

        thread_id = None

        if chat_type == "group":
            thread_id = (
                self._optional_text(
                    message.get("root_id"),
                    "Feishu root_id",
                    256,
                )
                or self._optional_text(
                    message.get("parent_id"),
                    "Feishu parent_id",
                    256,
                )
                or message_id
            )

        incident_id = (
            self._incident_from_text(
                text
            )
        )

        return ChatOpsInboundMessage(
            conversation=(
                ChatOpsConversationRef(
                    channel=(
                        FEISHU_CHANNEL
                    ),
                    tenant_id=tenant_id,
                    conversation_id=chat_id,
                    thread_id=thread_id,
                )
            ),
            message_id=message_id,
            external_actor_id=actor_id,
            text=text,
            incident_id=incident_id,
        )

    def _card_action(
        self,
        raw: dict[str, Any],
    ) -> ChatOpsInboundMessage:
        header = self._mapping(
            raw.get("header"),
            "Feishu header",
        )

        event = self._mapping(
            raw.get("event"),
            "Feishu card action event",
        )

        operator = self._mapping(
            event.get("operator"),
            "Feishu card operator",
        )

        actor_id = self._required_text(
            operator.get("open_id"),
            "Feishu operator open_id",
            256,
        )

        tenant_id = self._optional_text(
            operator.get("tenant_key")
            or header.get("tenant_key"),
            "Feishu tenant_key",
            256,
        )

        context = self._mapping(
            event.get("context"),
            "Feishu card context",
        )

        chat_id = self._required_text(
            context.get("open_chat_id"),
            "Feishu card open_chat_id",
            256,
        )

        card_message_id = (
            self._required_text(
                context.get(
                    "open_message_id"
                ),
                "Feishu card open_message_id",
                256,
            )
        )

        action = self._mapping(
            event.get("action"),
            "Feishu card action",
        )

        value = self._mapping(
            action.get("value"),
            "Feishu card action value",
        )

        action_name = self._required_text(
            value.get("ai_sre_action"),
            "Feishu AI SRE action",
            128,
        )

        text = self._CARD_ACTION_TEXT.get(
            action_name
        )

        if text is None:
            raise FeishuPayloadError(
                "Feishu card action is not allowlisted"
            )

        incident_id = self._optional_text(
            value.get("incident_id"),
            "Feishu incident_id",
            256,
        )

        if (
            action_name
            in self._WRITE_ACTIONS
            and incident_id is None
        ):
            raise FeishuPayloadError(
                "Feishu write action requires incident_id"
            )

        event_id = self._required_text(
            header.get("event_id"),
            "Feishu card event_id",
            256,
        )

        return ChatOpsInboundMessage(
            conversation=(
                ChatOpsConversationRef(
                    channel=(
                        FEISHU_CHANNEL
                    ),
                    tenant_id=tenant_id,
                    conversation_id=chat_id,
                    thread_id=(
                        card_message_id
                    ),
                )
            ),
            message_id=event_id,
            external_actor_id=actor_id,
            text=text,
            incident_id=incident_id,
        )

    def _message_text(
        self,
        message: dict[str, Any],
    ) -> str:
        content_raw = self._required_text(
            message.get("content"),
            "Feishu message content",
            4096,
        )

        try:
            content = json.loads(
                content_raw
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise FeishuPayloadError(
                "Feishu text message content is invalid JSON"
            ) from exc

        if not isinstance(
            content,
            dict,
        ):
            raise FeishuPayloadError(
                "Feishu text message content must be an object"
            )

        text = self._required_text(
            content.get("text"),
            "Feishu text",
            4096,
        )

        mentions = message.get(
            "mentions"
        )

        if isinstance(
            mentions,
            list,
        ):
            for item in mentions:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                key = item.get("key")

                if (
                    isinstance(key, str)
                    and key
                ):
                    text = text.replace(
                        key,
                        " ",
                    )

        text = " ".join(
            text.split()
        )

        if not text:
            raise FeishuPayloadError(
                "Feishu text message is empty after mention removal"
            )

        return text[:4096]

    def _buttons(
        self,
        *,
        actions: tuple[str, ...] | list[str],
        incident_id: str | None,
    ) -> list[
        dict[str, Any]
    ]:
        values: list[
            dict[str, Any]
        ] = []

        for action in actions:
            normalized = (
                self._SUGGESTION_TO_ACTION.get(
                    action,
                    action,
                )
            )

            if normalized not in (
                self._CARD_ACTION_TEXT
            ):
                continue

            if (
                normalized
                in self._WRITE_ACTIONS
                and incident_id is None
            ):
                continue

            label = {
                "show_status": "查看状态",
                "show_rca": "查看根因",
                "show_evidence": "查看证据",
                "what_next": "下一步",
                "approval.approve": "批准执行",
                "approval.reject": "拒绝",
                "action.resume": "执行修复",
            }[
                normalized
            ]

            value = {
                "ai_sre_action": normalized,
            }

            if incident_id is not None:
                value[
                    "incident_id"
                ] = incident_id

            values.append(
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": label,
                    },
                    "type": (
                        "primary"
                        if normalized
                        in {
                            "approval.approve",
                            "action.resume",
                        }
                        else "default"
                    ),
                    "value": value,
                }
            )

        return values[:5]

    @staticmethod
    def _card(
        *,
        title: str,
        elements: list[
            dict[str, Any]
        ],
    ) -> dict[str, Any]:
        return {
            "schema": "2.0",
            "config": {
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
            },
            "body": {
                "elements": elements,
            },
        }

    @staticmethod
    def _incident_from_text(
        text: str,
    ) -> str | None:
        match = _INCIDENT_PATTERN.search(
            text
        )

        if match is None:
            return None

        return match.group(
            1
        ).lower()

    @staticmethod
    def _mapping(
        value: Any,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(
            value,
            dict,
        ):
            raise FeishuPayloadError(
                f"{label} is invalid"
            )

        return value

    @staticmethod
    def _required_text(
        value: Any,
        label: str,
        max_length: int,
    ) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > max_length
            or "\x00" in value
        ):
            raise FeishuPayloadError(
                f"{label} is invalid"
            )

        return value

    @staticmethod
    def _optional_text(
        value: Any,
        label: str,
        max_length: int,
    ) -> str | None:
        # Feishu optional identifier fields may use an empty string
        # to represent absence, such as root_id and parent_id.
        # Only exact absence markers become None; non-empty values
        # still pass through strict _required_text validation.
        if value is None or value == "":
            return None

        return (
            FeishuChatOpsAdapter
            ._required_text(
                value,
                label,
                max_length,
            )
        )


__all__ = [
    "FEISHU_CARD_ACTION_EVENT",
    "FEISHU_CHANNEL",
    "FEISHU_MESSAGE_EVENT",
    "FeishuActorAttestationRegistry",
    "FeishuChatOpsActorVerifier",
    "FeishuChatOpsAdapter",
    "FeishuChatOpsError",
    "FeishuLongConnectionTrustBoundary",
    "FeishuPayloadError",
    "FeishuTrustedLongConnectionCallback",
    "FeishuUnsupportedEventError",
    "FeishuUntrustedTransportError",
]
