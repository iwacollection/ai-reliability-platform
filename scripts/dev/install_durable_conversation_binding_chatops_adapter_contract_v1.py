from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = (
    "durable-conversation-binding-chatops-adapter-contract-v1"
)

AFTER_NAME = (
    "durable_conversation_binding_chatops_adapter_contract_v1_after.txt"
)

ERROR_NAME = (
    "durable_conversation_binding_chatops_adapter_contract_v1_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/runtime/runtime.py': '63f51066f2f8a27fc12343249e13339b0bef03bc311f9107feb2fd5c934c1906', 'services/agent_runtime/app/conversation/store.py': '3ad9b5a6adbf78f16bc525f40bba1bcc01205560cfb1249517b68fb7ce917fb7', 'services/agent_runtime/app/conversation/orchestrator.py': '6bd7170edde1144d82ab37fd86018fde7a51fcfb3c7116f87c86eeb5582ff082', 'services/agent_runtime/app/conversation/__init__.py': '6d8afeaeab3cdf82c96a5030a94da70fb6ffc5eb123a8c468d1a5eb3dc6a428a'}
SOURCES = {'services/agent_runtime/app/conversation/store.py': 'from __future__ import annotations\n\nimport asyncio\nimport sqlite3\n\nfrom datetime import UTC, datetime\nfrom pathlib import Path\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIntent,\n    ConversationSession,\n)\n\n\nclass InMemoryConversationSessionStore:\n    """\n    Bounded process-local conversation routing state.\n\n    Incident/Evidence/RCA data remains in existing authoritative persistence.\n    """\n\n    def __init__(self, *, max_sessions: int = 10000) -> None:\n        if (\n            not isinstance(max_sessions, int)\n            or isinstance(max_sessions, bool)\n            or max_sessions <= 0\n            or max_sessions > 100000\n        ):\n            raise ValueError(\n                "Conversation max_sessions is invalid"\n            )\n\n        self.max_sessions = max_sessions\n        self._items: dict[str, ConversationSession] = {}\n        self._lock = asyncio.Lock()\n\n    async def get(\n        self,\n        conversation_id: str,\n    ) -> ConversationSession | None:\n        async with self._lock:\n            return self._items.get(conversation_id)\n\n    async def update(\n        self,\n        *,\n        conversation_id: str,\n        incident_id: str | None,\n        intent: ConversationIntent,\n    ) -> ConversationSession:\n        async with self._lock:\n            current = self._items.get(conversation_id)\n            now = datetime.now(UTC)\n\n            if current is None:\n                if len(self._items) >= self.max_sessions:\n                    oldest_key = min(\n                        self._items,\n                        key=lambda key: self._items[key].updated_at,\n                    )\n                    self._items.pop(oldest_key, None)\n\n                value = ConversationSession(\n                    conversation_id=conversation_id,\n                    incident_id=incident_id,\n                    last_intent=intent,\n                    turn_count=1,\n                    created_at=now,\n                    updated_at=now,\n                )\n            else:\n                value = current.model_copy(\n                    update={\n                        "incident_id": (\n                            incident_id\n                            if incident_id is not None\n                            else current.incident_id\n                        ),\n                        "last_intent": intent,\n                        "turn_count": current.turn_count + 1,\n                        "updated_at": now,\n                    }\n                )\n\n            self._items[conversation_id] = value\n            return value\n\n\n\nclass SQLiteConversationSessionStore:\n    """\n    Durable ChatOps conversation -> Incident binding.\n\n    Only routing/session state is stored here:\n    - opaque conversation binding key,\n    - currently-bound incident_id,\n    - last deterministic intent,\n    - turn count and timestamps.\n\n    Incident/RCA/Approval/Action/Verification facts remain in their existing\n    authoritative stores.\n    """\n\n    def __init__(\n        self,\n        db_path: str | Path | None = None,\n        *,\n        max_sessions: int = 10000,\n    ) -> None:\n        if (\n            not isinstance(\n                max_sessions,\n                int,\n            )\n            or isinstance(\n                max_sessions,\n                bool,\n            )\n            or max_sessions <= 0\n            or max_sessions > 100000\n        ):\n            raise ValueError(\n                "Conversation max_sessions is invalid"\n            )\n\n        self.db_path = Path(\n            db_path\n            or (\n                Path("data")\n                / "conversation_sessions.db"\n            )\n        )\n\n        self.max_sessions = (\n            max_sessions\n        )\n\n        self.db_path.parent.mkdir(\n            parents=True,\n            exist_ok=True,\n        )\n\n        self._init_db()\n\n    def _connect(\n        self,\n    ) -> sqlite3.Connection:\n        connection = sqlite3.connect(\n            self.db_path,\n            timeout=10.0,\n        )\n\n        connection.execute(\n            "PRAGMA busy_timeout = 10000"\n        )\n\n        return connection\n\n    def _init_db(\n        self,\n    ) -> None:\n        with self._connect() as connection:\n            connection.execute(\n                "PRAGMA journal_mode = WAL"\n            )\n\n            connection.execute(\n                "PRAGMA synchronous = FULL"\n            )\n\n            connection.execute(\n                """\n                CREATE TABLE IF NOT EXISTS conversation_sessions\n                (\n                    conversation_id TEXT PRIMARY KEY,\n                    session_data TEXT NOT NULL,\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n                )\n                """\n            )\n\n            connection.execute(\n                """\n                CREATE INDEX IF NOT EXISTS\n                idx_conversation_sessions_updated_at\n                ON conversation_sessions(updated_at)\n                """\n            )\n\n    async def get(\n        self,\n        conversation_id: str,\n    ) -> ConversationSession | None:\n        normalized_id = (\n            self._conversation_id(\n                conversation_id\n            )\n        )\n\n        return await asyncio.to_thread(\n            self._get_sync,\n            normalized_id,\n        )\n\n    def _get_sync(\n        self,\n        conversation_id: str,\n    ) -> ConversationSession | None:\n        with self._connect() as connection:\n            row = connection.execute(\n                """\n                SELECT session_data\n\n                FROM conversation_sessions\n\n                WHERE conversation_id = ?\n                """,\n                (\n                    conversation_id,\n                ),\n            ).fetchone()\n\n        if row is None:\n            return None\n\n        session = (\n            ConversationSession\n            .model_validate_json(\n                row[\n                    0\n                ]\n            )\n        )\n\n        if (\n            session.conversation_id\n            != conversation_id\n        ):\n            raise ValueError(\n                "Conversation session identity mismatch"\n            )\n\n        return session\n\n    async def update(\n        self,\n        *,\n        conversation_id: str,\n        incident_id: str | None,\n        intent: ConversationIntent,\n    ) -> ConversationSession:\n        normalized_id = (\n            self._conversation_id(\n                conversation_id\n            )\n        )\n\n        normalized_incident_id = (\n            self._incident_id(\n                incident_id\n            )\n        )\n\n        if not isinstance(\n            intent,\n            ConversationIntent,\n        ):\n            raise TypeError(\n                "Conversation intent is invalid"\n            )\n\n        return await asyncio.to_thread(\n            self._update_sync,\n            normalized_id,\n            normalized_incident_id,\n            intent,\n        )\n\n    def _update_sync(\n        self,\n        conversation_id: str,\n        incident_id: str | None,\n        intent: ConversationIntent,\n    ) -> ConversationSession:\n        connection = self._connect()\n\n        try:\n            connection.execute(\n                "BEGIN IMMEDIATE"\n            )\n\n            row = connection.execute(\n                """\n                SELECT session_data\n\n                FROM conversation_sessions\n\n                WHERE conversation_id = ?\n                """,\n                (\n                    conversation_id,\n                ),\n            ).fetchone()\n\n            now = datetime.now(\n                UTC\n            )\n\n            if row is None:\n                value = ConversationSession(\n                    conversation_id=(\n                        conversation_id\n                    ),\n                    incident_id=(\n                        incident_id\n                    ),\n                    last_intent=intent,\n                    turn_count=1,\n                    created_at=now,\n                    updated_at=now,\n                )\n\n                connection.execute(\n                    """\n                    INSERT INTO conversation_sessions\n                    (\n                        conversation_id,\n                        session_data,\n                        created_at,\n                        updated_at\n                    )\n\n                    VALUES\n                    (\n                        ?,\n                        ?,\n                        ?,\n                        ?\n                    )\n                    """,\n                    (\n                        conversation_id,\n                        value.model_dump_json(),\n                        value.created_at.isoformat(),\n                        value.updated_at.isoformat(),\n                    ),\n                )\n\n            else:\n                current = (\n                    ConversationSession\n                    .model_validate_json(\n                        row[\n                            0\n                        ]\n                    )\n                )\n\n                if (\n                    current.conversation_id\n                    != conversation_id\n                ):\n                    raise ValueError(\n                        "Conversation session identity mismatch"\n                    )\n\n                value = current.model_copy(\n                    update={\n                        "incident_id": (\n                            incident_id\n                            if incident_id\n                            is not None\n                            else current.incident_id\n                        ),\n                        "last_intent": intent,\n                        "turn_count": (\n                            current.turn_count\n                            + 1\n                        ),\n                        "updated_at": now,\n                    }\n                )\n\n                connection.execute(\n                    """\n                    UPDATE conversation_sessions\n\n                    SET\n                        session_data = ?,\n                        updated_at = ?\n\n                    WHERE conversation_id = ?\n                    """,\n                    (\n                        value.model_dump_json(),\n                        value.updated_at.isoformat(),\n                        conversation_id,\n                    ),\n                )\n\n            self._prune_sync(\n                connection\n            )\n\n            connection.commit()\n\n            return value\n\n        except Exception:\n            connection.rollback()\n            raise\n\n        finally:\n            connection.close()\n\n    def _prune_sync(\n        self,\n        connection: sqlite3.Connection,\n    ) -> None:\n        row = connection.execute(\n            """\n            SELECT COUNT(*)\n\n            FROM conversation_sessions\n            """\n        ).fetchone()\n\n        count = int(\n            row[\n                0\n            ]\n        )\n\n        overflow = (\n            count\n            - self.max_sessions\n        )\n\n        if overflow <= 0:\n            return\n\n        connection.execute(\n            """\n            DELETE FROM conversation_sessions\n\n            WHERE conversation_id IN\n            (\n                SELECT conversation_id\n\n                FROM conversation_sessions\n\n                ORDER BY\n                    updated_at ASC,\n                    conversation_id ASC\n\n                LIMIT ?\n            )\n            """,\n            (\n                overflow,\n            ),\n        )\n\n    @staticmethod\n    def _conversation_id(\n        value: str,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n            or len(\n                value\n            )\n            > 256\n            or "\\x00" in value\n        ):\n            raise ValueError(\n                "conversation_id is invalid"\n            )\n\n        return value\n\n    @staticmethod\n    def _incident_id(\n        value: str | None,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n            or len(\n                value\n            )\n            > 256\n            or "\\x00" in value\n        ):\n            raise ValueError(\n                "incident_id is invalid"\n            )\n\n        return value\n\n\n__all__ = [\n    "InMemoryConversationSessionStore",\n    "SQLiteConversationSessionStore",\n]\n', 'services/agent_runtime/app/conversation/chatops.py': 'from __future__ import annotations\n\nimport hashlib\nimport json\n\nfrom abc import ABC, abstractmethod\nfrom typing import Annotated, Any\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    StringConstraints,\n)\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationReplyPlan,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\n\n\nShortText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=256,\n    ),\n]\n\nMessageText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=4096,\n    ),\n]\n\n\nclass ChatOpsConversationRef(BaseModel):\n    """\n    External channel/thread identity.\n\n    The opaque binding_key is what ConversationSessionStore persists. Raw\n    workspace/conversation/thread IDs do not become the SQLite primary key.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    channel: ShortText\n    tenant_id: ShortText | None = None\n    conversation_id: ShortText\n    thread_id: ShortText | None = None\n\n    def binding_key(\n        self,\n    ) -> str:\n        payload = json.dumps(\n            [\n                self.channel,\n                self.tenant_id or "",\n                self.conversation_id,\n                self.thread_id or "",\n            ],\n            ensure_ascii=False,\n            separators=(\n                ",",\n                ":",\n            ),\n        )\n\n        digest = hashlib.sha256(\n            payload.encode(\n                "utf-8"\n            )\n        ).hexdigest()\n\n        return (\n            "chatops:"\n            + digest\n        )\n\n\nclass ChatOpsInboundMessage(BaseModel):\n    """\n    Channel-neutral inbound message.\n\n    external_actor_id is an untrusted external reference only. It does not\n    confer Runtime authentication/RBAC identity or write authority.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    conversation: ChatOpsConversationRef\n    message_id: ShortText\n    external_actor_id: ShortText | None = None\n    text: MessageText\n    incident_id: ShortText | None = None\n\n\nclass ChatOpsOutboundMessage(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    conversation: ChatOpsConversationRef\n    reply_to_message_id: ShortText\n    reply: ConversationReplyPlan\n\n\nclass BaseChatOpsChannelAdapter(ABC):\n    """\n    Pure transformation contract for one channel implementation.\n\n    v1 intentionally has no send(), HTTP client, SDK client, webhook listener,\n    authentication mutation, Approval or Action method.\n    """\n\n    @abstractmethod\n    def normalize_inbound(\n        self,\n        payload: Any,\n    ) -> ChatOpsInboundMessage:\n        raise NotImplementedError\n\n    @abstractmethod\n    def render_outbound(\n        self,\n        message: ChatOpsOutboundMessage,\n    ) -> Any:\n        raise NotImplementedError\n\n\nclass ChatOpsConversationGateway:\n    """\n    Thin bridge from a normalized channel message to ConversationOrchestrator.\n\n    It performs no network I/O and no write action. A write-capable user intent\n    still returns ConversationReplyMode.WRITE_ACTION_REQUIRED from the existing\n    Orchestrator.\n    """\n\n    def __init__(\n        self,\n        *,\n        orchestrator: ConversationOrchestrator,\n    ) -> None:\n        if not isinstance(\n            orchestrator,\n            ConversationOrchestrator,\n        ):\n            raise TypeError(\n                "ChatOps Conversation Orchestrator is invalid"\n            )\n\n        self.orchestrator = (\n            orchestrator\n        )\n\n    async def handle(\n        self,\n        message: ChatOpsInboundMessage,\n    ) -> ChatOpsOutboundMessage:\n        if not isinstance(\n            message,\n            ChatOpsInboundMessage,\n        ):\n            raise TypeError(\n                "ChatOps inbound message is invalid"\n            )\n\n        reply = await self.orchestrator.handle(\n            ConversationTurnRequest(\n                conversation_id=(\n                    message.conversation\n                    .binding_key()\n                ),\n                incident_id=(\n                    message.incident_id\n                ),\n                text=message.text,\n            )\n        )\n\n        return ChatOpsOutboundMessage(\n            conversation=(\n                message.conversation\n            ),\n            reply_to_message_id=(\n                message.message_id\n            ),\n            reply=reply,\n        )\n\n\n__all__ = [\n    "BaseChatOpsChannelAdapter",\n    "ChatOpsConversationGateway",\n    "ChatOpsConversationRef",\n    "ChatOpsInboundMessage",\n    "ChatOpsOutboundMessage",\n]\n', 'services/agent_runtime/app/conversation/orchestrator.py': 'from __future__ import annotations\n\nfrom services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n    SQLiteConversationSessionStore,\n)\n\n\nclass ConversationOrchestrator:\n    """\n    Channel-neutral ChatOps core.\n\n    v1 binds a conversation to an Incident, classifies intent, reads a stable\n    Incident projection, and returns a structured reply plan.\n\n    It has no direct Action/Approval/Verification write authority.\n    """\n\n    _WRITE_INTENTS = {\n        ConversationIntent.APPROVE,\n        ConversationIntent.REJECT,\n        ConversationIntent.REMEDIATE,\n    }\n\n    def __init__(\n        self,\n        *,\n        provider: BaseConversationIncidentContextProvider,\n        sessions: (\n            InMemoryConversationSessionStore\n            | SQLiteConversationSessionStore\n            | None\n        ) = None,\n        classifier: (\n            DeterministicConversationIntentClassifier\n            | None\n        ) = None,\n    ) -> None:\n        if not isinstance(\n            provider,\n            BaseConversationIncidentContextProvider,\n        ):\n            raise TypeError(\n                "Conversation context provider is invalid"\n            )\n\n        self.provider = provider\n        self.sessions = (\n            sessions\n            or InMemoryConversationSessionStore()\n        )\n        self.classifier = (\n            classifier\n            or DeterministicConversationIntentClassifier()\n        )\n\n    async def handle(\n        self,\n        request: ConversationTurnRequest,\n    ) -> ConversationReplyPlan:\n        if not isinstance(\n            request,\n            ConversationTurnRequest,\n        ):\n            raise TypeError(\n                "Conversation request is invalid"\n            )\n\n        intent = self.classifier.classify(\n            request.text\n        )\n\n        current = await self.sessions.get(\n            request.conversation_id\n        )\n\n        incident_id = (\n            request.incident_id\n            or (\n                current.incident_id\n                if current is not None\n                else None\n            )\n        )\n\n        await self.sessions.update(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=intent,\n        )\n\n        if intent == ConversationIntent.HELP:\n            return self._help(\n                request,\n                incident_id,\n            )\n\n        if incident_id is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=None,\n                intent=intent,\n                mode=ConversationReplyMode.NEEDS_INCIDENT,\n                sections=(\n                    ConversationReplySection(\n                        key="incident_binding",\n                        title="需要 Incident",\n                        lines=(\n                            "请先绑定一个 Incident，再继续查询或操作。",\n                        ),\n                    ),\n                ),\n                suggested_actions=(\n                    "bind_incident",\n                    "help",\n                ),\n            )\n\n        context = await self.provider.get(\n            incident_id\n        )\n\n        if context is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=incident_id,\n                intent=intent,\n                mode=(\n                    ConversationReplyMode\n                    .INCIDENT_NOT_FOUND\n                ),\n                sections=(\n                    ConversationReplySection(\n                        key="incident",\n                        title="Incident 不存在",\n                        lines=(\n                            f"未找到 Incident {incident_id}。",\n                        ),\n                    ),\n                ),\n                suggested_actions=("bind_incident",),\n            )\n\n        if intent in self._WRITE_INTENTS:\n            return self._write_intent(\n                request=request,\n                context=context,\n                intent=intent,\n            )\n\n        if intent == ConversationIntent.STATUS:\n            return self._status(request, context)\n\n        if intent == ConversationIntent.RCA:\n            return self._rca(request, context)\n\n        if intent == ConversationIntent.EVIDENCE:\n            return self._evidence(request, context)\n\n        if intent == ConversationIntent.NEXT_STEP:\n            return self._next_step(request, context)\n\n        if intent == ConversationIntent.VERIFICATION:\n            return self._verification(request, context)\n\n        return self._unknown(request, context)\n\n    @staticmethod\n    def _base(\n        request,\n        context,\n        *,\n        intent,\n        sections,\n        suggested_actions=(),\n    ):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=tuple(sections),\n            suggested_actions=tuple(suggested_actions),\n        )\n\n    def _status(self, request, context):\n        lines = [f"状态: {context.status}"]\n\n        if context.title:\n            lines.append(f"事件: {context.title}")\n\n        if context.approval_status:\n            lines.append(\n                f"审批: {context.approval_status}"\n            )\n\n        if context.action_execution_status:\n            lines.append(\n                "执行: "\n                + context.action_execution_status\n            )\n\n        if context.verification_status:\n            lines.append(\n                f"验证: {context.verification_status}"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.STATUS,\n            sections=(\n                ConversationReplySection(\n                    key="status",\n                    title="Incident 状态",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _rca(self, request, context):\n        if context.root_cause:\n            confidence = (\n                f"{context.root_cause_confidence:.0%}"\n                if context.root_cause_confidence is not None\n                else "unknown"\n            )\n            lines = (\n                f"根因: {context.root_cause}",\n                f"置信度: {confidence}",\n            )\n        elif context.hypotheses:\n            best = max(\n                context.hypotheses,\n                key=lambda item: item.confidence,\n            )\n            lines = (\n                "当前尚无最终根因。",\n                f"最高假设: {best.cause}",\n                f"假设置信度: {best.confidence:.0%}",\n            )\n        else:\n            lines = (\n                "当前还没有足够证据形成 RCA。",\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.RCA,\n            sections=(\n                ConversationReplySection(\n                    key="rca",\n                    title="根因分析",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _evidence(self, request, context):\n        if not context.evidence:\n            lines = ("当前还没有可展示的证据。",)\n        else:\n            lines = tuple(\n                (\n                    ("✓ " if item.trusted else "△ ")\n                    + item.summary\n                    + f" [{item.source}]"\n                )\n                for item in context.evidence\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.EVIDENCE,\n            sections=(\n                ConversationReplySection(\n                    key="evidence",\n                    title="证据",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "what_next",\n            ),\n        )\n\n    def _next_step(self, request, context):\n        lines = []\n\n        if context.recommended_action:\n            lines.append(\n                f"建议: {context.recommended_action}"\n            )\n\n            if context.action_risk:\n                lines.append(\n                    f"风险: {context.action_risk}"\n                )\n\n            if context.approval_status:\n                lines.append(\n                    f"审批状态: {context.approval_status}"\n                )\n        elif context.root_cause:\n            lines.append(\n                "根因已经形成，但当前没有可执行修复建议。"\n            )\n        else:\n            lines.append(\n                "继续收集证据并缩小根因假设。"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.NEXT_STEP,\n            sections=(\n                ConversationReplySection(\n                    key="next_step",\n                    title="下一步",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                (\n                    "request_remediation"\n                    if context.recommended_action\n                    else "show_evidence"\n                ),\n            ),\n        )\n\n    def _verification(self, request, context):\n        lines = (\n            (\n                f"验证状态: {context.verification_status}"\n                if context.verification_status\n                else "当前还没有 Verification 结果。"\n            ),\n        )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.VERIFICATION,\n            sections=(\n                ConversationReplySection(\n                    key="verification",\n                    title="恢复验证",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=("show_status",),\n        )\n\n    @staticmethod\n    def _write_intent(\n        *,\n        request,\n        context,\n        intent,\n    ):\n        operation = {\n            ConversationIntent.APPROVE: "approval.approve",\n            ConversationIntent.REJECT: "approval.reject",\n            ConversationIntent.REMEDIATE: "remediation.request",\n        }[intent]\n\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=(\n                ConversationReplyMode\n                .WRITE_ACTION_REQUIRED\n            ),\n            sections=(\n                ConversationReplySection(\n                    key="write_boundary",\n                    title="需要认证写操作",\n                    lines=(\n                        "Conversation Orchestrator v1 不直接执行写操作。",\n                        "该意图必须通过现有认证、RBAC、Approval/Action 边界继续。",\n                    ),\n                ),\n            ),\n            suggested_actions=(\n                "open_authenticated_write_flow",\n                "show_status",\n            ),\n            write_operation=operation,\n        )\n\n    @staticmethod\n    def _help(request, incident_id):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=ConversationIntent.HELP,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=(\n                ConversationReplySection(\n                    key="help",\n                    title="可以这样问",\n                    lines=(\n                        "现在状态怎么样？",\n                        "根因是什么？",\n                        "有哪些证据？",\n                        "下一步怎么办？",\n                        "验证结果怎么样？",\n                        "帮我修一下。",\n                        "批准执行。",\n                    ),\n                ),\n            ),\n        )\n\n    def _unknown(self, request, context):\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.UNKNOWN,\n            sections=(\n                ConversationReplySection(\n                    key="unknown",\n                    title="我还不能确定你的意图",\n                    lines=(\n                        "可以询问状态、根因、证据、下一步或验证结果。",\n                    ),\n                ),\n            ),\n            suggested_actions=("help",),\n        )\n', 'services/agent_runtime/app/conversation/__init__.py': 'from services.agent_runtime.app.conversation.chatops import (\n    BaseChatOpsChannelAdapter,\n    ChatOpsConversationGateway,\n    ChatOpsConversationRef,\n    ChatOpsInboundMessage,\n    ChatOpsOutboundMessage,\n)\nfrom services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationEvidenceView,\n    ConversationHypothesisView,\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationSession,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n    DictConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.runtime_provider import (\n    RuntimeConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n    SQLiteConversationSessionStore,\n)\n\n\n__all__ = [\n    "BaseChatOpsChannelAdapter",\n    "BaseConversationIncidentContextProvider",\n    "ChatOpsConversationGateway",\n    "ChatOpsConversationRef",\n    "ChatOpsInboundMessage",\n    "ChatOpsOutboundMessage",\n    "ConversationEvidenceView",\n    "ConversationHypothesisView",\n    "ConversationIncidentContext",\n    "ConversationIntent",\n    "ConversationOrchestrator",\n    "ConversationReplyMode",\n    "ConversationReplyPlan",\n    "ConversationReplySection",\n    "ConversationSession",\n    "ConversationTurnRequest",\n    "DeterministicConversationIntentClassifier",\n    "DictConversationIncidentContextProvider",\n    "InMemoryConversationSessionStore",\n    "RuntimeConversationIncidentContextProvider",\n    "SQLiteConversationSessionStore",\n]\n', 'services/agent_runtime/app/runtime/runtime.py': 'from copy import deepcopy\nfrom typing import Any\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.connection_factory import (\n    create_prometheus_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbe,\n    ProductionReadinessLiveProbeError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.persistence_models import (\n    build_incident_analysis_record,\n)\nfrom services.agent_runtime.app.investigation.store import (\n    IncidentAnalysisStore,\n)\nfrom services.agent_runtime.app.conversation.chatops import (\n    ChatOpsConversationGateway,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.runtime_provider import (\n    RuntimeConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    SQLiteConversationSessionStore,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            prometheus_cluster_registry\n            is None\n        ):\n            self.prometheus_cluster_registry = (\n                create_prometheus_cluster_registry()\n            )\n        else:\n            self.prometheus_cluster_registry = (\n                prometheus_cluster_registry\n            )\n\n        self.cluster_verified_evidence_required = (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n        if (\n            self.investigation_coordinator\n            is not None\n        ):\n            self.investigation_coordinator.require_cluster_verified_evidence = (\n                self.cluster_verified_evidence_required\n            )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        readiness_registry_types_valid = (\n            (\n                self.kubernetes_cluster_registry\n                is None\n                or isinstance(\n                    self.kubernetes_cluster_registry,\n                    KubernetesClusterRegistry,\n                )\n            )\n            and (\n                self.prometheus_cluster_registry\n                is None\n                or isinstance(\n                    self.prometheus_cluster_registry,\n                    PrometheusClusterRegistry,\n                )\n            )\n        )\n\n        self.production_multi_cluster_readiness = None\n        self.production_multi_cluster_coverage = None\n\n        self.production_multi_cluster_live_readiness = None\n\n        if readiness_registry_types_valid:\n            self.production_multi_cluster_readiness = (\n                ProductionMultiClusterReadinessGate(\n                    kubernetes_cluster_registry=(\n                        self.kubernetes_cluster_registry\n                    ),\n                    prometheus_cluster_registry=(\n                        self.prometheus_cluster_registry\n                    ),\n                    tools=self.tools,\n                    strict_evidence_required=(\n                        self.cluster_verified_evidence_required\n                    ),\n                )\n            )\n\n            self.production_multi_cluster_coverage = (\n                self.production_multi_cluster_readiness\n                .evaluate_all()\n            )\n\n            if (\n                self.production_multi_cluster_readiness\n                .applicable\n            ):\n                self.production_multi_cluster_live_readiness = (\n                    ProductionReadinessLiveProbe(\n                        readiness_gate=(\n                            self.production_multi_cluster_readiness\n                        ),\n                        tools=self.tools,\n                    )\n                )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools,\n            require_cluster_verified_evidence=(\n                self.cluster_verified_evidence_required\n            ),\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        self.incident_analysis_store = (\n            IncidentAnalysisStore()\n        )\n\n        self.conversation_context_provider = (\n            RuntimeConversationIncidentContextProvider(\n                incident_store=self.incident_store,\n                analysis_store=(\n                    self.incident_analysis_store\n                ),\n                approval_service=self.approval,\n                action_execution_service=(\n                    self.action_execution_service\n                ),\n                verification_service=(\n                    self.verification\n                ),\n            )\n        )\n\n        self.conversation_sessions = (\n            SQLiteConversationSessionStore()\n        )\n\n        self.conversation = ConversationOrchestrator(\n            provider=(\n                self.conversation_context_provider\n            ),\n            sessions=(\n                self.conversation_sessions\n            ),\n        )\n\n        self.chatops = ChatOpsConversationGateway(\n            orchestrator=self.conversation\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n            "production_multi_cluster_readiness",\n            "production_multi_cluster_live_readiness",\n            "incident_analysis_persistence",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Persist the authoritative Planner RCA immediately after the primary\n        # workflow. This remains weaker than the Pipeline itself: persistence\n        # failure is sanitized in metadata and cannot change Incident state.\n        await self._persist_incident_analysis(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Merge the bounded Investigation snapshot into the same per-Incident\n        # analysis record. Historical Memory remains independent.\n        await self._persist_incident_analysis(\n            context\n        )\n\n        return results\n\n    async def _persist_incident_analysis(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort durable analysis projection for ChatOps follow-up.\n\n        This method never mutates Incident, Approval, Action or Verification.\n        A persistence fault is recorded as a bounded failure code and remains\n        weaker than the authoritative PlannerPipeline.\n        """\n\n        store = getattr(\n            self,\n            "incident_analysis_store",\n            None,\n        )\n\n        if not isinstance(\n            store,\n            IncidentAnalysisStore,\n        ):\n            return\n\n        metadata = getattr(\n            context,\n            "metadata",\n            None,\n        )\n\n        if not isinstance(\n            metadata,\n            dict,\n        ):\n            return\n\n        incident = getattr(\n            context,\n            "incident",\n            None,\n        )\n\n        incident_id = getattr(\n            incident,\n            "id",\n            None,\n        )\n\n        if incident_id is None:\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "skipped",\n                "reason": "incident_identity_missing",\n            }\n\n            return\n\n        try:\n            existing = await store.get(\n                incident_id\n            )\n\n            record = build_incident_analysis_record(\n                incident_id=incident_id,\n                event=context.event,\n                request_id=context.request_id,\n                primary_rca=(\n                    context.variables.get(\n                        "rca"\n                    )\n                    if isinstance(\n                        context.variables,\n                        dict,\n                    )\n                    else None\n                ),\n                investigation_snapshot=(\n                    metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                existing=existing,\n            )\n\n            persisted = await store.upsert(\n                record\n            )\n\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "persisted",\n                "incident_id": str(\n                    persisted.incident_id\n                ),\n                "primary_rca": (\n                    persisted.primary_rca\n                    is not None\n                ),\n                "investigation": (\n                    persisted.investigation\n                    is not None\n                ),\n            }\n\n        except Exception as exc:\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[\n                        :256\n                    ]\n                ),\n            }\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_production_multi_cluster_live_readiness(\n        self,\n        context: AgentContext,\n        *,\n        acknowledgement: str,\n        reason: str,\n    ) -> dict[str, Any]:\n        """\n        Explicit bounded live-read production readiness proof.\n\n        This method is never called automatically by execute() or Runtime\n        startup. It records only a sanitized readiness snapshot.\n        """\n\n        if not isinstance(context, AgentContext):\n            raise TypeError(\n                "AgentRuntime live readiness requires AgentContext"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime live readiness requires shared Runtime tools"\n            )\n\n        probe = getattr(\n            self,\n            "production_multi_cluster_live_readiness",\n            None,\n        )\n\n        if probe is None:\n            raise ProductionReadinessLiveProbeError(\n                "AgentRuntime production live readiness is unavailable"\n            )\n\n        report = await probe.probe_event(\n            context.event,\n            acknowledgement=acknowledgement,\n            reason=reason,\n        )\n\n        snapshot = report.snapshot()\n\n        context.metadata[\n            "production_multi_cluster_live_readiness"\n        ] = deepcopy(snapshot)\n\n        return snapshot\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        if getattr(\n            self,\n            "cluster_verified_evidence_required",\n            False,\n        ):\n            if (\n                self.production_multi_cluster_readiness\n                is None\n            ):\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow readiness proof is unavailable"\n                )\n\n            readiness = (\n                self.production_multi_cluster_readiness\n                .evaluate_event(\n                    context.event\n                )\n            )\n\n            context.metadata[\n                "production_multi_cluster_readiness"\n            ] = readiness.snapshot()\n\n            if not readiness.ready:\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow read coverage is not ready"\n                )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n', 'services/agent_runtime/tests/test_durable_conversation_chatops_contract.py': 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nimport pytest\n\nfrom common.domain.event import (\n    Header,\n    Resource,\n    Signal,\n    StandardEvent,\n)\nfrom common.domain.event.enums import (\n    EventSource,\n    ResourceKind,\n    Severity,\n    SignalType,\n)\n\nfrom services.agent_runtime.app.conversation import (\n    BaseChatOpsChannelAdapter,\n    ChatOpsConversationGateway,\n    ChatOpsConversationRef,\n    ChatOpsInboundMessage,\n    ChatOpsOutboundMessage,\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationOrchestrator,\n    ConversationReplyMode,\n    ConversationTurnRequest,\n    DictConversationIncidentContextProvider,\n    SQLiteConversationSessionStore,\n)\nfrom services.agent_runtime.app.incident.state import (\n    IncidentState,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    11,\n    30,\n    tzinfo=UTC,\n)\n\n\ndef event() -> StandardEvent:\n    return StandardEvent(\n        header=Header(\n            source=EventSource.ALERTMANAGER,\n            occurred_at=NOW,\n        ),\n        signal=Signal(\n            type=SignalType.ALERT,\n            name="PodOOMKilled",\n            severity=Severity.CRITICAL,\n            message="pod was OOMKilled",\n        ),\n        resources=[\n            Resource(\n                kind=ResourceKind.POD,\n                name="checkout-api-abc123",\n                namespace="checkout",\n                cluster="prod-us-03",\n            )\n        ],\n    )\n\n\ndef context(\n    incident_id: str,\n) -> ConversationIncidentContext:\n    return ConversationIncidentContext(\n        incident_id=incident_id,\n        status="investigating",\n        title="checkout-api / PodOOMKilled",\n    )\n\n\ndef ref(\n    *,\n    channel="feishu",\n    tenant="tenant-a",\n    conversation="group-100",\n    thread="thread-200",\n):\n    return ChatOpsConversationRef(\n        channel=channel,\n        tenant_id=tenant,\n        conversation_id=conversation,\n        thread_id=thread,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_sqlite_conversation_binding_survives_store_restart(\n    tmp_path,\n):\n    db = (\n        tmp_path\n        / "conversation_sessions.db"\n    )\n\n    first = SQLiteConversationSessionStore(\n        db_path=db\n    )\n\n    await first.update(\n        conversation_id="chatops:abc123",\n        incident_id="INC-1001",\n        intent=ConversationIntent.STATUS,\n    )\n\n    second = SQLiteConversationSessionStore(\n        db_path=db\n    )\n\n    session = await second.get(\n        "chatops:abc123"\n    )\n\n    assert session is not None\n    assert session.incident_id == "INC-1001"\n    assert session.turn_count == 1\n\n\n@pytest.mark.asyncio\nasync def test_orchestrator_reuses_durable_incident_binding_after_restart(\n    tmp_path,\n):\n    db = (\n        tmp_path\n        / "conversation_sessions.db"\n    )\n\n    provider = (\n        DictConversationIncidentContextProvider(\n            {\n                "INC-1001": context(\n                    "INC-1001"\n                ),\n            }\n        )\n    )\n\n    first = ConversationOrchestrator(\n        provider=provider,\n        sessions=(\n            SQLiteConversationSessionStore(\n                db_path=db\n            )\n        ),\n    )\n\n    await first.handle(\n        ConversationTurnRequest(\n            conversation_id="chatops:thread-a",\n            incident_id="INC-1001",\n            text="现在状态怎么样？",\n        )\n    )\n\n    second = ConversationOrchestrator(\n        provider=provider,\n        sessions=(\n            SQLiteConversationSessionStore(\n                db_path=db\n            )\n        ),\n    )\n\n    reply = await second.handle(\n        ConversationTurnRequest(\n            conversation_id="chatops:thread-a",\n            text="根因是什么？",\n        )\n    )\n\n    assert reply.incident_id == "INC-1001"\n    assert reply.mode == (\n        ConversationReplyMode.READ_ONLY\n    )\n\n\ndef test_chatops_binding_key_is_stable_and_scoped():\n    first = ref()\n    same = ref()\n\n    assert (\n        first.binding_key()\n        == same.binding_key()\n    )\n\n    assert (\n        first.binding_key()\n        != ref(\n            tenant="tenant-b"\n        ).binding_key()\n    )\n\n    assert (\n        first.binding_key()\n        != ref(\n            thread="thread-201"\n        ).binding_key()\n    )\n\n    assert (\n        first.binding_key()\n        != ref(\n            channel="dingtalk"\n        ).binding_key()\n    )\n\n    assert first.binding_key().startswith(\n        "chatops:"\n    )\n\n    assert "group-100" not in (\n        first.binding_key()\n    )\n\n\n@pytest.mark.asyncio\nasync def test_chatops_gateway_binds_incident_then_reuses_thread_context(\n    tmp_path,\n):\n    db = (\n        tmp_path\n        / "conversation_sessions.db"\n    )\n\n    provider = (\n        DictConversationIncidentContextProvider(\n            {\n                "INC-1001": context(\n                    "INC-1001"\n                ),\n            }\n        )\n    )\n\n    first_gateway = ChatOpsConversationGateway(\n        orchestrator=ConversationOrchestrator(\n            provider=provider,\n            sessions=(\n                SQLiteConversationSessionStore(\n                    db_path=db\n                )\n            ),\n        )\n    )\n\n    thread = ref()\n\n    first = await first_gateway.handle(\n        ChatOpsInboundMessage(\n            conversation=thread,\n            message_id="msg-1",\n            external_actor_id="user-1",\n            incident_id="INC-1001",\n            text="现在状态怎么样？",\n        )\n    )\n\n    assert isinstance(\n        first,\n        ChatOpsOutboundMessage,\n    )\n\n    assert first.reply.incident_id == (\n        "INC-1001"\n    )\n\n    # Recreate both store and Orchestrator, simulating a Runtime process restart.\n    second_gateway = ChatOpsConversationGateway(\n        orchestrator=ConversationOrchestrator(\n            provider=provider,\n            sessions=(\n                SQLiteConversationSessionStore(\n                    db_path=db\n                )\n            ),\n        )\n    )\n\n    second = await second_gateway.handle(\n        ChatOpsInboundMessage(\n            conversation=thread,\n            message_id="msg-2",\n            external_actor_id="user-1",\n            text="根因是什么？",\n        )\n    )\n\n    assert second.reply.incident_id == (\n        "INC-1001"\n    )\n\n    assert second.reply_to_message_id == (\n        "msg-2"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_chatops_write_intent_remains_nonexecuting():\n    provider = (\n        DictConversationIncidentContextProvider(\n            {\n                "INC-1001": context(\n                    "INC-1001"\n                ),\n            }\n        )\n    )\n\n    gateway = ChatOpsConversationGateway(\n        orchestrator=ConversationOrchestrator(\n            provider=provider\n        )\n    )\n\n    output = await gateway.handle(\n        ChatOpsInboundMessage(\n            conversation=ref(),\n            message_id="msg-write",\n            external_actor_id="user-untrusted",\n            incident_id="INC-1001",\n            text="批准执行",\n        )\n    )\n\n    assert output.reply.mode == (\n        ConversationReplyMode\n        .WRITE_ACTION_REQUIRED\n    )\n\n    assert output.reply.write_operation == (\n        "approval.approve"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_chatops_binding_survives_runtime_restart(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    for name in (\n        "PROMETHEUS_URL",\n        "KUBERNETES_API_URL",\n        "KUBERNETES_SERVICE_HOST",\n        "KUBERNETES_SERVICE_PORT",\n        "KUBERNETES_SERVICE_PORT_HTTPS",\n    ):\n        monkeypatch.delenv(\n            name,\n            raising=False,\n        )\n\n    monkeypatch.setenv(\n        "PROMETHEUS_ALLOW_MOCK_FALLBACK",\n        "true",\n    )\n\n    monkeypatch.setenv(\n        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",\n        "true",\n    )\n\n    runtime_one = AgentRuntime()\n\n    incident = await (\n        runtime_one.incident_store.save(\n            IncidentState(\n                reason="ChatOps durable binding test"\n            )\n        )\n    )\n\n    thread = ref(\n        channel="slack",\n        tenant="workspace-a",\n        conversation="channel-ops",\n        thread="incident-thread",\n    )\n\n    first = await runtime_one.chatops.handle(\n        ChatOpsInboundMessage(\n            conversation=thread,\n            message_id="msg-runtime-1",\n            incident_id=str(\n                incident.id\n            ),\n            text="现在状态怎么样？",\n        )\n    )\n\n    assert first.reply.incident_id == str(\n        incident.id\n    )\n\n    runtime_two = AgentRuntime()\n\n    second = await runtime_two.chatops.handle(\n        ChatOpsInboundMessage(\n            conversation=thread,\n            message_id="msg-runtime-2",\n            text="现在状态怎么样？",\n        )\n    )\n\n    assert second.reply.incident_id == str(\n        incident.id\n    )\n\n    assert isinstance(\n        runtime_two.conversation_sessions,\n        SQLiteConversationSessionStore,\n    )\n\n\nclass FakeAdapter(\n    BaseChatOpsChannelAdapter\n):\n    def normalize_inbound(\n        self,\n        payload,\n    ):\n        return ChatOpsInboundMessage(\n            conversation=ref(),\n            message_id=payload[\n                "message_id"\n            ],\n            text=payload[\n                "text"\n            ],\n        )\n\n    def render_outbound(\n        self,\n        message,\n    ):\n        return {\n            "reply_to": (\n                message.reply_to_message_id\n            ),\n            "sections": [\n                section.model_dump(\n                    mode="json"\n                )\n                for section\n                in message.reply.sections\n            ],\n        }\n\n\ndef test_channel_adapter_contract_is_transform_only():\n    adapter = FakeAdapter()\n\n    inbound = adapter.normalize_inbound(\n        {\n            "message_id": "msg-1",\n            "text": "help",\n        }\n    )\n\n    assert isinstance(\n        inbound,\n        ChatOpsInboundMessage,\n    )\n\n\ndef test_chatops_contract_has_no_network_or_runtime_write_authority():\n    from pathlib import Path\n\n    import services.agent_runtime.app.conversation.chatops as module\n\n    source = Path(\n        module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        "httpx",\n        "requests.",\n        "aiohttp",\n        "ActionRuntime",\n        "ApprovalService",\n        "KubernetesProductionExecutor",\n        ".approve(",\n        ".reject(",\n        ".resume(",\n        ".execute(",\n        "def send(",\n        "async def send(",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n'}


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found."
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    normalized = (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    if not normalized.endswith(
        "\n"
    ):
        normalized += "\n"

    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
    )


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


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
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
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


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = raw_sha256(
        path
    )

    expected = EXPECTED_RAW_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the installed Conversation Context baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Durable Conversation / ChatOps installation."
            )
        )


def require_tests(
    root: Path,
    values: list[str],
) -> list[str]:
    missing = [
        value
        for value in values
        if not (
            root
            / value
        ).exists()
    ]

    if missing:
        raise RuntimeError(
            "Required compatibility tests are missing: "
            + ", ".join(
                missing
            )
        )

    return values


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
        (
            root
            / relative
        ): source
        for relative, source
        in SOURCES.items()
    }

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Durable Conversation Binding + ChatOps Adapter Contract v1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Product direction:",
        "- ChatOps-first AI SRE Agent",
        "- channel-neutral core before choosing Feishu/DingTalk/Slack",
        "",
        "Durable binding:",
        "- new SQLiteConversationSessionStore",
        "- default database: data/conversation_sessions.db",
        "- stores only opaque conversation binding key + incident_id + last intent + turn count",
        "- Incident/RCA/Approval/Action/Verification facts are not duplicated",
        "- Runtime restart preserves thread -> Incident binding",
        "",
        "ChatOps contract:",
        "- ChatOpsConversationRef models channel/tenant/conversation/thread identity",
        "- persisted key is chatops:<sha256>, not a raw channel/thread ID",
        "- ChatOpsInboundMessage carries normalized message + optional explicit Incident binding",
        "- ChatOpsConversationGateway delegates to the existing ConversationOrchestrator",
        "- BaseChatOpsChannelAdapter exposes normalize_inbound/render_outbound only",
        "",
        "Write boundary:",
        "- external_actor_id is an untrusted reference only",
        "- ChatOps does not map it to Runtime RBAC identity in v1",
        "- approve/reject/remediate still return WRITE_ACTION_REQUIRED",
        "- no channel SDK, network client, send(), Approval or Action execution is added",
        "",
        "Installer sends no real network/LLM/Action request.",
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

        for path in targets:
            relative = str(
                path.relative_to(
                    root
                )
            ).replace(
                "\\",
                "/",
            )

            if (
                relative
                not in EXPECTED_RAW_HASHES
                and path.exists()
            ):
                raise RuntimeError(
                    "New Durable Conversation / ChatOps target already exists; refusing overwrite: "
                    + relative
                )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        for path, source in targets.items():
            write_text(
                path,
                source,
            )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
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
                "Durable Conversation / ChatOps syntax failed"
            )

        focused = run_command(
            root=root,
            name="Durable Conversation + ChatOps focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_durable_conversation_chatops_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_conversation_orchestrator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_incident_analysis_conversation_context.py"
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
                "Durable Conversation / ChatOps focused tests failed"
            )

        compatibility_paths = require_tests(
            root,
            [
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_auto_shadow_orchestration.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_read_rbac.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_action_verification.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_action_execution_wiring.py"
                ),
            ],
        )

        compatibility = run_command(
            root=root,
            name="Runtime / API write-boundary compatibility",
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
                "Durable Conversation Runtime compatibility failed"
            )

        architecture = run_command(
            root=root,
            name="ChatOps architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/conversation/store.py').read_text(encoding='utf-8'); "
                    "c=Path(r'services/agent_runtime/app/conversation/chatops.py').read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "print('sqlite_session='+str('CREATE TABLE IF NOT EXISTS conversation_sessions' in s)); "
                    "print('opaque_binding='+str('chatops:' in c and 'sha256' in c)); "
                    "print('runtime_durable_sessions='+str('SQLiteConversationSessionStore' in r and 'self.conversation_sessions' in r)); "
                    "print('runtime_chatops_gateway='+str('self.chatops = ChatOpsConversationGateway' in r)); "
                    "assert 'CREATE TABLE IF NOT EXISTS conversation_sessions' in s; "
                    "assert 'chatops:' in c and 'sha256' in c; "
                    "assert 'SQLiteConversationSessionStore' in r; "
                    "assert 'self.chatops = ChatOpsConversationGateway' in r"
                ),
            ],
        )

        add_command(
            report,
            architecture,
        )

        if architecture.returncode != 0:
            raise RuntimeError(
                "ChatOps architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="ChatOps network/write-authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "c=Path(r'services/agent_runtime/app/conversation/chatops.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['httpx','requests.','aiohttp','ActionRuntime','ApprovalService','KubernetesProductionExecutor','.approve(','.reject(','.resume(','.execute(','def send(','async def send('] if x in c]; "
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
                "ChatOps network/write authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
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
                "Durable Conversation Binding + ChatOps Adapter Contract v1 is installed.",
                "",
                "Runtime now owns:",
                "- durable SQLite conversation session bindings",
                "- ConversationOrchestrator wired to those durable sessions",
                "- channel-neutral ChatOpsConversationGateway",
                "",
                "A channel thread can bind an Incident once and continue asking after Runtime restart without resupplying incident_id.",
                "",
                "No concrete Feishu/DingTalk/Slack network adapter is installed yet.",
                "",
                "Next recommended step:",
                "- ChatOps Identity + Authenticated Write Bridge v1: map a verified channel actor to existing Runtime authentication/RBAC identity, then route approve/reject/remediate through the current authenticated API boundary without giving the channel adapter direct ActionRuntime authority.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "DURABLE CONVERSATION BINDING + CHATOPS ADAPTER CONTRACT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "Installer sent no network/LLM/Action request."
        )
        print()
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + (
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )
                    )

        write_text(
            error,
            "\n".join(
                [
                    (
                        "Durable Conversation Binding + "
                        "ChatOps Adapter Contract v1 FAILED"
                    ),
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "DURABLE CONVERSATION BINDING + CHATOPS ADAPTER CONTRACT V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "Modified files were rolled back where possible."
        )
        print()
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
