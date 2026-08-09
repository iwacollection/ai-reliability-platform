from __future__ import annotations

import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "conversation-orchestrator-foundation-v1"
AFTER_NAME = "conversation_orchestrator_foundation_v1_after.txt"
ERROR_NAME = "conversation_orchestrator_foundation_v1_error.txt"

MODELS_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom enum import Enum\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field, field_validator\n\n\nclass ConversationIntent(str, Enum):\n    STATUS = "status"\n    RCA = "rca"\n    EVIDENCE = "evidence"\n    NEXT_STEP = "next_step"\n    VERIFICATION = "verification"\n    APPROVE = "approve"\n    REJECT = "reject"\n    REMEDIATE = "remediate"\n    HELP = "help"\n    UNKNOWN = "unknown"\n\n\nclass ConversationReplyMode(str, Enum):\n    READ_ONLY = "read_only"\n    WRITE_ACTION_REQUIRED = "write_action_required"\n    NEEDS_INCIDENT = "needs_incident"\n    INCIDENT_NOT_FOUND = "incident_not_found"\n\n\nclass ConversationTurnRequest(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    text: str\n    incident_id: str | None = None\n\n    @field_validator(\n        "conversation_id",\n        "text",\n        "incident_id",\n        mode="before",\n    )\n    @classmethod\n    def validate_text(cls, value, info):\n        if value is None and info.field_name == "incident_id":\n            return None\n\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or "\\x00" in value\n        ):\n            raise ValueError(\n                f"{info.field_name} is invalid"\n            )\n\n        limit = 4096 if info.field_name == "text" else 256\n        if len(value) > limit:\n            raise ValueError(\n                f"{info.field_name} is too long"\n            )\n\n        return value\n\n\nclass ConversationEvidenceView(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    evidence_id: str\n    source: str\n    summary: str\n    trusted: bool = False\n    cluster_verified: bool = False\n\n\nclass ConversationHypothesisView(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    cause: str\n    confidence: float = Field(ge=0.0, le=1.0)\n\n\nclass ConversationIncidentContext(BaseModel):\n    """\n    Stable ChatOps-facing incident view.\n\n    Existing persistence remains authoritative. The conversation layer receives\n    only a read-only projection assembled by a provider adapter.\n    """\n\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    incident_id: str\n    status: str\n    title: str | None = None\n    summary: str | None = None\n\n    root_cause: str | None = None\n    root_cause_confidence: float | None = Field(\n        default=None,\n        ge=0.0,\n        le=1.0,\n    )\n\n    evidence: tuple[ConversationEvidenceView, ...] = ()\n    hypotheses: tuple[ConversationHypothesisView, ...] = ()\n\n    recommended_action: str | None = None\n    action_risk: str | None = None\n    approval_status: str | None = None\n    verification_status: str | None = None\n\n    metadata: dict[str, Any] = Field(default_factory=dict)\n\n\nclass ConversationReplySection(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    key: str\n    title: str\n    lines: tuple[str, ...] = ()\n\n\nclass ConversationReplyPlan(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    incident_id: str | None\n    intent: ConversationIntent\n    mode: ConversationReplyMode\n\n    sections: tuple[ConversationReplySection, ...] = ()\n    suggested_actions: tuple[str, ...] = ()\n    write_operation: str | None = None\n\n    created_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n\n\nclass ConversationSession(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    incident_id: str | None = None\n    last_intent: ConversationIntent | None = None\n    turn_count: int = Field(default=0, ge=0)\n    created_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n'
CLASSIFIER_SOURCE = 'from __future__ import annotations\n\nimport re\nimport unicodedata\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIntent,\n)\n\n\nclass DeterministicConversationIntentClassifier:\n    """\n    Conservative deterministic intent classifier.\n\n    Write-capable intents are explicit and are never delegated to an LLM in v1.\n    """\n\n    _RULES = (\n        (\n            ConversationIntent.APPROVE,\n            (\n                "批准",\n                "同意执行",\n                "同意修复",\n                "确认执行",\n                "approve",\n                "approved",\n            ),\n        ),\n        (\n            ConversationIntent.REJECT,\n            (\n                "拒绝",\n                "不要执行",\n                "取消修复",\n                "reject",\n                "rejected",\n                "do not execute",\n            ),\n        ),\n        (\n            ConversationIntent.REMEDIATE,\n            (\n                "帮我修",\n                "修一下",\n                "执行修复",\n                "开始修复",\n                "自动修复",\n                "remediate",\n                "fix it",\n                "heal it",\n            ),\n        ),\n        (\n            ConversationIntent.VERIFICATION,\n            (\n                "验证结果",\n                "验证怎么样",\n                "修复验证",\n                "verification",\n                "verify result",\n                "recovery check",\n            ),\n        ),\n        (\n            ConversationIntent.EVIDENCE,\n            (\n                "证据",\n                "依据",\n                "为什么这么判断",\n                "你看到了什么",\n                "evidence",\n                "supporting evidence",\n            ),\n        ),\n        (\n            ConversationIntent.RCA,\n            (\n                "根因",\n                "原因是什么",\n                "为什么会",\n                "为什么是",\n                "rca",\n                "root cause",\n                "why did",\n            ),\n        ),\n        (\n            ConversationIntent.NEXT_STEP,\n            (\n                "下一步",\n                "怎么办",\n                "建议怎么",\n                "怎么处理",\n                "what next",\n                "next step",\n                "recommend",\n            ),\n        ),\n        (\n            ConversationIntent.STATUS,\n            (\n                "状态",\n                "进展",\n                "现在怎么样",\n                "处理到哪",\n                "恢复了吗",\n                "status",\n                "progress",\n                "current state",\n            ),\n        ),\n        (\n            ConversationIntent.HELP,\n            (\n                "帮助",\n                "你能做什么",\n                "怎么用",\n                "help",\n                "what can you do",\n            ),\n        ),\n    )\n\n    def classify(self, text: str) -> ConversationIntent:\n        normalized = self._normalize(text)\n\n        for intent, phrases in self._RULES:\n            for phrase in phrases:\n                if self._contains(\n                    normalized,\n                    self._normalize(phrase),\n                ):\n                    return intent\n\n        return ConversationIntent.UNKNOWN\n\n    @staticmethod\n    def _normalize(value: str) -> str:\n        normalized = unicodedata.normalize(\n            "NFKC",\n            value,\n        ).strip().lower()\n\n        return re.sub(\n            r"\\s+",\n            " ",\n            normalized,\n        )\n\n    @staticmethod\n    def _contains(text: str, phrase: str) -> bool:\n        if not phrase:\n            return False\n\n        if re.fullmatch(r"[a-z0-9 ]+", phrase):\n            return bool(\n                re.search(\n                    r"(?<![a-z0-9])"\n                    + re.escape(phrase)\n                    + r"(?![a-z0-9])",\n                    text,\n                )\n            )\n\n        return phrase in text\n'
STORE_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIntent,\n    ConversationSession,\n)\n\n\nclass InMemoryConversationSessionStore:\n    """\n    Bounded process-local conversation routing state.\n\n    Incident/Evidence/RCA data remains in existing authoritative persistence.\n    """\n\n    def __init__(self, *, max_sessions: int = 10000) -> None:\n        if (\n            not isinstance(max_sessions, int)\n            or isinstance(max_sessions, bool)\n            or max_sessions <= 0\n            or max_sessions > 100000\n        ):\n            raise ValueError(\n                "Conversation max_sessions is invalid"\n            )\n\n        self.max_sessions = max_sessions\n        self._items: dict[str, ConversationSession] = {}\n        self._lock = asyncio.Lock()\n\n    async def get(\n        self,\n        conversation_id: str,\n    ) -> ConversationSession | None:\n        async with self._lock:\n            return self._items.get(conversation_id)\n\n    async def update(\n        self,\n        *,\n        conversation_id: str,\n        incident_id: str | None,\n        intent: ConversationIntent,\n    ) -> ConversationSession:\n        async with self._lock:\n            current = self._items.get(conversation_id)\n            now = datetime.now(UTC)\n\n            if current is None:\n                if len(self._items) >= self.max_sessions:\n                    oldest_key = min(\n                        self._items,\n                        key=lambda key: self._items[key].updated_at,\n                    )\n                    self._items.pop(oldest_key, None)\n\n                value = ConversationSession(\n                    conversation_id=conversation_id,\n                    incident_id=incident_id,\n                    last_intent=intent,\n                    turn_count=1,\n                    created_at=now,\n                    updated_at=now,\n                )\n            else:\n                value = current.model_copy(\n                    update={\n                        "incident_id": (\n                            incident_id\n                            if incident_id is not None\n                            else current.incident_id\n                        ),\n                        "last_intent": intent,\n                        "turn_count": current.turn_count + 1,\n                        "updated_at": now,\n                    }\n                )\n\n            self._items[conversation_id] = value\n            return value\n'
PROVIDER_SOURCE = 'from __future__ import annotations\n\nfrom abc import ABC, abstractmethod\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n)\n\n\nclass BaseConversationIncidentContextProvider(ABC):\n    """\n    Read-only bridge into existing Runtime persistence.\n\n    Mutation methods are deliberately absent.\n    """\n\n    @abstractmethod\n    async def get(\n        self,\n        incident_id: str,\n    ) -> ConversationIncidentContext | None:\n        raise NotImplementedError\n\n\nclass DictConversationIncidentContextProvider(\n    BaseConversationIncidentContextProvider\n):\n    def __init__(\n        self,\n        items: dict[\n            str,\n            ConversationIncidentContext,\n        ] | None = None,\n    ) -> None:\n        self._items = dict(items or {})\n\n    async def get(\n        self,\n        incident_id: str,\n    ) -> ConversationIncidentContext | None:\n        return self._items.get(incident_id)\n'
ORCHESTRATOR_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n)\n\n\nclass ConversationOrchestrator:\n    """\n    Channel-neutral ChatOps core.\n\n    v1 binds a conversation to an Incident, classifies intent, reads a stable\n    Incident projection, and returns a structured reply plan.\n\n    It has no direct Action/Approval/Verification write authority.\n    """\n\n    _WRITE_INTENTS = {\n        ConversationIntent.APPROVE,\n        ConversationIntent.REJECT,\n        ConversationIntent.REMEDIATE,\n    }\n\n    def __init__(\n        self,\n        *,\n        provider: BaseConversationIncidentContextProvider,\n        sessions: InMemoryConversationSessionStore | None = None,\n        classifier: (\n            DeterministicConversationIntentClassifier\n            | None\n        ) = None,\n    ) -> None:\n        if not isinstance(\n            provider,\n            BaseConversationIncidentContextProvider,\n        ):\n            raise TypeError(\n                "Conversation context provider is invalid"\n            )\n\n        self.provider = provider\n        self.sessions = (\n            sessions\n            or InMemoryConversationSessionStore()\n        )\n        self.classifier = (\n            classifier\n            or DeterministicConversationIntentClassifier()\n        )\n\n    async def handle(\n        self,\n        request: ConversationTurnRequest,\n    ) -> ConversationReplyPlan:\n        if not isinstance(\n            request,\n            ConversationTurnRequest,\n        ):\n            raise TypeError(\n                "Conversation request is invalid"\n            )\n\n        intent = self.classifier.classify(\n            request.text\n        )\n\n        current = await self.sessions.get(\n            request.conversation_id\n        )\n\n        incident_id = (\n            request.incident_id\n            or (\n                current.incident_id\n                if current is not None\n                else None\n            )\n        )\n\n        await self.sessions.update(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=intent,\n        )\n\n        if intent == ConversationIntent.HELP:\n            return self._help(\n                request,\n                incident_id,\n            )\n\n        if incident_id is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=None,\n                intent=intent,\n                mode=ConversationReplyMode.NEEDS_INCIDENT,\n                sections=(\n                    ConversationReplySection(\n                        key="incident_binding",\n                        title="需要 Incident",\n                        lines=(\n                            "请先绑定一个 Incident，再继续查询或操作。",\n                        ),\n                    ),\n                ),\n                suggested_actions=(\n                    "bind_incident",\n                    "help",\n                ),\n            )\n\n        context = await self.provider.get(\n            incident_id\n        )\n\n        if context is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=incident_id,\n                intent=intent,\n                mode=(\n                    ConversationReplyMode\n                    .INCIDENT_NOT_FOUND\n                ),\n                sections=(\n                    ConversationReplySection(\n                        key="incident",\n                        title="Incident 不存在",\n                        lines=(\n                            f"未找到 Incident {incident_id}。",\n                        ),\n                    ),\n                ),\n                suggested_actions=("bind_incident",),\n            )\n\n        if intent in self._WRITE_INTENTS:\n            return self._write_intent(\n                request=request,\n                context=context,\n                intent=intent,\n            )\n\n        if intent == ConversationIntent.STATUS:\n            return self._status(request, context)\n\n        if intent == ConversationIntent.RCA:\n            return self._rca(request, context)\n\n        if intent == ConversationIntent.EVIDENCE:\n            return self._evidence(request, context)\n\n        if intent == ConversationIntent.NEXT_STEP:\n            return self._next_step(request, context)\n\n        if intent == ConversationIntent.VERIFICATION:\n            return self._verification(request, context)\n\n        return self._unknown(request, context)\n\n    @staticmethod\n    def _base(\n        request,\n        context,\n        *,\n        intent,\n        sections,\n        suggested_actions=(),\n    ):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=tuple(sections),\n            suggested_actions=tuple(suggested_actions),\n        )\n\n    def _status(self, request, context):\n        lines = [f"状态: {context.status}"]\n\n        if context.title:\n            lines.append(f"事件: {context.title}")\n\n        if context.approval_status:\n            lines.append(\n                f"审批: {context.approval_status}"\n            )\n\n        if context.verification_status:\n            lines.append(\n                f"验证: {context.verification_status}"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.STATUS,\n            sections=(\n                ConversationReplySection(\n                    key="status",\n                    title="Incident 状态",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _rca(self, request, context):\n        if context.root_cause:\n            confidence = (\n                f"{context.root_cause_confidence:.0%}"\n                if context.root_cause_confidence is not None\n                else "unknown"\n            )\n            lines = (\n                f"根因: {context.root_cause}",\n                f"置信度: {confidence}",\n            )\n        elif context.hypotheses:\n            best = max(\n                context.hypotheses,\n                key=lambda item: item.confidence,\n            )\n            lines = (\n                "当前尚无最终根因。",\n                f"最高假设: {best.cause}",\n                f"假设置信度: {best.confidence:.0%}",\n            )\n        else:\n            lines = (\n                "当前还没有足够证据形成 RCA。",\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.RCA,\n            sections=(\n                ConversationReplySection(\n                    key="rca",\n                    title="根因分析",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _evidence(self, request, context):\n        if not context.evidence:\n            lines = ("当前还没有可展示的证据。",)\n        else:\n            lines = tuple(\n                (\n                    ("✓ " if item.trusted else "△ ")\n                    + item.summary\n                    + f" [{item.source}]"\n                )\n                for item in context.evidence\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.EVIDENCE,\n            sections=(\n                ConversationReplySection(\n                    key="evidence",\n                    title="证据",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "what_next",\n            ),\n        )\n\n    def _next_step(self, request, context):\n        lines = []\n\n        if context.recommended_action:\n            lines.append(\n                f"建议: {context.recommended_action}"\n            )\n\n            if context.action_risk:\n                lines.append(\n                    f"风险: {context.action_risk}"\n                )\n\n            if context.approval_status:\n                lines.append(\n                    f"审批状态: {context.approval_status}"\n                )\n        elif context.root_cause:\n            lines.append(\n                "根因已经形成，但当前没有可执行修复建议。"\n            )\n        else:\n            lines.append(\n                "继续收集证据并缩小根因假设。"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.NEXT_STEP,\n            sections=(\n                ConversationReplySection(\n                    key="next_step",\n                    title="下一步",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                (\n                    "request_remediation"\n                    if context.recommended_action\n                    else "show_evidence"\n                ),\n            ),\n        )\n\n    def _verification(self, request, context):\n        lines = (\n            (\n                f"验证状态: {context.verification_status}"\n                if context.verification_status\n                else "当前还没有 Verification 结果。"\n            ),\n        )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.VERIFICATION,\n            sections=(\n                ConversationReplySection(\n                    key="verification",\n                    title="恢复验证",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=("show_status",),\n        )\n\n    @staticmethod\n    def _write_intent(\n        *,\n        request,\n        context,\n        intent,\n    ):\n        operation = {\n            ConversationIntent.APPROVE: "approval.approve",\n            ConversationIntent.REJECT: "approval.reject",\n            ConversationIntent.REMEDIATE: "remediation.request",\n        }[intent]\n\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=(\n                ConversationReplyMode\n                .WRITE_ACTION_REQUIRED\n            ),\n            sections=(\n                ConversationReplySection(\n                    key="write_boundary",\n                    title="需要认证写操作",\n                    lines=(\n                        "Conversation Orchestrator v1 不直接执行写操作。",\n                        "该意图必须通过现有认证、RBAC、Approval/Action 边界继续。",\n                    ),\n                ),\n            ),\n            suggested_actions=(\n                "open_authenticated_write_flow",\n                "show_status",\n            ),\n            write_operation=operation,\n        )\n\n    @staticmethod\n    def _help(request, incident_id):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=ConversationIntent.HELP,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=(\n                ConversationReplySection(\n                    key="help",\n                    title="可以这样问",\n                    lines=(\n                        "现在状态怎么样？",\n                        "根因是什么？",\n                        "有哪些证据？",\n                        "下一步怎么办？",\n                        "验证结果怎么样？",\n                        "帮我修一下。",\n                        "批准执行。",\n                    ),\n                ),\n            ),\n        )\n\n    def _unknown(self, request, context):\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.UNKNOWN,\n            sections=(\n                ConversationReplySection(\n                    key="unknown",\n                    title="我还不能确定你的意图",\n                    lines=(\n                        "可以询问状态、根因、证据、下一步或验证结果。",\n                    ),\n                ),\n            ),\n            suggested_actions=("help",),\n        )\n'
INIT_SOURCE = 'from services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationEvidenceView,\n    ConversationHypothesisView,\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationSession,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n    DictConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n)\n\n\n__all__ = [\n    "BaseConversationIncidentContextProvider",\n    "ConversationEvidenceView",\n    "ConversationHypothesisView",\n    "ConversationIncidentContext",\n    "ConversationIntent",\n    "ConversationOrchestrator",\n    "ConversationReplyMode",\n    "ConversationReplyPlan",\n    "ConversationReplySection",\n    "ConversationSession",\n    "ConversationTurnRequest",\n    "DeterministicConversationIntentClassifier",\n    "DictConversationIncidentContextProvider",\n    "InMemoryConversationSessionStore",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport pytest\n\nfrom services.agent_runtime.app.conversation import (\n    ConversationEvidenceView,\n    ConversationHypothesisView,\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationOrchestrator,\n    ConversationReplyMode,\n    ConversationTurnRequest,\n    DeterministicConversationIntentClassifier,\n    DictConversationIncidentContextProvider,\n)\n\n\ndef context(incident_id="INC-1001"):\n    return ConversationIncidentContext(\n        incident_id=incident_id,\n        status="waiting_approval",\n        title="payment-api PodOOMKilled",\n        root_cause=(\n            "Memory limit regression caused OOMKilled"\n        ),\n        root_cause_confidence=0.94,\n        evidence=(\n            ConversationEvidenceView(\n                evidence_id="ev-1",\n                source="kubernetes",\n                summary="Container terminated with OOMKilled",\n                trusted=True,\n                cluster_verified=True,\n            ),\n            ConversationEvidenceView(\n                evidence_id="ev-2",\n                source="prometheus",\n                summary="Memory utilization reached 99%",\n                trusted=True,\n                cluster_verified=True,\n            ),\n        ),\n        hypotheses=(\n            ConversationHypothesisView(\n                cause="Memory limit regression",\n                confidence=0.94,\n            ),\n            ConversationHypothesisView(\n                cause="Memory leak",\n                confidence=0.21,\n            ),\n        ),\n        recommended_action=(\n            "Restore memory limit from 512Mi to 1Gi"\n        ),\n        action_risk="medium",\n        approval_status="pending",\n    )\n\n\ndef orchestrator():\n    provider = DictConversationIncidentContextProvider(\n        {\n            "INC-1001": context(),\n            "INC-2002": context("INC-2002"),\n        }\n    )\n    return ConversationOrchestrator(\n        provider=provider\n    )\n\n\n@pytest.mark.parametrize(\n    ("text", "intent"),\n    [\n        ("现在状态怎么样？", ConversationIntent.STATUS),\n        ("根因是什么？", ConversationIntent.RCA),\n        ("为什么这么判断？", ConversationIntent.EVIDENCE),\n        ("有哪些证据？", ConversationIntent.EVIDENCE),\n        ("下一步怎么办？", ConversationIntent.NEXT_STEP),\n        ("验证结果怎么样？", ConversationIntent.VERIFICATION),\n        ("帮我修一下", ConversationIntent.REMEDIATE),\n        ("批准执行", ConversationIntent.APPROVE),\n        ("拒绝", ConversationIntent.REJECT),\n        ("help", ConversationIntent.HELP),\n    ],\n)\ndef test_deterministic_intent_classifier(\n    text,\n    intent,\n):\n    classifier = (\n        DeterministicConversationIntentClassifier()\n    )\n    assert classifier.classify(text) == intent\n\n\n@pytest.mark.asyncio\nasync def test_first_turn_requires_incident_binding():\n    value = orchestrator()\n    reply = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-1",\n            text="根因是什么？",\n        )\n    )\n    assert reply.mode == (\n        ConversationReplyMode.NEEDS_INCIDENT\n    )\n    assert reply.incident_id is None\n\n\n@pytest.mark.asyncio\nasync def test_explicit_incident_binding_is_reused_by_follow_up_turns():\n    value = orchestrator()\n\n    first = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-1",\n            incident_id="INC-1001",\n            text="现在状态怎么样？",\n        )\n    )\n\n    second = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-1",\n            text="根因是什么？",\n        )\n    )\n\n    assert first.incident_id == "INC-1001"\n    assert second.incident_id == "INC-1001"\n\n    session = await value.sessions.get("chat-1")\n    assert session is not None\n    assert session.incident_id == "INC-1001"\n    assert session.turn_count == 2\n\n\n@pytest.mark.asyncio\nasync def test_explicit_new_incident_rebinds_conversation():\n    value = orchestrator()\n\n    await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-1",\n            incident_id="INC-1001",\n            text="状态",\n        )\n    )\n\n    reply = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-1",\n            incident_id="INC-2002",\n            text="状态",\n        )\n    )\n    assert reply.incident_id == "INC-2002"\n\n\n@pytest.mark.asyncio\nasync def test_rca_reply_uses_existing_context_without_llm():\n    value = orchestrator()\n\n    reply = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-2",\n            incident_id="INC-1001",\n            text="根因是什么？",\n        )\n    )\n\n    assert reply.intent == ConversationIntent.RCA\n    assert reply.mode == ConversationReplyMode.READ_ONLY\n\n    text = str(reply.model_dump())\n    assert (\n        "Memory limit regression caused OOMKilled"\n        in text\n    )\n    assert "94%" in text\n\n\n@pytest.mark.asyncio\nasync def test_evidence_reply_preserves_sources():\n    value = orchestrator()\n\n    reply = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-3",\n            incident_id="INC-1001",\n            text="证据是什么？",\n        )\n    )\n\n    text = str(reply.model_dump())\n    assert "OOMKilled" in text\n    assert "99%" in text\n    assert "kubernetes" in text\n    assert "prometheus" in text\n\n\n@pytest.mark.asyncio\nasync def test_write_intents_return_explicit_nonexecuting_boundary():\n    value = orchestrator()\n\n    for text, expected_operation in (\n        ("批准执行", "approval.approve"),\n        ("拒绝", "approval.reject"),\n        ("帮我修一下", "remediation.request"),\n    ):\n        reply = await value.handle(\n            ConversationTurnRequest(\n                conversation_id=(\n                    "chat-write-"\n                    + expected_operation\n                ),\n                incident_id="INC-1001",\n                text=text,\n            )\n        )\n\n        assert reply.mode == (\n            ConversationReplyMode\n            .WRITE_ACTION_REQUIRED\n        )\n        assert (\n            reply.write_operation\n            == expected_operation\n        )\n\n\n@pytest.mark.asyncio\nasync def test_unknown_incident_is_explicit():\n    value = orchestrator()\n\n    reply = await value.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-4",\n            incident_id="INC-404",\n            text="状态",\n        )\n    )\n\n    assert reply.mode == (\n        ConversationReplyMode\n        .INCIDENT_NOT_FOUND\n    )\n\n\ndef test_conversation_core_has_no_runtime_write_authority():\n    from pathlib import Path\n    import services.agent_runtime.app.conversation.orchestrator as module\n\n    source = Path(module.__file__).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        "ActionRuntime",\n        "ApprovalService",\n        "KubernetesProductionExecutor",\n        ".approve(",\n        ".reject(",\n        ".resume(",\n        ".execute(",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
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
    raise RuntimeError("Repository root not found.")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
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
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(lines: list[str], title: str) -> None:
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
    section(lines, f"COMMAND: {result.name}")
    lines.extend(
        [
            " ".join(result.command),
            "",
            f"ExitCode: {result.returncode}",
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


def main() -> int:
    root = find_repo_root(Path.cwd().resolve())
    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (after, error):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    package = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "conversation"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_conversation_orchestrator.py"
    )

    files = {
        package / "__init__.py": INIT_SOURCE,
        package / "models.py": MODELS_SOURCE,
        package / "classifier.py": CLASSIFIER_SOURCE,
        package / "store.py": STORE_SOURCE,
        package / "provider.py": PROVIDER_SOURCE,
        package / "orchestrator.py": ORCHESTRATOR_SOURCE,
        test_file: TEST_SOURCE,
    }

    created = []

    report = [
        "Conversation Orchestrator Foundation v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Direction:",
        "- ChatOps-first",
        "- channel-neutral conversation core",
        "- additive only",
        "",
        "Capabilities:",
        "- conversation -> Incident binding",
        "- deterministic Chinese/English intent classification",
        "- follow-up context retention",
        "- structured reply plans for status/RCA/evidence/next-step/verification",
        "- explicit detection of approve/reject/remediate",
        "",
        "Write boundary:",
        "- approve/reject/remediate are never executed by v1",
        "- v1 returns WRITE_ACTION_REQUIRED only",
        "- existing authenticated RBAC/Approval/Action boundary remains authoritative",
        "",
        "No existing production file is modified.",
    ]

    try:
        for path in files:
            if path.exists():
                raise RuntimeError(
                    "Conversation v1 target already exists: "
                    + str(path.relative_to(root))
                )

        for path, source in files.items():
            write_text(path, source)
            created.append(path)

        syntax = run_command(
            root=root,
            name="Conversation Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(path.relative_to(root))
                    for path in files
                ],
            ],
        )
        add_command(report, syntax)
        if syntax.returncode != 0:
            raise RuntimeError(
                "Conversation syntax failed"
            )

        focused = run_command(
            root=root,
            name="Conversation focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_conversation_orchestrator.py"
                ),
                "-q",
            ],
        )
        add_command(report, focused)
        if focused.returncode != 0:
            raise RuntimeError(
                "Conversation focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Existing API / Investigation compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_api_read_rbac.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_action_resume.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_action_verification.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                "-q",
            ],
        )
        add_command(report, compatibility)
        if compatibility.returncode != 0:
            raise RuntimeError(
                "Conversation compatibility tests failed"
            )

        authority = run_command(
            root=root,
            name="Conversation write-authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/orchestrator.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','KubernetesProductionExecutor','.approve(','.reject(','.resume(','.execute('] if x in p]; "
                    "print('forbidden_matches='+str(bad)); "
                    "print('write_boundary='+str('WRITE_ACTION_REQUIRED' in p)); "
                    "raise SystemExit(1 if bad or 'WRITE_ACTION_REQUIRED' not in p else 0)"
                ),
            ],
        )
        add_command(report, authority)
        if authority.returncode != 0:
            raise RuntimeError(
                "Conversation authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                "services/agent_runtime/app/conversation",
                (
                    "services/agent_runtime/tests/"
                    "test_conversation_orchestrator.py"
                ),
            ],
        )
        add_command(report, status)

        section(report, "RESULT")
        report.extend(
            [
                "PASSED",
                "",
                "Conversation Orchestrator Foundation v1 is installed.",
                "",
                "Read intents:",
                "- status",
                "- rca",
                "- evidence",
                "- next_step",
                "- verification",
                "- help",
                "",
                "Recognized non-executing write intents:",
                "- approve -> approval.approve",
                "- reject -> approval.reject",
                "- remediate -> remediation.request",
                "",
                "Next recommended step:",
                "- Runtime Conversation Context Provider v1: assemble this read-only context from existing Incident / Investigation / Approval / Verification persistence.",
            ]
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print(
            "CONVERSATION ORCHESTRATOR FOUNDATION V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "No existing production file was modified."
        )
        print(
            "No network/LLM/Action/Approval write was executed."
        )
        print()
        print("Upload only:")
        print(after)
        return 0

    except Exception as exc:
        rollback = []

        for path in reversed(created):
            try:
                path.unlink()
                rollback.append(
                    "REMOVED "
                    + str(path.relative_to(root))
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(path.relative_to(root))
                    + ": "
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        try:
            if package.exists() and not any(
                package.iterdir()
            ):
                package.rmdir()
        except Exception:
            pass

        write_text(
            error,
            "\n".join(
                [
                    "Conversation Orchestrator Foundation v1 FAILED",
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
            "CONVERSATION ORCHESTRATOR FOUNDATION V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "New Conversation files were rolled back where possible."
        )
        print()
        print("Upload only:")
        print(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
