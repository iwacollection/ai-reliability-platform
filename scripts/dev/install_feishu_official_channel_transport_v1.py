from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "feishu-official-channel-transport-v1"
SDK_REQUIREMENT = "lark-channel-sdk==1.2.0"

AFTER_NAME = "feishu_official_channel_transport_v1_after.txt"
ERROR_NAME = "feishu_official_channel_transport_v1_error.txt"

EXPECTED_RAW_HASHES = {'pyproject.toml': '8fdbd7928c2c733c22d3e61f59fa97f040d10baac95dd5aba461363a0faa4edd', 'uv.lock': 'f1f96c0c7468433558044f1d487b8577f0b31dd84e4f2a515e363015444f0fad', 'services/agent_runtime/app/conversation/feishu.py': '36477d225722d4bfcfeb449f5392eb3d13285f8d3893452b3b70863ff6ffb5f6', 'services/agent_runtime/app/conversation/chatops.py': '3c73a9a86bc34712a77ac3ea3196e44ee355989f0b869b73500e83d791d80966', 'services/agent_runtime/app/conversation/identity.py': '440318d59d17155cd6e24763736243624ba758ae8eace41627ea12a5d175ec76', 'services/agent_runtime/app/conversation/write_bridge.py': 'fc9dd30b0771672d66b75a4bd0f1eb34fad7e57677c0ccba8a66a12186fd5e7c', 'services/agent_runtime/app/conversation/orchestrator.py': 'f41d09ae583479d65c486fea4d1e4d667fe81be0330a2c66c32225208a4789d1', 'services/agent_runtime/app/runtime/runtime.py': 'dfe189a4c25f0c5c48393935360956f55bfe12afe2c7d273d6d57ba330db4650', 'services/agent_runtime/tests/test_feishu_chatops_adapter.py': '856fccf71c19f78153c612e828b1ba5785a8a1b5f37c83b645505dfc206a4435'}

NEW_SOURCES = {
    "services/agent_runtime/app/conversation/feishu_channel_transport.py": 'from __future__ import annotations\n\nimport hashlib\nimport json\n\nfrom typing import Any\n\nfrom lark_channel import (\n    CardActionEvent,\n    Events,\n    InboundMessage,\n)\n\nfrom services.agent_runtime.app.conversation.chatops import (\n    ChatOpsConversationGateway,\n    ChatOpsInboundMessage,\n)\nfrom services.agent_runtime.app.conversation.feishu import (\n    FEISHU_CARD_ACTION_EVENT,\n    FEISHU_MESSAGE_EVENT,\n    FeishuChatOpsAdapter,\n    FeishuPayloadError,\n)\nfrom services.agent_runtime.app.conversation.write_bridge import (\n    ChatOpsAuthenticatedWriteBridge,\n    ChatOpsWriteStatus,\n)\n\n\nclass FeishuChannelTransportError(RuntimeError):\n    """Base fail-closed error for the official standalone Channel transport."""\n\n\nclass FeishuChannelTransportPayloadError(\n    FeishuChannelTransportError\n):\n    """An official Channel SDK event cannot enter the ChatOps core safely."""\n\n\nclass FeishuChannelTransportSendError(\n    FeishuChannelTransportError\n):\n    """A rendered ChatOps reply could not be sent safely."""\n\n\nclass FeishuOfficialChannelTransport:\n    """\n    Adapter between the standalone ``lark-channel-sdk`` and AI SRE ChatOps.\n\n    This object deliberately does NOT own credentials and does NOT connect the\n    Channel SDK. A later live-runner stage may construct ``FeishuChannel`` and\n    call its lifecycle methods explicitly.\n\n    Trust flow:\n\n        lark_channel typed event\n            -> this transport\n            -> FeishuLongConnectionTrustBoundary\n            -> FeishuChatOpsAdapter\n            -> ChatOpsConversationGateway\n               or explicit ChatOpsAuthenticatedWriteBridge\n            -> Feishu Card 2.0\n            -> injected channel.send()\n\n    v1 is single-app scoped. The standalone SDK\'s normalized handler models do\n    not expose the tenant key used by our older raw-event envelope, therefore\n    normalized live messages intentionally enter ChatOps with tenant_id=None.\n    A tenant-aware multi-app transport must be added explicitly rather than\n    guessed from unrelated identifiers.\n    """\n\n    def __init__(\n        self,\n        *,\n        channel: Any,\n        adapter: FeishuChatOpsAdapter,\n        gateway: ChatOpsConversationGateway,\n        write_bridge: (\n            ChatOpsAuthenticatedWriteBridge\n            | None\n        ) = None,\n    ) -> None:\n        if not callable(\n            getattr(\n                channel,\n                "on",\n                None,\n            )\n        ):\n            raise TypeError(\n                "Feishu Channel transport requires channel.on"\n            )\n\n        if not callable(\n            getattr(\n                channel,\n                "send",\n                None,\n            )\n        ):\n            raise TypeError(\n                "Feishu Channel transport requires channel.send"\n            )\n\n        if not isinstance(\n            adapter,\n            FeishuChatOpsAdapter,\n        ):\n            raise TypeError(\n                "Feishu Channel transport requires FeishuChatOpsAdapter"\n            )\n\n        if not isinstance(\n            gateway,\n            ChatOpsConversationGateway,\n        ):\n            raise TypeError(\n                "Feishu Channel transport requires ChatOpsConversationGateway"\n            )\n\n        if (\n            write_bridge is not None\n            and not isinstance(\n                write_bridge,\n                ChatOpsAuthenticatedWriteBridge,\n            )\n        ):\n            raise TypeError(\n                "Feishu Channel write bridge is invalid"\n            )\n\n        self.channel = channel\n        self.adapter = adapter\n        self.gateway = gateway\n        self.write_bridge = write_bridge\n        self._registered = False\n\n    @property\n    def registered(\n        self,\n    ) -> bool:\n        return self._registered\n\n    def register(\n        self,\n    ) -> "FeishuOfficialChannelTransport":\n        """\n        Register handlers only.\n\n        This method never starts WebSocket/network lifecycle and is safe to call\n        during explicit application assembly before the separate live runner\n        decides whether to connect.\n        """\n\n        if self._registered:\n            return self\n\n        self.channel.on(\n            Events.MESSAGE,\n            self.handle_message,\n        )\n        self.channel.on(\n            Events.CARD_ACTION,\n            self.handle_card_action,\n        )\n\n        self._registered = True\n        return self\n\n    def normalize_message(\n        self,\n        event: InboundMessage,\n    ) -> ChatOpsInboundMessage:\n        """\n        Convert the official SDK\'s normalized human text message back through\n        the existing Feishu Core trust boundary.\n\n        We intentionally use ``body_text`` so the bot\'s own @-mention is not\n        treated as part of the command text.\n        """\n\n        if not isinstance(\n            event,\n            InboundMessage,\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel message type is invalid"\n            )\n\n        if (\n            event.sender_type\n            != "user"\n            or event.sender_is_bot\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel ChatOps accepts human user messages only"\n            )\n\n        if event.raw_content_type != "text":\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel ChatOps v1 supports text messages only"\n            )\n\n        message_id = self._required_text(\n            event.message_id,\n            "Feishu Channel message_id",\n        )\n        chat_id = self._required_text(\n            event.chat_id,\n            "Feishu Channel chat_id",\n        )\n        actor_id = self._required_text(\n            event.sender_id,\n            "Feishu Channel sender open_id",\n        )\n\n        chat_type = self._required_text(\n            event.chat_type,\n            "Feishu Channel chat_type",\n        )\n\n        if chat_type not in {\n            "p2p",\n            "group",\n            "topic",\n        }:\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel chat_type is unsupported"\n            )\n\n        text = self._message_text(\n            event.body_text\n        )\n\n        root_id = ""\n        parent_id = ""\n\n        if chat_type in {\n            "group",\n            "topic",\n        }:\n            thread_id = getattr(\n                event.conversation,\n                "thread_id",\n                None,\n            )\n\n            if isinstance(\n                thread_id,\n                str,\n            ):\n                root_id = thread_id\n\n            parent = event.reply_to_message_id\n\n            if isinstance(\n                parent,\n                str,\n            ):\n                parent_id = parent\n\n        raw = {\n            "schema": "2.0",\n            "header": {\n                "event_id": (\n                    self._stable_id(\n                        "message",\n                        message_id,\n                        chat_id,\n                        actor_id,\n                    )\n                ),\n                "event_type": (\n                    FEISHU_MESSAGE_EVENT\n                ),\n            },\n            "event": {\n                "sender": {\n                    "sender_id": {\n                        "open_id": actor_id,\n                    },\n                    "sender_type": "user",\n                },\n                "message": {\n                    "message_id": (\n                        message_id\n                    ),\n                    "root_id": root_id,\n                    "parent_id": (\n                        parent_id\n                    ),\n                    "chat_id": chat_id,\n                    "chat_type": (\n                        "p2p"\n                        if chat_type\n                        == "p2p"\n                        else "group"\n                    ),\n                    "message_type": (\n                        "text"\n                    ),\n                    "content": (\n                        json.dumps(\n                            {\n                                "text": text,\n                            },\n                            ensure_ascii=False,\n                            separators=(\n                                ",",\n                                ":",\n                            ),\n                        )\n                    ),\n                    # body_text already removed the current bot\'s mention.\n                    "mentions": [],\n                },\n            },\n        }\n\n        try:\n            trusted = (\n                self.adapter\n                .trust_boundary\n                .accept(\n                    raw\n                )\n            )\n\n            return (\n                self.adapter\n                .normalize_inbound(\n                    trusted\n                )\n            )\n\n        except FeishuPayloadError:\n            raise\n\n        except Exception as exc:\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel message normalization failed"\n            ) from exc\n\n    def normalize_card_action(\n        self,\n        event: CardActionEvent,\n    ) -> ChatOpsInboundMessage:\n        """\n        Convert the official SDK card callback through the existing Feishu Core\n        allowlist. This transport never duplicates or relaxes action policy.\n        """\n\n        if not isinstance(\n            event,\n            CardActionEvent,\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel card action type is invalid"\n            )\n\n        message_id = self._required_text(\n            event.message_id,\n            "Feishu Channel card message_id",\n        )\n        chat_id = self._required_text(\n            event.chat_id,\n            "Feishu Channel card chat_id",\n        )\n        actor_id = self._required_text(\n            getattr(\n                event.operator,\n                "open_id",\n                None,\n            ),\n            "Feishu Channel card operator open_id",\n        )\n\n        value = getattr(\n            event.action,\n            "value",\n            None,\n        )\n\n        if not isinstance(\n            value,\n            dict,\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel card action value is invalid"\n            )\n\n        event_id = self._stable_id(\n            "card",\n            message_id,\n            chat_id,\n            actor_id,\n            self._canonical_json(\n                value\n            ),\n        )\n\n        raw = {\n            "schema": "2.0",\n            "header": {\n                "event_id": event_id,\n                "event_type": (\n                    FEISHU_CARD_ACTION_EVENT\n                ),\n            },\n            "event": {\n                "operator": {\n                    "open_id": actor_id,\n                },\n                "context": {\n                    "open_chat_id": (\n                        chat_id\n                    ),\n                    "open_message_id": (\n                        message_id\n                    ),\n                },\n                "action": {\n                    "value": dict(\n                        value\n                    ),\n                },\n            },\n        }\n\n        try:\n            trusted = (\n                self.adapter\n                .trust_boundary\n                .accept(\n                    raw\n                )\n            )\n\n            return (\n                self.adapter\n                .normalize_inbound(\n                    trusted\n                )\n            )\n\n        except FeishuPayloadError:\n            raise\n\n        except Exception as exc:\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel card normalization failed"\n            ) from exc\n\n    async def handle_message(\n        self,\n        event: InboundMessage,\n    ) -> Any:\n        inbound = self.normalize_message(\n            event\n        )\n\n        thread_id = getattr(\n            event.conversation,\n            "thread_id",\n            None,\n        )\n\n        return await self._dispatch(\n            inbound,\n            reply_to=event.message_id,\n            reply_in_thread=(\n                isinstance(\n                    thread_id,\n                    str,\n                )\n                and bool(\n                    thread_id\n                )\n            ),\n        )\n\n    async def handle_card_action(\n        self,\n        event: CardActionEvent,\n    ) -> Any:\n        inbound = (\n            self.normalize_card_action(\n                event\n            )\n        )\n\n        return await self._dispatch(\n            inbound,\n            reply_to=event.message_id,\n            reply_in_thread=False,\n        )\n\n    async def _dispatch(\n        self,\n        inbound: ChatOpsInboundMessage,\n        *,\n        reply_to: str,\n        reply_in_thread: bool,\n    ) -> Any:\n        if self.write_bridge is not None:\n            outcome = await (\n                self.write_bridge\n                .handle(\n                    inbound\n                )\n            )\n\n            if (\n                outcome.status\n                != ChatOpsWriteStatus\n                .NO_WRITE_INTENT\n            ):\n                card = (\n                    self.adapter\n                    .render_write_outcome(\n                        outcome\n                    )\n                )\n\n                return await self._send_card(\n                    inbound=inbound,\n                    card=card,\n                    reply_to=reply_to,\n                    reply_in_thread=(\n                        reply_in_thread\n                    ),\n                )\n\n        outbound = await (\n            self.gateway\n            .handle(\n                inbound\n            )\n        )\n\n        rendered = (\n            self.adapter\n            .render_outbound(\n                outbound\n            )\n        )\n\n        card = rendered.get(\n            "card"\n        )\n\n        if not isinstance(\n            card,\n            dict,\n        ):\n            raise FeishuChannelTransportSendError(\n                "Feishu Channel rendered card is invalid"\n            )\n\n        return await self._send_card(\n            inbound=inbound,\n            card=card,\n            reply_to=reply_to,\n            reply_in_thread=(\n                reply_in_thread\n            ),\n        )\n\n    async def _send_card(\n        self,\n        *,\n        inbound: ChatOpsInboundMessage,\n        card: dict[str, Any],\n        reply_to: str,\n        reply_in_thread: bool,\n    ) -> Any:\n        chat_id = (\n            inbound.conversation\n            .conversation_id\n        )\n\n        result = await self.channel.send(\n            chat_id,\n            {\n                "card": card,\n            },\n            {\n                "reply_to": reply_to,\n                "reply_in_thread": (\n                    reply_in_thread\n                ),\n                "receive_id_type": (\n                    "chat_id"\n                ),\n                "uuid": self._stable_id(\n                    "send",\n                    inbound.conversation\n                    .binding_key(),\n                    inbound.message_id,\n                    reply_to,\n                ),\n            },\n        )\n\n        success = getattr(\n            result,\n            "success",\n            None,\n        )\n\n        if success is False:\n            raise FeishuChannelTransportSendError(\n                "Feishu Channel send returned failure"\n            )\n\n        return result\n\n    @staticmethod\n    def _message_text(\n        value: Any,\n    ) -> str:\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel body_text is invalid"\n            )\n\n        normalized = " ".join(\n            value.split()\n        )\n\n        if (\n            not normalized\n            or len(\n                normalized\n            ) > 4096\n            or "\\x00"\n            in normalized\n        ):\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel body_text is invalid"\n            )\n\n        return normalized\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        label: str,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value\n            != value.strip()\n            or len(value) > 256\n            or "\\x00" in value\n        ):\n            raise FeishuChannelTransportPayloadError(\n                label\n                + " is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _canonical_json(\n        value: Any,\n    ) -> str:\n        try:\n            return json.dumps(\n                value,\n                ensure_ascii=False,\n                sort_keys=True,\n                separators=(",", ":"),\n            )\n        except (\n            TypeError,\n            ValueError,\n        ) as exc:\n            raise FeishuChannelTransportPayloadError(\n                "Feishu Channel card action value is not serializable"\n            ) from exc\n\n    @staticmethod\n    def _stable_id(\n        *parts: str,\n    ) -> str:\n        value = json.dumps(\n            list(parts),\n            ensure_ascii=False,\n            separators=(",", ":"),\n        )\n\n        return hashlib.sha256(\n            value.encode(\n                "utf-8"\n            )\n        ).hexdigest()\n\n\n__all__ = [\n    "FeishuChannelTransportError",\n    "FeishuChannelTransportPayloadError",\n    "FeishuChannelTransportSendError",\n    "FeishuOfficialChannelTransport",\n]\n',
    "services/agent_runtime/tests/test_feishu_channel_transport.py": 'from __future__ import annotations\n\nfrom importlib.metadata import version\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom lark_channel import (\n    CardActionEvent,\n    CardActionPayload,\n    Conversation,\n    EventOperator,\n    Events,\n    Identity,\n    InboundMessage,\n    ReplyRef,\n)\n\nfrom services.agent_runtime.app.conversation.chatops import (\n    ChatOpsConversationGateway,\n)\nfrom services.agent_runtime.app.conversation.feishu import (\n    FeishuActorAttestationRegistry,\n    FeishuChatOpsActorVerifier,\n    FeishuChatOpsAdapter,\n    FeishuLongConnectionTrustBoundary,\n    FeishuPayloadError,\n)\nfrom services.agent_runtime.app.conversation.feishu_channel_transport import (\n    FeishuChannelTransportPayloadError,\n    FeishuOfficialChannelTransport,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    DictConversationIncidentContextProvider,\n)\n\n\nINCIDENT_ID = (\n    "7f0d8f0a-9e8a-4b78-"\n    "9b62-486f7039e142"\n)\n\n\nclass FakeChannel:\n    def __init__(self) -> None:\n        self.handlers = {}\n        self.sent = []\n\n    def on(\n        self,\n        event,\n        handler,\n    ) -> None:\n        self.handlers[\n            event\n        ] = handler\n\n    async def send(\n        self,\n        to,\n        message,\n        opts=None,\n    ):\n        self.sent.append(\n            {\n                "to": to,\n                "message": message,\n                "opts": opts,\n            }\n        )\n\n        return SimpleNamespace(\n            success=True,\n            message_id="om-sent-1",\n        )\n\n\ndef build_components():\n    trust = (\n        FeishuLongConnectionTrustBoundary()\n    )\n    attestations = (\n        FeishuActorAttestationRegistry()\n    )\n    adapter = FeishuChatOpsAdapter(\n        trust_boundary=trust,\n        attestations=attestations,\n    )\n\n    provider = (\n        DictConversationIncidentContextProvider(\n            {\n                INCIDENT_ID: (\n                    ConversationIncidentContext(\n                        incident_id=(\n                            INCIDENT_ID\n                        ),\n                        status=(\n                            "waiting_approval"\n                        ),\n                        title=(\n                            "payment-api PodOOMKilled"\n                        ),\n                        root_cause=(\n                            "Memory limit regression"\n                        ),\n                        root_cause_confidence=(\n                            0.94\n                        ),\n                    )\n                )\n            }\n        )\n    )\n\n    orchestrator = (\n        ConversationOrchestrator(\n            provider=provider\n        )\n    )\n\n    gateway = (\n        ChatOpsConversationGateway(\n            orchestrator=orchestrator\n        )\n    )\n\n    channel = FakeChannel()\n\n    transport = (\n        FeishuOfficialChannelTransport(\n            channel=channel,\n            adapter=adapter,\n            gateway=gateway,\n        )\n    )\n\n    verifier = (\n        FeishuChatOpsActorVerifier(\n            attestations\n        )\n    )\n\n    return (\n        transport,\n        channel,\n        verifier,\n    )\n\n\ndef sdk_message(\n    *,\n    text="根因是什么？",\n    chat_type="group",\n    thread_id="omt-thread-1",\n    reply_to=None,\n    sender_type="user",\n    is_bot=False,\n    raw_content_type="text",\n):\n    reply = (\n        ReplyRef(\n            message_id=reply_to\n        )\n        if reply_to\n        else None\n    )\n\n    return InboundMessage(\n        id="om-message-1",\n        create_time=1786470000,\n        conversation=(\n            Conversation(\n                chat_id=(\n                    "oc-sre-group"\n                ),\n                chat_type=chat_type,\n                thread_id=thread_id,\n            )\n        ),\n        sender=(\n            Identity(\n                open_id="ou-operator-1",\n                is_bot=is_bot,\n                sender_type=(\n                    sender_type\n                ),\n            )\n        ),\n        reply=reply,\n        content_text=text,\n        raw_content_type=(\n            raw_content_type\n        ),\n        body_text=text,\n    )\n\n\ndef sdk_card_action(\n    *,\n    action_name="show_status",\n    incident_id=INCIDENT_ID,\n):\n    value = {\n        "ai_sre_action": action_name,\n    }\n\n    if incident_id is not None:\n        value[\n            "incident_id"\n        ] = incident_id\n\n    return CardActionEvent(\n        message_id=(\n            "om-card-1"\n        ),\n        chat_id="oc-sre-group",\n        operator=(\n            EventOperator(\n                open_id=(\n                    "ou-operator-1"\n                )\n            )\n        ),\n        action=(\n            CardActionPayload(\n                value=value\n            )\n        ),\n    )\n\n\ndef test_installed_official_channel_sdk_contract():\n    assert (\n        version(\n            "lark-channel-sdk"\n        )\n        == "1.2.0"\n    )\n\n    assert Events.MESSAGE == "message"\n    assert (\n        Events.CARD_ACTION\n        == "cardAction"\n    )\n\n\ndef test_register_only_installs_handlers_and_is_idempotent():\n    transport, channel, _ = (\n        build_components()\n    )\n\n    returned = transport.register()\n\n    assert returned is transport\n    assert transport.registered is True\n    assert set(\n        channel.handlers\n    ) == {\n        Events.MESSAGE,\n        Events.CARD_ACTION,\n    }\n\n    message_handler = (\n        channel.handlers[\n            Events.MESSAGE\n        ]\n    )\n    card_handler = (\n        channel.handlers[\n            Events.CARD_ACTION\n        ]\n    )\n\n    transport.register()\n\n    assert (\n        channel.handlers[\n            Events.MESSAGE\n        ]\n        is message_handler\n    )\n    assert (\n        channel.handlers[\n            Events.CARD_ACTION\n        ]\n        is card_handler\n    )\n\n\n@pytest.mark.asyncio\nasync def test_sdk_message_crosses_existing_trust_boundary_and_attests_actor():\n    transport, _, verifier = (\n        build_components()\n    )\n\n    inbound = (\n        transport.normalize_message(\n            sdk_message()\n        )\n    )\n\n    assert (\n        inbound.conversation.channel\n        == "feishu"\n    )\n    assert (\n        inbound.conversation.tenant_id\n        is None\n    )\n    assert (\n        inbound.conversation.conversation_id\n        == "oc-sre-group"\n    )\n    assert (\n        inbound.conversation.thread_id\n        == "omt-thread-1"\n    )\n    assert (\n        inbound.external_actor_id\n        == "ou-operator-1"\n    )\n    assert inbound.text == "根因是什么？"\n\n    actor = await verifier.verify(\n        inbound\n    )\n\n    assert actor.channel == "feishu"\n    assert (\n        actor.external_actor_id\n        == "ou-operator-1"\n    )\n    assert (\n        actor.verification_method\n        == "feishu_official_sdk_long_connection"\n    )\n\n\ndef test_sdk_topic_maps_to_existing_group_thread_contract():\n    transport, _, _ = (\n        build_components()\n    )\n\n    inbound = (\n        transport.normalize_message(\n            sdk_message(\n                chat_type="topic",\n                thread_id=(\n                    "omt-topic-1"\n                ),\n            )\n        )\n    )\n\n    assert (\n        inbound.conversation.thread_id\n        == "omt-topic-1"\n    )\n\n\n@pytest.mark.parametrize(\n    "event",\n    [\n        sdk_message(\n            sender_type="bot",\n            is_bot=True,\n        ),\n        sdk_message(\n            sender_type="system",\n        ),\n        sdk_message(\n            raw_content_type="image",\n        ),\n        sdk_message(\n            text="   ",\n        ),\n    ],\n)\ndef test_sdk_message_fails_closed_for_nonhuman_or_unsupported_input(\n    event,\n):\n    transport, _, _ = (\n        build_components()\n    )\n\n    with pytest.raises(\n        FeishuChannelTransportPayloadError\n    ):\n        transport.normalize_message(\n            event\n        )\n\n\ndef test_sdk_card_action_reuses_existing_core_allowlist():\n    transport, _, _ = (\n        build_components()\n    )\n\n    inbound = (\n        transport.normalize_card_action(\n            sdk_card_action()\n        )\n    )\n\n    assert inbound.text == (\n        "现在状态怎么样？"\n    )\n    assert inbound.incident_id == (\n        INCIDENT_ID\n    )\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        transport.normalize_card_action(\n            sdk_card_action(\n                action_name=(\n                    "arbitrary.shell"\n                )\n            )\n        )\n\n\ndef test_sdk_write_card_requires_incident_through_existing_core():\n    transport, _, _ = (\n        build_components()\n    )\n\n    with pytest.raises(\n        FeishuPayloadError\n    ):\n        transport.normalize_card_action(\n            sdk_card_action(\n                action_name=(\n                    "approval.approve"\n                ),\n                incident_id=None,\n            )\n        )\n\n\n@pytest.mark.asyncio\nasync def test_read_only_message_dispatch_renders_and_sends_card_v2():\n    transport, channel, _ = (\n        build_components()\n    )\n\n    message = sdk_message(\n        text=(\n            "incident_id: "\n            + INCIDENT_ID\n            + " 根因是什么？"\n        ),\n    )\n\n    await transport.handle_message(\n        message\n    )\n\n    assert len(\n        channel.sent\n    ) == 1\n\n    sent = channel.sent[\n        0\n    ]\n\n    assert sent["to"] == (\n        "oc-sre-group"\n    )\n    assert (\n        sent["message"][\n            "card"\n        ][\n            "schema"\n        ]\n        == "2.0"\n    )\n    assert (\n        sent["opts"][\n            "reply_to"\n        ]\n        == "om-message-1"\n    )\n    assert (\n        sent["opts"][\n            "reply_in_thread"\n        ]\n        is True\n    )\n    assert (\n        sent["opts"][\n            "receive_id_type"\n        ]\n        == "chat_id"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_read_only_card_action_dispatch_uses_same_gateway():\n    transport, channel, _ = (\n        build_components()\n    )\n\n    await (\n        transport.handle_card_action(\n            sdk_card_action(\n                action_name=(\n                    "show_status"\n                )\n            )\n        )\n    )\n\n    assert len(\n        channel.sent\n    ) == 1\n\n    assert (\n        channel.sent[\n            0\n        ][\n            "message"\n        ][\n            "card"\n        ][\n            "schema"\n        ]\n        == "2.0"\n    )\n\n\ndef test_transport_source_has_no_live_credentials_or_auto_connect():\n    from pathlib import Path\n\n    import services.agent_runtime.app.conversation.feishu_channel_transport as module\n\n    source = Path(\n        module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    required = [\n        "from lark_channel import",\n        "Events.MESSAGE",\n        "Events.CARD_ACTION",\n        "FeishuChatOpsAdapter",\n        "ChatOpsConversationGateway",\n        "ChatOpsAuthenticatedWriteBridge",\n    ]\n\n    assert [\n        item\n        for item in required\n        if item not in source\n    ] == []\n\n    forbidden = [\n        "lark_oapi",\n        "os.environ",\n        "LARK_APP_ID",\n        "LARK_APP_SECRET",\n        "FEISHU_APP_ID",\n        "FEISHU_APP_SECRET",\n        ".connect(",\n        "FeishuChannel(",\n        "ApprovalService",\n        "ActionRuntime",\n        "KubernetesProductionExecutor",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n',
}

FEISHU_DOC_OLD = """    Explicit protocol boundary for the future official lark-oapi transport.

    v1 does not open a network connection. The future transport handler must
    call accept() before a callback may enter FeishuChatOpsAdapter.
"""

FEISHU_DOC_NEW = """    Explicit protocol boundary for the official Feishu Channel transport.

    The Feishu Core itself does not open a network connection. An external
    transport must call accept() before a callback may enter
    FeishuChatOpsAdapter.
"""


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
        "Repository root not found. Run this installer inside ai-reliability-platform."
    )


def normalize(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def read_text(path: Path) -> str:
    return normalize(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        value,
        encoding="utf-8",
        newline="\n",
    )


def section(
    report: list[str],
    title: str,
) -> None:
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
        stdout=normalize(
            process.stdout
        ),
        stderr=normalize(
            process.stderr
        ),
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
            " ".join(
                result.command
            ),
            "",
            "ExitCode: "
            + str(
                result.returncode
            ),
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_hashes(
    root: Path,
    report: list[str],
) -> None:
    for relative, expected in (
        EXPECTED_RAW_HASHES.items()
    ):
        path = root / relative

        if not path.exists():
            raise RuntimeError(
                "Required current file is missing: "
                + relative
            )

        actual = raw_sha256(
            path
        )

        report.append(
            relative
            + "="
            + actual
        )

        if actual != expected:
            raise RuntimeError(
                relative
                + " changed after the reviewed Transport snapshot. "
                + "expected_raw_sha256="
                + expected
                + " actual_raw_sha256="
                + actual
                + ". Refusing stale installation; capture current code again."
            )


def restore_bytes(
    backups: dict[Path, bytes],
) -> None:
    for path, raw in backups.items():
        path.write_bytes(
            raw
        )


def remove_created(
    created: list[Path],
) -> None:
    for path in reversed(
        created
    ):
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

    mutable_existing = (
        root / "pyproject.toml",
        root / "uv.lock",
        root / (
            "services/agent_runtime/"
            "app/conversation/feishu.py"
        ),
    )

    backups = {
        path: path.read_bytes()
        for path
        in mutable_existing
    }

    targets = {
        root / relative: source
        for relative, source
        in NEW_SOURCES.items()
    }

    created: list[Path] = []

    report = [
        "Feishu Official Channel Transport v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Reviewed baseline:",
        "- Feishu ChatOps Adapter Core v1 fix1 is installed and full-suite green.",
        "- standalone lark_channel is absent before this installer.",
        "- legacy lark_oapi Channel is not used.",
        "",
        "This stage installs:",
        "- pinned lark-channel-sdk==1.2.0 dependency",
        "- typed InboundMessage / CardActionEvent transport bridge",
        "- Events.MESSAGE / Events.CARD_ACTION handler registration",
        "- existing FeishuLongConnectionTrustBoundary reuse",
        "- existing FeishuChatOpsAdapter reuse",
        "- read-only Conversation Gateway dispatch",
        "- optional existing ChatOpsAuthenticatedWriteBridge dispatch",
        "- Card 2.0 send through an injected Channel object",
        "",
        "This stage intentionally does NOT:",
        "- construct FeishuChannel with credentials",
        "- read App ID or App Secret",
        "- call channel.connect()",
        "- auto-start any Runtime transport",
        "- weaken the existing Feishu card-action allowlist",
        "- grant direct ApprovalService / ActionRuntime authority",
        "",
        "Transport tenancy:",
        "- v1 is explicitly single-app scoped",
        "- normalized standalone SDK handler models enter ChatOps with tenant_id=None",
        "- multi-app tenant-aware routing remains a later explicit extension",
        "",
        "Rollback:",
        "- pyproject.toml, uv.lock and feishu.py are restored byte-for-byte on failure",
        "- newly created transport/test files are removed on failure",
        "- uv sync is attempted after rollback to restore the environment",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        verify_hashes(
            root,
            report,
        )

        section(
            report,
            "NEW TARGET PREFLIGHT",
        )

        for path in targets:
            relative = str(
                path.relative_to(
                    root
                )
            ).replace(
                "\\",
                "/",
            )

            if path.exists():
                raise RuntimeError(
                    "Transport v1 target already exists; refusing overwrite: "
                    + relative
                )

            report.append(
                "new_target="
                + relative
            )

        feishu_path = (
            root
            / "services/agent_runtime/app/conversation/feishu.py"
        )

        feishu_text = read_text(
            feishu_path
        )

        if feishu_text.count(
            FEISHU_DOC_OLD
        ) != 1:
            raise RuntimeError(
                "Feishu trust-boundary documentation block changed unexpectedly"
            )

        feishu_text = (
            feishu_text.replace(
                FEISHU_DOC_OLD,
                FEISHU_DOC_NEW,
                1,
            )
        )

        dependency = run_command(
            root=root,
            name="Install pinned standalone Feishu Channel SDK",
            command=[
                "uv",
                "add",
                SDK_REQUIREMENT,
            ],
        )
        add_command(
            report,
            dependency,
        )

        if dependency.returncode != 0:
            raise RuntimeError(
                "lark-channel-sdk dependency installation failed"
            )

        sdk_contract = run_command(
            root=root,
            name="Standalone SDK import/version contract",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from importlib.metadata import version; "
                    "from lark_channel import "
                    "CardActionEvent, Events, InboundMessage; "
                    "v=version('lark-channel-sdk'); "
                    "print('version='+v); "
                    "print('message_event='+Events.MESSAGE); "
                    "print('card_event='+Events.CARD_ACTION); "
                    "raise SystemExit(0 if "
                    "v=='1.2.0' and "
                    "Events.MESSAGE=='message' and "
                    "Events.CARD_ACTION=='cardAction' "
                    "else 1)"
                ),
            ],
        )
        add_command(
            report,
            sdk_contract,
        )

        if sdk_contract.returncode != 0:
            raise RuntimeError(
                "Standalone SDK contract check failed"
            )

        write_text(
            feishu_path,
            feishu_text,
        )

        for path, source in (
            targets.items()
        ):
            write_text(
                path,
                source,
            )
            created.append(
                path
            )

        syntax = run_command(
            root=root,
            name="Feishu Channel Transport Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(
                    feishu_path.relative_to(
                        root
                    )
                ),
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path
                    in targets
                ],
            ],
        )
        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Feishu Channel Transport syntax failed"
            )

        focused = run_command(
            root=root,
            name="Feishu Channel Transport focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_channel_transport.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_chatops_adapter.py"
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
                "Feishu Channel Transport focused tests failed"
            )

        write_compat = run_command(
            root=root,
            name="Authenticated ChatOps write compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_chatops_authenticated_write_bridge.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_security_authentication_service.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_security_policy.py"
                ),
                "-q",
            ],
        )
        add_command(
            report,
            write_compat,
        )

        if write_compat.returncode != 0:
            raise RuntimeError(
                "Authenticated ChatOps write compatibility failed"
            )

        architecture = run_command(
            root=root,
            name="Transport no-credential/no-auto-connect boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/"
                    "feishu_channel_transport.py').read_text(encoding='utf-8'); "
                    "required=['from lark_channel import','Events.MESSAGE',"
                    "'Events.CARD_ACTION','FeishuChatOpsAdapter',"
                    "'ChatOpsConversationGateway',"
                    "'ChatOpsAuthenticatedWriteBridge']; "
                    "forbidden=['lark_oapi','os.environ','LARK_APP_ID',"
                    "'LARK_APP_SECRET','FEISHU_APP_ID','FEISHU_APP_SECRET',"
                    "'.connect(','FeishuChannel(','ApprovalService',"
                    "'ActionRuntime','KubernetesProductionExecutor']; "
                    "missing=[x for x in required if x not in p]; "
                    "bad=[x for x in forbidden if x in p]; "
                    "print('missing='+str(missing)); "
                    "print('forbidden='+str(bad)); "
                    "raise SystemExit(1 if missing or bad else 0)"
                ),
            ],
        )
        add_command(
            report,
            architecture,
        )

        if architecture.returncode != 0:
            raise RuntimeError(
                "Transport architecture boundary failed"
            )

        lock_check = run_command(
            root=root,
            name="uv lock consistency",
            command=[
                "uv",
                "lock",
                "--check",
            ],
        )
        add_command(
            report,
            lock_check,
        )

        if lock_check.returncode != 0:
            raise RuntimeError(
                "uv lock consistency failed"
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

        status_paths = [
            "pyproject.toml",
            "uv.lock",
            (
                "services/agent_runtime/app/"
                "conversation/feishu.py"
            ),
            *NEW_SOURCES.keys(),
        ]

        status = run_command(
            root=root,
            name="Git status for Feishu Channel Transport v1",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *status_paths,
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
                "Feishu Official Channel Transport v1 is installed.",
                "",
                "Installed dependency:",
                "- lark-channel-sdk==1.2.0",
                "",
                "Verified transport chain:",
                "1. official SDK typed events only",
                "2. human text / card callback validation",
                "3. existing Feishu trust boundary",
                "4. existing exact-message actor attestation",
                "5. existing Feishu adapter allowlist",
                "6. Conversation Gateway by default",
                "7. optional authenticated write bridge only when explicitly injected",
                "8. Card 2.0 send through injected Channel object",
                "",
                "Still NOT installed:",
                "- App ID/App Secret loader",
                "- actual FeishuChannel construction",
                "- SecurityConfig strict live policy",
                "- group allowlist configuration",
                "- channel.connect() runner",
                "- automatic Runtime startup wiring",
                "",
                "Next stage after review:",
                "- Feishu Live Channel Runner + Runtime Assembly v1",
                "",
                "Upload only: "
                + AFTER_NAME,
            ]
        )

        after.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU OFFICIAL CHANNEL TRANSPORT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "No live Feishu connection was opened."
        )
        print(
            "No App ID or App Secret was read."
        )
        print()
        print(
            "Upload only:"
        )
        print(after)

        return 0

    except Exception as exc:
        remove_created(
            created
        )
        restore_bytes(
            backups
        )

        rollback_sync = run_command(
            root=root,
            name="Rollback uv environment sync",
            command=[
                "uv",
                "sync",
            ],
        )

        report.extend(
            [
                "",
                "=" * 120,
                "ROLLBACK",
                "=" * 120,
                "",
                "Existing mutable files restored byte-for-byte.",
                "New Transport v1 files removed.",
                "",
                "Rollback uv sync exit code: "
                + str(
                    rollback_sync.returncode
                ),
                "Rollback uv sync stdout:",
                rollback_sync.stdout.rstrip()
                or "<EMPTY>",
                "Rollback uv sync stderr:",
                rollback_sync.stderr.rstrip()
                or "<EMPTY>",
            ]
        )

        error.write_text(
            "\n".join(
                [
                    "Feishu Official Channel Transport v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    ),
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only: "
                    + ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU OFFICIAL CHANNEL TRANSPORT V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "Existing files were restored and new Transport files removed."
        )
        print()
        print(
            "Upload only:"
        )
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
