from __future__ import annotations

import hashlib
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "feishu-chatops-adapter-core-v1-fix1"

AFTER_NAME = "feishu_chatops_adapter_core_v1_fix1_after.txt"
ERROR_NAME = "feishu_chatops_adapter_core_v1_fix1_error.txt"

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/conversation/chatops.py': '3c73a9a86bc34712a77ac3ea3196e44ee355989f0b869b73500e83d791d80966', 'services/agent_runtime/app/conversation/identity.py': '440318d59d17155cd6e24763736243624ba758ae8eace41627ea12a5d175ec76', 'services/agent_runtime/app/conversation/write_bridge.py': 'fc9dd30b0771672d66b75a4bd0f1eb34fad7e57677c0ccba8a66a12186fd5e7c', 'services/agent_runtime/app/conversation/orchestrator.py': 'f41d09ae583479d65c486fea4d1e4d667fe81be0330a2c66c32225208a4789d1', 'services/agent_runtime/app/runtime/runtime.py': 'dfe189a4c25f0c5c48393935360956f55bfe12afe2c7d273d6d57ba330db4650'}

SOURCES = {'services/agent_runtime/app/conversation/feishu.py': 'from __future__ import annotations\n\nimport hashlib\nimport json\nimport re\nimport threading\nimport time\n\nfrom copy import deepcopy\nfrom dataclasses import dataclass\nfrom typing import Any, Callable\n\nfrom services.agent_runtime.app.conversation.chatops import (\n    BaseChatOpsChannelAdapter,\n    ChatOpsConversationRef,\n    ChatOpsInboundMessage,\n    ChatOpsOutboundMessage,\n)\nfrom services.agent_runtime.app.conversation.identity import (\n    BaseChatOpsActorVerifier,\n    ChatOpsActorVerificationError,\n    ChatOpsVerifiedActor,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationReplyPlan,\n)\nfrom services.agent_runtime.app.conversation.write_bridge import (\n    ChatOpsWriteOutcome,\n)\n\n\nFEISHU_CHANNEL = "feishu"\nFEISHU_MESSAGE_EVENT = "im.message.receive_v1"\nFEISHU_CARD_ACTION_EVENT = "card.action.trigger"\n\n_TRUST_METHOD = "feishu_official_sdk_long_connection"\n\n_INCIDENT_PATTERN = re.compile(\n    r"(?:incident(?:_id)?|事故)\\s*[:=]\\s*"\n    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"\n    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"\n    r"[0-9a-fA-F]{12})"\n)\n\n\nclass FeishuChatOpsError(RuntimeError):\n    """Base fail-closed Feishu ChatOps adapter error."""\n\n\nclass FeishuUntrustedTransportError(\n    FeishuChatOpsError\n):\n    """Raw payload bypassed the trusted official-SDK transport boundary."""\n\n\nclass FeishuPayloadError(\n    FeishuChatOpsError\n):\n    """Trusted Feishu payload is malformed or outside the v1 contract."""\n\n\nclass FeishuUnsupportedEventError(\n    FeishuChatOpsError\n):\n    """Trusted Feishu event type is unsupported by this adapter."""\n\n\n@dataclass(frozen=True)\nclass FeishuTrustedLongConnectionCallback:\n    """\n    Opaque payload wrapper created only at the official-SDK long-connection\n    boundary. The adapter refuses arbitrary raw dictionaries.\n    """\n\n    payload: dict[str, Any]\n    verification_method: str = _TRUST_METHOD\n\n\nclass FeishuLongConnectionTrustBoundary:\n    """\n    Explicit protocol boundary for the future official lark-oapi transport.\n\n    v1 does not open a network connection. The future transport handler must\n    call accept() before a callback may enter FeishuChatOpsAdapter.\n    """\n\n    def accept(\n        self,\n        payload: Any,\n    ) -> FeishuTrustedLongConnectionCallback:\n        if not isinstance(\n            payload,\n            dict,\n        ):\n            raise FeishuPayloadError(\n                "Feishu callback payload must be an object"\n            )\n\n        header = self._mapping(\n            payload.get("header"),\n            "Feishu callback header",\n        )\n\n        event_type = self._required_text(\n            header.get("event_type"),\n            "Feishu event_type",\n            128,\n        )\n\n        if event_type not in {\n            FEISHU_MESSAGE_EVENT,\n            FEISHU_CARD_ACTION_EVENT,\n        }:\n            raise FeishuUnsupportedEventError(\n                "Feishu event type is unsupported"\n            )\n\n        return FeishuTrustedLongConnectionCallback(\n            payload=deepcopy(payload)\n        )\n\n    @staticmethod\n    def _mapping(\n        value: Any,\n        label: str,\n    ) -> dict[str, Any]:\n        if not isinstance(\n            value,\n            dict,\n        ):\n            raise FeishuPayloadError(\n                f"{label} is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        label: str,\n        max_length: int,\n    ) -> str:\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or len(value) > max_length\n            or "\\x00" in value\n        ):\n            raise FeishuPayloadError(\n                f"{label} is invalid"\n            )\n\n        return value\n\n\n@dataclass(frozen=True)\nclass _FeishuActorAttestation:\n    fingerprint: str\n    channel: str\n    tenant_id: str | None\n    actor_id: str\n    verification_method: str\n    expires_at_monotonic: float\n\n\nclass FeishuActorAttestationRegistry:\n    """\n    Bounded short-lived proof that a normalized ChatOps message crossed the\n    trusted Feishu transport boundary.\n\n    The existing ChatOpsAuthenticatedWriteBridge still performs Runtime\n    authentication and RBAC. This registry grants neither role nor permission.\n    """\n\n    def __init__(\n        self,\n        *,\n        ttl_seconds: float = 300.0,\n        max_entries: int = 10000,\n        clock: Callable[[], float] = time.monotonic,\n    ) -> None:\n        if (\n            isinstance(ttl_seconds, bool)\n            or not isinstance(\n                ttl_seconds,\n                (int, float),\n            )\n            or ttl_seconds <= 0\n            or ttl_seconds > 900\n        ):\n            raise ValueError(\n                "Feishu attestation TTL is invalid"\n            )\n\n        if (\n            isinstance(max_entries, bool)\n            or not isinstance(max_entries, int)\n            or max_entries <= 0\n            or max_entries > 100000\n        ):\n            raise ValueError(\n                "Feishu attestation max_entries is invalid"\n            )\n\n        if not callable(clock):\n            raise TypeError(\n                "Feishu attestation clock is invalid"\n            )\n\n        self.ttl_seconds = float(\n            ttl_seconds\n        )\n        self.max_entries = max_entries\n        self._clock = clock\n        self._items: dict[\n            str,\n            _FeishuActorAttestation,\n        ] = {}\n        self._lock = threading.Lock()\n\n    def attest(\n        self,\n        message: ChatOpsInboundMessage,\n        *,\n        verification_method: str,\n    ) -> None:\n        if not isinstance(\n            message,\n            ChatOpsInboundMessage,\n        ):\n            raise TypeError(\n                "Feishu attestation requires ChatOpsInboundMessage"\n            )\n\n        actor_id = message.external_actor_id\n\n        if actor_id is None:\n            raise FeishuPayloadError(\n                "Feishu actor identity is missing"\n            )\n\n        if (\n            not isinstance(\n                verification_method,\n                str,\n            )\n            or not verification_method\n            or verification_method\n            != verification_method.strip()\n            or len(verification_method) > 256\n            or "\\x00" in verification_method\n        ):\n            raise FeishuPayloadError(\n                "Feishu verification method is invalid"\n            )\n\n        fingerprint = (\n            self.message_fingerprint(\n                message\n            )\n        )\n\n        now = float(\n            self._clock()\n        )\n\n        value = _FeishuActorAttestation(\n            fingerprint=fingerprint,\n            channel=(\n                message.conversation.channel\n            ),\n            tenant_id=(\n                message.conversation.tenant_id\n            ),\n            actor_id=actor_id,\n            verification_method=(\n                verification_method\n            ),\n            expires_at_monotonic=(\n                now + self.ttl_seconds\n            ),\n        )\n\n        with self._lock:\n            self._prune_locked(now)\n\n            if (\n                len(self._items)\n                >= self.max_entries\n                and fingerprint\n                not in self._items\n            ):\n                oldest = min(\n                    self._items,\n                    key=lambda key: (\n                        self._items[\n                            key\n                        ].expires_at_monotonic,\n                        key,\n                    ),\n                )\n                self._items.pop(\n                    oldest,\n                    None,\n                )\n\n            self._items[\n                fingerprint\n            ] = value\n\n    def verify(\n        self,\n        message: ChatOpsInboundMessage,\n    ) -> _FeishuActorAttestation:\n        if not isinstance(\n            message,\n            ChatOpsInboundMessage,\n        ):\n            raise ChatOpsActorVerificationError(\n                "Feishu actor verification message is invalid"\n            )\n\n        fingerprint = (\n            self.message_fingerprint(\n                message\n            )\n        )\n\n        now = float(\n            self._clock()\n        )\n\n        with self._lock:\n            self._prune_locked(now)\n            value = self._items.get(\n                fingerprint\n            )\n\n        if value is None:\n            raise ChatOpsActorVerificationError(\n                "Feishu message has no trusted transport attestation"\n            )\n\n        if (\n            value.channel\n            != message.conversation.channel\n            or value.tenant_id\n            != message.conversation.tenant_id\n            or value.actor_id\n            != message.external_actor_id\n        ):\n            raise ChatOpsActorVerificationError(\n                "Feishu actor attestation does not match message identity"\n            )\n\n        return value\n\n    def _prune_locked(\n        self,\n        now: float,\n    ) -> None:\n        expired = [\n            key\n            for key, value\n            in self._items.items()\n            if (\n                value.expires_at_monotonic\n                <= now\n            )\n        ]\n\n        for key in expired:\n            self._items.pop(\n                key,\n                None,\n            )\n\n    @staticmethod\n    def message_fingerprint(\n        message: ChatOpsInboundMessage,\n    ) -> str:\n        payload = json.dumps(\n            {\n                "binding": (\n                    message.conversation\n                    .binding_key()\n                ),\n                "message_id": (\n                    message.message_id\n                ),\n                "actor": (\n                    message.external_actor_id\n                ),\n                "text": message.text,\n                "incident_id": (\n                    message.incident_id\n                ),\n            },\n            ensure_ascii=False,\n            sort_keys=True,\n            separators=(",", ":"),\n        )\n\n        return hashlib.sha256(\n            payload.encode("utf-8")\n        ).hexdigest()\n\n\nclass FeishuChatOpsActorVerifier(\n    BaseChatOpsActorVerifier\n):\n    """Bridge a trusted Feishu attestation into the existing identity layer."""\n\n    def __init__(\n        self,\n        attestations: FeishuActorAttestationRegistry,\n    ) -> None:\n        if not isinstance(\n            attestations,\n            FeishuActorAttestationRegistry,\n        ):\n            raise TypeError(\n                "Feishu actor verifier requires attestation registry"\n            )\n\n        self.attestations = attestations\n\n    async def verify(\n        self,\n        message: ChatOpsInboundMessage,\n    ) -> ChatOpsVerifiedActor:\n        value = self.attestations.verify(\n            message\n        )\n\n        return ChatOpsVerifiedActor(\n            channel=value.channel,\n            tenant_id=value.tenant_id,\n            external_actor_id=value.actor_id,\n            verification_method=(\n                value.verification_method\n            ),\n        )\n\n\nclass FeishuChatOpsAdapter(\n    BaseChatOpsChannelAdapter\n):\n    """\n    Pure Feishu protocol adapter.\n\n    Supported inbound contracts:\n    - im.message.receive_v1 human text messages\n    - card.action.trigger allowlisted card actions\n\n    This class performs no network I/O, token exchange, Runtime authentication,\n    Approval mutation, Action execution or Verification write.\n    """\n\n    _CARD_ACTION_TEXT = {\n        "show_status": (\n            "现在状态怎么样？"\n        ),\n        "show_rca": (\n            "根因是什么？"\n        ),\n        "show_evidence": (\n            "有哪些证据？"\n        ),\n        "what_next": (\n            "下一步怎么办？"\n        ),\n        "approval.approve": (\n            "批准执行"\n        ),\n        "approval.reject": (\n            "拒绝"\n        ),\n        "action.resume": (\n            "执行修复"\n        ),\n    }\n\n    _SUGGESTION_TO_ACTION = {\n        "show_status": "show_status",\n        "show_rca": "show_rca",\n        "show_evidence": (\n            "show_evidence"\n        ),\n        "what_next": "what_next",\n        "request_remediation": (\n            "action.resume"\n        ),\n    }\n\n    _WRITE_ACTIONS = {\n        "approval.approve",\n        "approval.reject",\n        "action.resume",\n    }\n\n    def __init__(\n        self,\n        *,\n        trust_boundary: (\n            FeishuLongConnectionTrustBoundary\n        ),\n        attestations: (\n            FeishuActorAttestationRegistry\n        ),\n    ) -> None:\n        if not isinstance(\n            trust_boundary,\n            FeishuLongConnectionTrustBoundary,\n        ):\n            raise TypeError(\n                "Feishu adapter requires long-connection trust boundary"\n            )\n\n        if not isinstance(\n            attestations,\n            FeishuActorAttestationRegistry,\n        ):\n            raise TypeError(\n                "Feishu adapter requires actor attestation registry"\n            )\n\n        self.trust_boundary = (\n            trust_boundary\n        )\n        self.attestations = (\n            attestations\n        )\n\n    def normalize_inbound(\n        self,\n        payload: Any,\n    ) -> ChatOpsInboundMessage:\n        if not isinstance(\n            payload,\n            FeishuTrustedLongConnectionCallback,\n        ):\n            raise FeishuUntrustedTransportError(\n                "Feishu adapter accepts only trusted official-SDK "\n                "long-connection callbacks"\n            )\n\n        raw = deepcopy(\n            payload.payload\n        )\n\n        header = self._mapping(\n            raw.get("header"),\n            "Feishu header",\n        )\n\n        event_type = self._required_text(\n            header.get("event_type"),\n            "Feishu event_type",\n            128,\n        )\n\n        if event_type == FEISHU_MESSAGE_EVENT:\n            message = self._message_event(\n                raw\n            )\n        elif event_type == FEISHU_CARD_ACTION_EVENT:\n            message = self._card_action(\n                raw\n            )\n        else:\n            raise FeishuUnsupportedEventError(\n                "Feishu event type is unsupported"\n            )\n\n        self.attestations.attest(\n            message,\n            verification_method=(\n                payload.verification_method\n            ),\n        )\n\n        return message\n\n    def render_outbound(\n        self,\n        message: ChatOpsOutboundMessage,\n    ) -> dict[str, Any]:\n        if not isinstance(\n            message,\n            ChatOpsOutboundMessage,\n        ):\n            raise TypeError(\n                "Feishu outbound rendering requires ChatOpsOutboundMessage"\n            )\n\n        return {\n            "receive_id": (\n                message.conversation\n                .conversation_id\n            ),\n            "receive_id_type": "chat_id",\n            "reply_to_message_id": (\n                message.reply_to_message_id\n            ),\n            "msg_type": "interactive",\n            "card": self.render_reply_plan(\n                message.reply\n            ),\n        }\n\n    def render_reply_plan(\n        self,\n        reply: ConversationReplyPlan,\n    ) -> dict[str, Any]:\n        if not isinstance(\n            reply,\n            ConversationReplyPlan,\n        ):\n            raise TypeError(\n                "Feishu reply renderer requires ConversationReplyPlan"\n            )\n\n        elements: list[\n            dict[str, Any]\n        ] = []\n\n        for section in reply.sections:\n            text = "\\n".join(\n                section.lines\n            )\n\n            if text:\n                elements.append(\n                    {\n                        "tag": "markdown",\n                        "content": (\n                            "**"\n                            + section.title\n                            + "**\\n"\n                            + text\n                        ),\n                    }\n                )\n\n        buttons = self._buttons(\n            actions=(\n                reply.suggested_actions\n            ),\n            incident_id=(\n                reply.incident_id\n            ),\n        )\n\n        if buttons:\n            elements.append(\n                {\n                    "tag": "action",\n                    "actions": buttons,\n                }\n            )\n\n        if not elements:\n            elements.append(\n                {\n                    "tag": "markdown",\n                    "content": (\n                        "AI SRE Agent 已处理该消息。"\n                    ),\n                }\n            )\n\n        return self._card(\n            title="AI SRE Agent",\n            elements=elements,\n        )\n\n    def render_write_outcome(\n        self,\n        outcome: ChatOpsWriteOutcome,\n    ) -> dict[str, Any]:\n        if not isinstance(\n            outcome,\n            ChatOpsWriteOutcome,\n        ):\n            raise TypeError(\n                "Feishu write renderer requires ChatOpsWriteOutcome"\n            )\n\n        lines = [\n            outcome.message,\n            "状态: " + outcome.status.value,\n        ]\n\n        if outcome.operator_id:\n            lines.append(\n                "Operator: "\n                + outcome.operator_id\n            )\n\n        if outcome.execution_status:\n            lines.append(\n                "Execution: "\n                + outcome.execution_status\n            )\n\n        if outcome.verification_status:\n            lines.append(\n                "Verification: "\n                + outcome.verification_status\n            )\n\n        elements: list[\n            dict[str, Any]\n        ] = [\n            {\n                "tag": "markdown",\n                "content": "\\n".join(\n                    lines\n                ),\n            }\n        ]\n\n        if (\n            outcome.status.value\n            == "approved"\n        ):\n            buttons = self._buttons(\n                actions=(\n                    "action.resume",\n                ),\n                incident_id=(\n                    outcome.incident_id\n                ),\n            )\n\n            if buttons:\n                elements.append(\n                    {\n                        "tag": "action",\n                        "actions": buttons,\n                    }\n                )\n\n        return self._card(\n            title=(\n                "AI SRE Agent · 操作结果"\n            ),\n            elements=elements,\n        )\n\n    def _message_event(\n        self,\n        raw: dict[str, Any],\n    ) -> ChatOpsInboundMessage:\n        header = self._mapping(\n            raw.get("header"),\n            "Feishu header",\n        )\n\n        event = self._mapping(\n            raw.get("event"),\n            "Feishu message event",\n        )\n\n        sender = self._mapping(\n            event.get("sender"),\n            "Feishu sender",\n        )\n\n        sender_type = self._required_text(\n            sender.get("sender_type"),\n            "Feishu sender_type",\n            64,\n        )\n\n        if sender_type != "user":\n            raise FeishuPayloadError(\n                "Feishu ChatOps accepts only human user messages"\n            )\n\n        sender_id = self._mapping(\n            sender.get("sender_id"),\n            "Feishu sender_id",\n        )\n\n        actor_id = self._required_text(\n            sender_id.get("open_id"),\n            "Feishu sender open_id",\n            256,\n        )\n\n        tenant_id = self._optional_text(\n            header.get("tenant_key")\n            or sender.get("tenant_key"),\n            "Feishu tenant_key",\n            256,\n        )\n\n        message = self._mapping(\n            event.get("message"),\n            "Feishu message",\n        )\n\n        message_id = self._required_text(\n            message.get("message_id"),\n            "Feishu message_id",\n            256,\n        )\n\n        chat_id = self._required_text(\n            message.get("chat_id"),\n            "Feishu chat_id",\n            256,\n        )\n\n        chat_type = self._required_text(\n            message.get("chat_type"),\n            "Feishu chat_type",\n            64,\n        )\n\n        if chat_type not in {\n            "group",\n            "p2p",\n        }:\n            raise FeishuPayloadError(\n                "Feishu chat_type is unsupported"\n            )\n\n        message_type = self._required_text(\n            message.get("message_type"),\n            "Feishu message_type",\n            64,\n        )\n\n        if message_type != "text":\n            raise FeishuPayloadError(\n                "Feishu ChatOps v1 supports text messages only"\n            )\n\n        text = self._message_text(\n            message\n        )\n\n        thread_id = None\n\n        if chat_type == "group":\n            thread_id = (\n                self._optional_text(\n                    message.get("root_id"),\n                    "Feishu root_id",\n                    256,\n                )\n                or self._optional_text(\n                    message.get("parent_id"),\n                    "Feishu parent_id",\n                    256,\n                )\n                or message_id\n            )\n\n        incident_id = (\n            self._incident_from_text(\n                text\n            )\n        )\n\n        return ChatOpsInboundMessage(\n            conversation=(\n                ChatOpsConversationRef(\n                    channel=(\n                        FEISHU_CHANNEL\n                    ),\n                    tenant_id=tenant_id,\n                    conversation_id=chat_id,\n                    thread_id=thread_id,\n                )\n            ),\n            message_id=message_id,\n            external_actor_id=actor_id,\n            text=text,\n            incident_id=incident_id,\n        )\n\n    def _card_action(\n        self,\n        raw: dict[str, Any],\n    ) -> ChatOpsInboundMessage:\n        header = self._mapping(\n            raw.get("header"),\n            "Feishu header",\n        )\n\n        event = self._mapping(\n            raw.get("event"),\n            "Feishu card action event",\n        )\n\n        operator = self._mapping(\n            event.get("operator"),\n            "Feishu card operator",\n        )\n\n        actor_id = self._required_text(\n            operator.get("open_id"),\n            "Feishu operator open_id",\n            256,\n        )\n\n        tenant_id = self._optional_text(\n            operator.get("tenant_key")\n            or header.get("tenant_key"),\n            "Feishu tenant_key",\n            256,\n        )\n\n        context = self._mapping(\n            event.get("context"),\n            "Feishu card context",\n        )\n\n        chat_id = self._required_text(\n            context.get("open_chat_id"),\n            "Feishu card open_chat_id",\n            256,\n        )\n\n        card_message_id = (\n            self._required_text(\n                context.get(\n                    "open_message_id"\n                ),\n                "Feishu card open_message_id",\n                256,\n            )\n        )\n\n        action = self._mapping(\n            event.get("action"),\n            "Feishu card action",\n        )\n\n        value = self._mapping(\n            action.get("value"),\n            "Feishu card action value",\n        )\n\n        action_name = self._required_text(\n            value.get("ai_sre_action"),\n            "Feishu AI SRE action",\n            128,\n        )\n\n        text = self._CARD_ACTION_TEXT.get(\n            action_name\n        )\n\n        if text is None:\n            raise FeishuPayloadError(\n                "Feishu card action is not allowlisted"\n            )\n\n        incident_id = self._optional_text(\n            value.get("incident_id"),\n            "Feishu incident_id",\n            256,\n        )\n\n        if (\n            action_name\n            in self._WRITE_ACTIONS\n            and incident_id is None\n        ):\n            raise FeishuPayloadError(\n                "Feishu write action requires incident_id"\n            )\n\n        event_id = self._required_text(\n            header.get("event_id"),\n            "Feishu card event_id",\n            256,\n        )\n\n        return ChatOpsInboundMessage(\n            conversation=(\n                ChatOpsConversationRef(\n                    channel=(\n                        FEISHU_CHANNEL\n                    ),\n                    tenant_id=tenant_id,\n                    conversation_id=chat_id,\n                    thread_id=(\n                        card_message_id\n                    ),\n                )\n            ),\n            message_id=event_id,\n            external_actor_id=actor_id,\n            text=text,\n            incident_id=incident_id,\n        )\n\n    def _message_text(\n        self,\n        message: dict[str, Any],\n    ) -> str:\n        content_raw = self._required_text(\n            message.get("content"),\n            "Feishu message content",\n            4096,\n        )\n\n        try:\n            content = json.loads(\n                content_raw\n            )\n        except (\n            TypeError,\n            ValueError,\n        ) as exc:\n            raise FeishuPayloadError(\n                "Feishu text message content is invalid JSON"\n            ) from exc\n\n        if not isinstance(\n            content,\n            dict,\n        ):\n            raise FeishuPayloadError(\n                "Feishu text message content must be an object"\n            )\n\n        text = self._required_text(\n            content.get("text"),\n            "Feishu text",\n            4096,\n        )\n\n        mentions = message.get(\n            "mentions"\n        )\n\n        if isinstance(\n            mentions,\n            list,\n        ):\n            for item in mentions:\n                if not isinstance(\n                    item,\n                    dict,\n                ):\n                    continue\n\n                key = item.get("key")\n\n                if (\n                    isinstance(key, str)\n                    and key\n                ):\n                    text = text.replace(\n                        key,\n                        " ",\n                    )\n\n        text = " ".join(\n            text.split()\n        )\n\n        if not text:\n            raise FeishuPayloadError(\n                "Feishu text message is empty after mention removal"\n            )\n\n        return text[:4096]\n\n    def _buttons(\n        self,\n        *,\n        actions: tuple[str, ...] | list[str],\n        incident_id: str | None,\n    ) -> list[\n        dict[str, Any]\n    ]:\n        values: list[\n            dict[str, Any]\n        ] = []\n\n        for action in actions:\n            normalized = (\n                self._SUGGESTION_TO_ACTION.get(\n                    action,\n                    action,\n                )\n            )\n\n            if normalized not in (\n                self._CARD_ACTION_TEXT\n            ):\n                continue\n\n            if (\n                normalized\n                in self._WRITE_ACTIONS\n                and incident_id is None\n            ):\n                continue\n\n            label = {\n                "show_status": "查看状态",\n                "show_rca": "查看根因",\n                "show_evidence": "查看证据",\n                "what_next": "下一步",\n                "approval.approve": "批准执行",\n                "approval.reject": "拒绝",\n                "action.resume": "执行修复",\n            }[\n                normalized\n            ]\n\n            value = {\n                "ai_sre_action": normalized,\n            }\n\n            if incident_id is not None:\n                value[\n                    "incident_id"\n                ] = incident_id\n\n            values.append(\n                {\n                    "tag": "button",\n                    "text": {\n                        "tag": "plain_text",\n                        "content": label,\n                    },\n                    "type": (\n                        "primary"\n                        if normalized\n                        in {\n                            "approval.approve",\n                            "action.resume",\n                        }\n                        else "default"\n                    ),\n                    "value": value,\n                }\n            )\n\n        return values[:5]\n\n    @staticmethod\n    def _card(\n        *,\n        title: str,\n        elements: list[\n            dict[str, Any]\n        ],\n    ) -> dict[str, Any]:\n        return {\n            "schema": "2.0",\n            "config": {\n                "update_multi": True,\n            },\n            "header": {\n                "title": {\n                    "tag": "plain_text",\n                    "content": title,\n                },\n            },\n            "body": {\n                "elements": elements,\n            },\n        }\n\n    @staticmethod\n    def _incident_from_text(\n        text: str,\n    ) -> str | None:\n        match = _INCIDENT_PATTERN.search(\n            text\n        )\n\n        if match is None:\n            return None\n\n        return match.group(\n            1\n        ).lower()\n\n    @staticmethod\n    def _mapping(\n        value: Any,\n        label: str,\n    ) -> dict[str, Any]:\n        if not isinstance(\n            value,\n            dict,\n        ):\n            raise FeishuPayloadError(\n                f"{label} is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        label: str,\n        max_length: int,\n    ) -> str:\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or len(value) > max_length\n            or "\\x00" in value\n        ):\n            raise FeishuPayloadError(\n                f"{label} is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _optional_text(\n        value: Any,\n        label: str,\n        max_length: int,\n    ) -> str | None:\n        # Feishu optional identifier fields may use an empty string\n        # to represent absence, such as root_id and parent_id.\n        # Only exact absence markers become None; non-empty values\n        # still pass through strict _required_text validation.\n        if value is None or value == "":\n            return None\n\n        return (\n            FeishuChatOpsAdapter\n            ._required_text(\n                value,\n                label,\n                max_length,\n            )\n        )\n\n\n__all__ = [\n    "FEISHU_CARD_ACTION_EVENT",\n    "FEISHU_CHANNEL",\n    "FEISHU_MESSAGE_EVENT",\n    "FeishuActorAttestationRegistry",\n    "FeishuChatOpsActorVerifier",\n    "FeishuChatOpsAdapter",\n    "FeishuChatOpsError",\n    "FeishuLongConnectionTrustBoundary",\n    "FeishuPayloadError",\n    "FeishuTrustedLongConnectionCallback",\n    "FeishuUnsupportedEventError",\n    "FeishuUntrustedTransportError",\n]\n', 'services/agent_runtime/tests/test_feishu_chatops_adapter.py': 'from __future__ import annotations\n\nimport copy\n\nimport pytest\n\nfrom services.agent_runtime.app.conversation.chatops import (\n    ChatOpsInboundMessage,\n    ChatOpsOutboundMessage,\n)\nfrom services.agent_runtime.app.conversation.feishu import (\n    FEISHU_CARD_ACTION_EVENT,\n    FEISHU_MESSAGE_EVENT,\n    FeishuActorAttestationRegistry,\n    FeishuChatOpsActorVerifier,\n    FeishuChatOpsAdapter,\n    FeishuLongConnectionTrustBoundary,\n    FeishuPayloadError,\n    FeishuUntrustedTransportError,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n)\nfrom services.agent_runtime.app.conversation.write_bridge import (\n    ChatOpsWriteOutcome,\n    ChatOpsWriteStatus,\n)\n\n\nINCIDENT_ID = (\n    "7f0d8f0a-9e8a-4b78-9b62-"\n    "486f7039e142"\n)\n\n\ndef components():\n    trust = (\n        FeishuLongConnectionTrustBoundary()\n    )\n    attestations = (\n        FeishuActorAttestationRegistry()\n    )\n    adapter = FeishuChatOpsAdapter(\n        trust_boundary=trust,\n        attestations=attestations,\n    )\n    verifier = FeishuChatOpsActorVerifier(\n        attestations\n    )\n    return trust, adapter, verifier\n\n\ndef message_event(\n    *,\n    sender_type="user",\n    message_type="text",\n    text="@_user_1 根因是什么？",\n    root_id="om_root",\n    parent_id="",\n    chat_type="group",\n):\n    return {\n        "schema": "2.0",\n        "header": {\n            "event_id": "evt-msg-1",\n            "event_type": (\n                FEISHU_MESSAGE_EVENT\n            ),\n            "tenant_key": "tenant-a",\n        },\n        "event": {\n            "sender": {\n                "sender_id": {\n                    "open_id": "ou_user_1",\n                },\n                "sender_type": sender_type,\n                "tenant_key": "tenant-a",\n            },\n            "message": {\n                "message_id": "om_message_1",\n                "root_id": root_id,\n                "parent_id": parent_id,\n                "chat_id": "oc_sre_group",\n                "chat_type": chat_type,\n                "message_type": message_type,\n                "content": (\n                    \'{"text": \'\n                    + repr(text).replace(\n                        "\'",\n                        \'"\',\n                    )\n                    + "}"\n                ),\n                "mentions": [\n                    {\n                        "key": "@_user_1",\n                    }\n                ],\n            },\n        },\n    }\n\n\ndef card_action(\n    *,\n    action_name="approval.approve",\n    incident_id=INCIDENT_ID,\n):\n    value = {\n        "ai_sre_action": action_name,\n    }\n\n    if incident_id is not None:\n        value[\n            "incident_id"\n        ] = incident_id\n\n    return {\n        "schema": "2.0",\n        "header": {\n            "event_id": "evt-card-1",\n            "event_type": (\n                FEISHU_CARD_ACTION_EVENT\n            ),\n            "tenant_key": "tenant-a",\n        },\n        "event": {\n            "operator": {\n                "open_id": "ou_user_1",\n                "tenant_key": "tenant-a",\n            },\n            "context": {\n                "open_chat_id": "oc_sre_group",\n                "open_message_id": (\n                    "om_card_message_1"\n                ),\n            },\n            "action": {\n                "value": value,\n            },\n        },\n    }\n\n\ndef trusted(\n    trust,\n    payload,\n):\n    return trust.accept(\n        payload\n    )\n\n\ndef test_raw_payload_cannot_bypass_long_connection_trust_boundary():\n    _, adapter, _ = components()\n\n    with pytest.raises(\n        FeishuUntrustedTransportError\n    ):\n        adapter.normalize_inbound(\n            message_event()\n        )\n\n\n@pytest.mark.asyncio\nasync def test_trusted_message_normalizes_and_attests_actor():\n    trust, adapter, verifier = (\n        components()\n    )\n\n    inbound = adapter.normalize_inbound(\n        trusted(\n            trust,\n            message_event(),\n        )\n    )\n\n    assert isinstance(\n        inbound,\n        ChatOpsInboundMessage,\n    )\n    assert (\n        inbound.conversation.channel\n        == "feishu"\n    )\n    assert (\n        inbound.conversation.tenant_id\n        == "tenant-a"\n    )\n    assert (\n        inbound.conversation.conversation_id\n        == "oc_sre_group"\n    )\n    assert (\n        inbound.conversation.thread_id\n        == "om_root"\n    )\n    assert (\n        inbound.external_actor_id\n        == "ou_user_1"\n    )\n    assert inbound.text == "根因是什么？"\n\n    actor = await verifier.verify(\n        inbound\n    )\n\n    assert actor.channel == "feishu"\n    assert actor.tenant_id == "tenant-a"\n    assert (\n        actor.external_actor_id\n        == "ou_user_1"\n    )\n    assert (\n        actor.verification_method\n        == "feishu_official_sdk_long_connection"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_modified_normalized_message_loses_attestation():\n    trust, adapter, verifier = (\n        components()\n    )\n\n    inbound = adapter.normalize_inbound(\n        trusted(\n            trust,\n            message_event(),\n        )\n    )\n\n    changed = inbound.model_copy(\n        update={\n            "text": "批准执行",\n        }\n    )\n\n    with pytest.raises(\n        Exception\n    ):\n        await verifier.verify(\n            changed\n        )\n\n\ndef test_message_requires_human_text_sender():\n    trust, adapter, _ = components()\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        adapter.normalize_inbound(\n            trusted(\n                trust,\n                message_event(\n                    sender_type="bot",\n                ),\n            )\n        )\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        adapter.normalize_inbound(\n            trusted(\n                trust,\n                message_event(\n                    message_type="image",\n                ),\n            )\n        )\n\n\ndef test_group_thread_falls_back_to_parent_then_message_id():\n    trust, adapter, _ = components()\n\n    parent = adapter.normalize_inbound(\n        trusted(\n            trust,\n            message_event(\n                root_id="",\n                parent_id="om_parent",\n            ),\n        )\n    )\n    assert (\n        parent.conversation.thread_id\n        == "om_parent"\n    )\n\n    top_level = (\n        adapter.normalize_inbound(\n            trusted(\n                trust,\n                message_event(\n                    root_id="",\n                    parent_id="",\n                ),\n            )\n        )\n    )\n    assert (\n        top_level.conversation.thread_id\n        == "om_message_1"\n    )\n\n\ndef test_p2p_message_has_no_thread_id():\n    trust, adapter, _ = components()\n\n    inbound = adapter.normalize_inbound(\n        trusted(\n            trust,\n            message_event(\n                chat_type="p2p",\n                root_id="",\n            ),\n        )\n    )\n\n    assert (\n        inbound.conversation.thread_id\n        is None\n    )\n\n\ndef test_explicit_incident_id_can_be_extracted_from_text():\n    trust, adapter, _ = components()\n\n    inbound = adapter.normalize_inbound(\n        trusted(\n            trust,\n            message_event(\n                text=(\n                    "incident_id: "\n                    + INCIDENT_ID\n                    + " 现在状态怎么样？"\n                )\n            ),\n        )\n    )\n\n    assert (\n        inbound.incident_id\n        == INCIDENT_ID\n    )\n\n\ndef test_card_write_action_is_allowlisted_and_requires_incident():\n    trust, adapter, _ = components()\n\n    inbound = adapter.normalize_inbound(\n        trusted(\n            trust,\n            card_action(),\n        )\n    )\n\n    assert inbound.text == "批准执行"\n    assert inbound.incident_id == INCIDENT_ID\n    assert (\n        inbound.conversation.thread_id\n        == "om_card_message_1"\n    )\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        adapter.normalize_inbound(\n            trusted(\n                trust,\n                card_action(\n                    incident_id=None,\n                ),\n            )\n        )\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        adapter.normalize_inbound(\n            trusted(\n                trust,\n                card_action(\n                    action_name=(\n                        "arbitrary.shell"\n                    ),\n                ),\n            )\n        )\n\n\ndef test_render_outbound_uses_card_v2_and_allowlisted_buttons_only():\n    _, adapter, _ = components()\n\n    reply = ConversationReplyPlan(\n        conversation_id=(\n            "chatops:test"\n        ),\n        incident_id=INCIDENT_ID,\n        intent=ConversationIntent.STATUS,\n        mode=(\n            ConversationReplyMode.READ_ONLY\n        ),\n        sections=(\n            ConversationReplySection(\n                key="status",\n                title="当前状态",\n                lines=(\n                    "Incident 已确认",\n                ),\n            ),\n        ),\n        suggested_actions=(\n            "show_rca",\n            "request_remediation",\n            "not_allowed",\n        ),\n    )\n\n    outbound = ChatOpsOutboundMessage(\n        conversation=(\n            adapter.normalize_inbound(\n                trusted(\n                    adapter.trust_boundary,\n                    message_event(),\n                )\n            ).conversation\n        ),\n        reply_to_message_id=(\n            "om_message_1"\n        ),\n        reply=reply,\n    )\n\n    rendered = adapter.render_outbound(\n        outbound\n    )\n\n    assert rendered[\n        "msg_type"\n    ] == "interactive"\n    assert rendered[\n        "card"\n    ][\n        "schema"\n    ] == "2.0"\n\n    actions = [\n        item\n        for element\n        in rendered["card"]["body"]["elements"]\n        if element["tag"] == "action"\n        for item in element["actions"]\n    ]\n\n    names = {\n        item["value"][\n            "ai_sre_action"\n        ]\n        for item in actions\n    }\n\n    assert names == {\n        "show_rca",\n        "action.resume",\n    }\n\n\ndef test_render_approved_write_outcome_can_offer_resume():\n    _, adapter, _ = components()\n\n    outcome = ChatOpsWriteOutcome(\n        success=True,\n        status=(\n            ChatOpsWriteStatus.APPROVED\n        ),\n        operation="approve",\n        incident_id=INCIDENT_ID,\n        approval_id="approval-1",\n        operator_id="operator-a",\n        message="Approval approved.",\n    )\n\n    card = adapter.render_write_outcome(\n        outcome\n    )\n\n    assert card["schema"] == "2.0"\n\n    actions = [\n        item\n        for element\n        in card["body"]["elements"]\n        if element["tag"] == "action"\n        for item in element["actions"]\n    ]\n\n    assert [\n        item["value"][\n            "ai_sre_action"\n        ]\n        for item in actions\n    ] == [\n        "action.resume"\n    ]\n\n\ndef test_feishu_core_has_no_network_or_runtime_write_authority():\n    from pathlib import Path\n\n    import services.agent_runtime.app.conversation.feishu as module\n\n    source = Path(\n        module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        "lark_oapi",\n        "httpx",\n        "requests.",\n        "aiohttp",\n        "ApprovalService",\n        "ActionRuntime",\n        "KubernetesProductionExecutor",\n        ".approve(",\n        ".reject(",\n        ".resume(",\n        ".execute(",\n        "app_secret",\n        "tenant_access_token",\n    ]\n\n    assert [\n        value\n        for value in forbidden\n        if value in source\n    ] == []\n'}


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run this installer from inside ai-reliability-platform."
    )


def normalize(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        value,
        encoding="utf-8",
        newline="\n",
    )


def section(report: list[str], title: str) -> None:
    report.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=tuple(command),
        returncode=process.returncode,
        stdout=normalize(process.stdout),
        stderr=normalize(process.stderr),
    )


def add_command(
    report: list[str],
    result: CommandResult,
) -> None:
    section(
        report,
        "COMMAND: " + result.name,
    )

    report.extend(
        [
            " ".join(result.command),
            "",
            "ExitCode: " + str(result.returncode),
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            "Required current file is missing: "
            + relative
        )

    actual = raw_sha256(path)
    expected = EXPECTED_RAW_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            relative
            + " changed after the captured ChatOps baseline. "
            + "expected_raw_sha256="
            + expected
            + " actual_raw_sha256="
            + actual
            + ". Refusing stale Feishu installation; capture current code again."
        )


def require_tests(
    root: Path,
    values: list[str],
) -> list[str]:
    missing = [
        value
        for value in values
        if not (root / value).exists()
    ]

    if missing:
        raise RuntimeError(
            "Required compatibility tests are missing: "
            + ", ".join(missing)
        )

    return values


def remove_created(
    created: list[Path],
) -> None:
    for path in reversed(created):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    targets = {
        root / relative: source
        for relative, source
        in SOURCES.items()
    }

    created: list[Path] = []

    report = [
        "Feishu ChatOps Adapter Core v1 fix1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Baseline:",
        "- captured 2026-08-12 current repository has no Feishu adapter core",
        "- lark_oapi is intentionally not required by this stage",
        "- existing channel-neutral ChatOps identity/write boundary remains authoritative",
        "",
        "This stage installs only:",
        "- pure Feishu protocol normalization",
        "- explicit trusted long-connection callback wrapper",
        "- short-lived exact-message actor attestation",
        "- BaseChatOpsActorVerifier implementation",
        "- ConversationReplyPlan / ChatOpsWriteOutcome card rendering",
        "- Feishu adapter focused tests",
        "",
        "Supported inbound contracts:",
        "- im.message.receive_v1 human text messages",
        "- card.action.trigger allowlisted interactive-card actions",
        "",
        "Safety:",
        "- no existing production source file is modified",
        "- no lark-oapi/httpx/requests/aiohttp use in Feishu core",
        "- no Feishu network connection",
        "- no app secret/token access",
        "- no direct ApprovalService or ActionRuntime authority",
        "- no LLM/Kubernetes/Prometheus request",
        "- installer rolls back all newly created Feishu files on any failure",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        for relative in EXPECTED_RAW_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )
            report.append(
                relative
                + "="
                + EXPECTED_RAW_HASHES[
                    relative
                ]
            )

        section(
            report,
            "TARGET PREFLIGHT",
        )

        for path in targets:
            relative = str(
                path.relative_to(root)
            ).replace("\\", "/")

            if path.exists():
                raise RuntimeError(
                    "Feishu v1 target already exists; refusing overwrite: "
                    + relative
                )

            report.append(
                "new_target=" + relative
            )

        for path, source in targets.items():
            write_text(
                path,
                source,
            )
            created.append(path)

        syntax = run_command(
            root=root,
            name="Feishu adapter Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(path.relative_to(root))
                    for path in targets
                ],
            ],
        )
        add_command(
            report,
            syntax,
        )
        if syntax.returncode != 0:
            raise RuntimeError(
                "Feishu adapter syntax failed"
            )

        focused = run_command(
            root=root,
            name="Feishu ChatOps focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_chatops_adapter.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_chatops_authenticated_write_bridge.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_durable_conversation_chatops_contract.py"
                ),
                "-q",
            ],
        )
        add_command(
            report,
            focused,
        )
        if focused.returncode != 0:
            raise RuntimeError(
                "Feishu ChatOps focused tests failed"
            )

        compatibility_paths = require_tests(
            root,
            [
                (
                    "services/agent_runtime/tests/"
                    "test_security_authentication_service.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_security_policy.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_approval_rbac.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_resume_rbac.py"
                ),
            ],
        )

        compatibility = run_command(
            root=root,
            name="Existing Authentication / RBAC compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                *compatibility_paths,
                "-q",
            ],
        )
        add_command(
            report,
            compatibility,
        )
        if compatibility.returncode != 0:
            raise RuntimeError(
                "Feishu Authentication/RBAC compatibility failed"
            )

        architecture = run_command(
            root=root,
            name="Feishu adapter architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/feishu.py').read_text(encoding='utf-8'); "
                    "checks={"
                    "'message_event':'im.message.receive_v1' in p,"
                    "'card_action':'card.action.trigger' in p,"
                    "'long_connection_trust':'FeishuLongConnectionTrustBoundary' in p,"
                    "'actor_attestation':'FeishuActorAttestationRegistry' in p,"
                    "'actor_verifier':'BaseChatOpsActorVerifier' in p,"
                    "'card_v2':'schema' in p and '2.0' in p"
                    "}; "
                    "print(checks); "
                    "raise SystemExit(0 if all(checks.values()) else 1)"
                ),
            ],
        )
        add_command(
            report,
            architecture,
        )
        if architecture.returncode != 0:
            raise RuntimeError(
                "Feishu adapter architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Feishu network/write-authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/feishu.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ["
                    "'lark_oapi','httpx','requests.','aiohttp',"
                    "'ApprovalService','ActionRuntime','KubernetesProductionExecutor',"
                    "'.approve(','.reject(','.resume(','.execute(',"
                    "'app_secret','tenant_access_token'"
                    "] if x in p]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )
        add_command(
            report,
            authority,
        )
        if authority.returncode != 0:
            raise RuntimeError(
                "Feishu adapter network/write boundary failed"
            )

        full_suite = run_command(
            root=root,
            name="Agent Runtime full test suite",
            command=[
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests",
                "-q",
            ],
        )
        add_command(
            report,
            full_suite,
        )
        if full_suite.returncode != 0:
            raise RuntimeError(
                "Agent Runtime full test suite failed"
            )

        status = run_command(
            root=root,
            name="Git status for Feishu v1 targets",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(path.relative_to(root))
                    for path in targets
                ],
            ],
        )
        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Feishu ChatOps Adapter Core v1 fix1 is installed.",
                "",
                "Installed boundaries:",
                "1. raw dictionaries cannot enter FeishuChatOpsAdapter directly",
                "2. future official-SDK handler must cross FeishuLongConnectionTrustBoundary",
                "3. trusted normalization creates bounded exact-message actor attestation",
                "4. FeishuChatOpsActorVerifier feeds the existing authenticated write bridge",
                "5. card actions are exact allowlist only",
                "6. write actions require incident_id",
                "7. renderer creates card payloads but does not send them",
                "",
                "Still NOT installed:",
                "- lark-oapi dependency",
                "- real Feishu long-connection client",
                "- network sender",
                "- credential loader",
                "- automatic Runtime startup wiring",
                "",
                "Next stage after this result is reviewed:",
                "- Feishu Official SDK Long-Connection Transport v1",
                "",
                "Upload only: " + AFTER_NAME,
            ]
        )

        after.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("FEISHU CHATOPS ADAPTER CORE V1 FIX1 PASSED")
        print("=" * 72)
        print()
        print("No existing production source file was modified.")
        print("No Feishu/network/LLM/Kubernetes/Prometheus request was sent.")
        print()
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        remove_created(
            created
        )

        report.extend(
            [
                "",
                "=" * 120,
                "ROLLBACK",
                "=" * 120,
                "",
                "All newly created Feishu v1 target files were removed.",
                "Existing source files were never modified by this installer.",
            ]
        )

        error.write_text(
            "\n".join(
                [
                    "Feishu ChatOps Adapter Core v1 fix1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    type(exc).__name__
                    + ": "
                    + str(exc),
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only: " + ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("FEISHU CHATOPS ADAPTER CORE V1 FIX1 FAILED")
        print("=" * 72)
        print()
        print("New Feishu files were rolled back.")
        print()
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
