from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = (
    "incident-analysis-persistence-conversation-context-provider-v1.1"
)

AFTER_NAME = (
    "incident_analysis_persistence_conversation_context_provider_v1_1_after.txt"
)

ERROR_NAME = (
    "incident_analysis_persistence_conversation_context_provider_v1_1_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/runtime/runtime.py': 'be3df28faaf881e45293ec4b5819c0a72cbce95e68ee8e51df4c83f31c318656', 'services/agent_runtime/app/conversation/models.py': 'fcd04f5ff7ec1ebe5e2314d51e87fe5cb0715f6a3269a5e823c532bd4fa36a46', 'services/agent_runtime/app/conversation/provider.py': '5aa44efe948c748321526484f0327252f84d2ac824548d6cfe92a720ae34caac', 'services/agent_runtime/app/conversation/orchestrator.py': '19a4f6dcfb136f0c316bdae7ce260b1ff8ad2870e22565877a96905e9155c7a1', 'services/agent_runtime/app/conversation/__init__.py': 'f10cdc44fa3b857dd219a25df595e71badfd0f0ba4e0561425a9fb68566b0570'}

SOURCES = {'services/agent_runtime/app/investigation/persistence_models.py': 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Annotated, Any\nfrom uuid import UUID\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    StringConstraints,\n)\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\n\n\nBoundedText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=512,\n    ),\n]\n\nLongBoundedText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=2000,\n    ),\n]\n\n\nclass IncidentAnalysisScope(BaseModel):\n    """\n    Stable, non-secret Incident scope retained for ChatOps queries.\n\n    The scope is copied from the original StandardEvent. It does not contain\n    credentials, endpoint URLs or raw backend payloads.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    alert_name: BoundedText\n    resource: BoundedText | None = None\n    namespace: BoundedText | None = None\n    cluster: BoundedText | None = None\n\n\nclass IncidentPrimaryRCA(BaseModel):\n    """\n    Bounded durable projection of the authoritative Planner RCA.\n\n    Legacy MemoryStore remains historical similarity memory. This model is\n    per-Incident and therefore does not use the service/alert memory key.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    root_cause: LongBoundedText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    evidence: tuple[\n        LongBoundedText,\n        ...,\n    ] = Field(\n        default_factory=tuple,\n        max_length=32,\n    )\n    recorded_at: datetime = Field(\n        default_factory=lambda: datetime.now(\n            UTC\n        )\n    )\n\n\nclass IncidentAnalysisRecord(BaseModel):\n    """\n    One durable analysis snapshot per Incident.\n\n    Incident lifecycle state remains owned by IncidentStore.\n    Approval, Action Execution and Verification remain owned by their existing\n    stores. This record owns only analysis facts that previously lived in\n    request-local context.variables/context.metadata.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    incident_id: UUID\n    request_id: BoundedText | None = None\n    scope: IncidentAnalysisScope\n    primary_rca: IncidentPrimaryRCA | None = None\n    investigation: InvestigationState | None = None\n    created_at: datetime = Field(\n        default_factory=lambda: datetime.now(\n            UTC\n        )\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(\n            UTC\n        )\n    )\n\n\ndef build_incident_analysis_record(\n    *,\n    incident_id: UUID | str,\n    event: Any,\n    request_id: Any = None,\n    primary_rca: Any = None,\n    investigation_snapshot: Any = None,\n    existing: IncidentAnalysisRecord | None = None,\n    now: datetime | None = None,\n) -> IncidentAnalysisRecord:\n    """\n    Build one bounded record from Runtime-owned values.\n\n    Invalid optional RCA/Investigation data is ignored rather than guessed.\n    The caller may persist a scope-only record and enrich it later.\n    """\n\n    normalized_incident_id = UUID(\n        str(\n            incident_id\n        )\n    )\n\n    current_time = (\n        now\n        or datetime.now(\n            UTC\n        )\n    )\n\n    if (\n        current_time.tzinfo is None\n        or current_time.utcoffset()\n        is None\n    ):\n        raise ValueError(\n            "Incident analysis clock must be timezone-aware"\n        )\n\n    current_time = (\n        current_time.astimezone(\n            UTC\n        )\n    )\n\n    scope = (\n        _scope_from_event(\n            event\n        )\n        or (\n            existing.scope\n            if existing is not None\n            else None\n        )\n    )\n\n    if scope is None:\n        raise ValueError(\n            "Incident analysis requires bounded event scope"\n        )\n\n    parsed_primary = (\n        _primary_rca(\n            primary_rca,\n            recorded_at=current_time,\n        )\n    )\n\n    if (\n        parsed_primary is None\n        and existing is not None\n    ):\n        parsed_primary = (\n            existing.primary_rca\n        )\n\n    parsed_investigation = (\n        _investigation(\n            investigation_snapshot\n        )\n    )\n\n    if (\n        parsed_investigation is None\n        and existing is not None\n    ):\n        parsed_investigation = (\n            existing.investigation\n        )\n\n    return IncidentAnalysisRecord(\n        incident_id=(\n            normalized_incident_id\n        ),\n        request_id=(\n            _optional_text(\n                request_id,\n                max_length=512,\n            )\n            or (\n                existing.request_id\n                if existing is not None\n                else None\n            )\n        ),\n        scope=scope,\n        primary_rca=parsed_primary,\n        investigation=parsed_investigation,\n        created_at=(\n            existing.created_at\n            if existing is not None\n            else current_time\n        ),\n        updated_at=current_time,\n    )\n\n\ndef _scope_from_event(\n    event: Any,\n) -> IncidentAnalysisScope | None:\n    signal = getattr(\n        event,\n        "signal",\n        None,\n    )\n\n    alert_name = _optional_text(\n        getattr(\n            signal,\n            "name",\n            None,\n        ),\n        max_length=512,\n    )\n\n    resources = getattr(\n        event,\n        "resources",\n        None,\n    )\n\n    resource = None\n\n    if isinstance(\n        resources,\n        (\n            list,\n            tuple,\n        ),\n    ) and resources:\n        resource = resources[\n            0\n        ]\n\n    resource_name = _optional_text(\n        getattr(\n            resource,\n            "name",\n            None,\n        ),\n        max_length=512,\n    )\n\n    if alert_name is None:\n        return None\n\n    return IncidentAnalysisScope(\n        alert_name=alert_name,\n        resource=resource_name,\n        namespace=_optional_text(\n            getattr(\n                resource,\n                "namespace",\n                None,\n            ),\n            max_length=512,\n        ),\n        cluster=_optional_text(\n            getattr(\n                resource,\n                "cluster",\n                None,\n            ),\n            max_length=512,\n        ),\n    )\n\n\ndef _primary_rca(\n    value: Any,\n    *,\n    recorded_at: datetime,\n) -> IncidentPrimaryRCA | None:\n    if not isinstance(\n        value,\n        dict,\n    ):\n        return None\n\n    root_cause = _optional_text(\n        value.get(\n            "root_cause"\n        ),\n        max_length=2000,\n    )\n\n    confidence = value.get(\n        "confidence"\n    )\n\n    if (\n        root_cause is None\n        or isinstance(\n            confidence,\n            bool,\n        )\n        or not isinstance(\n            confidence,\n            (\n                int,\n                float,\n            ),\n        )\n    ):\n        return None\n\n    confidence_value = float(\n        confidence\n    )\n\n    if (\n        not isfinite(\n            confidence_value\n        )\n        or confidence_value < 0.0\n        or confidence_value > 1.0\n    ):\n        return None\n\n    evidence_value = value.get(\n        "evidence",\n        []\n    )\n\n    evidence: list[str] = []\n\n    if isinstance(\n        evidence_value,\n        (\n            list,\n            tuple,\n        ),\n    ):\n        for item in evidence_value[\n            :32\n        ]:\n            normalized = _optional_text(\n                item,\n                max_length=2000,\n            )\n\n            if normalized is not None:\n                evidence.append(\n                    normalized\n                )\n\n    return IncidentPrimaryRCA(\n        root_cause=root_cause,\n        confidence=confidence_value,\n        evidence=tuple(\n            evidence\n        ),\n        recorded_at=recorded_at,\n    )\n\n\ndef _investigation(\n    value: Any,\n) -> InvestigationState | None:\n    if value is None:\n        return None\n\n    if isinstance(\n        value,\n        InvestigationState,\n    ):\n        return value.model_copy(\n            deep=True\n        )\n\n    if not isinstance(\n        value,\n        dict,\n    ):\n        return None\n\n    try:\n        return (\n            InvestigationState\n            .model_validate(\n                value\n            )\n        )\n\n    except Exception:\n        return None\n\n\ndef _optional_text(\n    value: Any,\n    *,\n    max_length: int,\n) -> str | None:\n    if value is None:\n        return None\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        value = str(\n            value\n        )\n\n    normalized = value.strip()\n\n    if (\n        not normalized\n        or "\\x00" in normalized\n    ):\n        return None\n\n    return normalized[\n        :max_length\n    ]\n\n\n__all__ = [\n    "IncidentAnalysisRecord",\n    "IncidentAnalysisScope",\n    "IncidentPrimaryRCA",\n    "build_incident_analysis_record",\n]\n', 'services/agent_runtime/app/investigation/store.py': 'from __future__ import annotations\n\nimport asyncio\nimport sqlite3\n\nfrom pathlib import Path\nfrom uuid import UUID\n\nfrom services.agent_runtime.app.investigation.persistence_models import (\n    IncidentAnalysisRecord,\n)\n\n\nclass IncidentAnalysisStore:\n    """\n    SQLite-backed per-Incident analysis persistence.\n\n    The store is deliberately separate from historical Agent Memory:\n    - one row is keyed by incident_id;\n    - primary RCA and Investigation can be enriched independently;\n    - merge-on-write prevents a primary-only update from deleting a previously\n      persisted Investigation, or vice versa;\n    - no Incident/Approval/Action/Verification lifecycle state is stored here.\n    """\n\n    def __init__(\n        self,\n        db_path: str | Path | None = None,\n    ) -> None:\n        self.db_path = Path(\n            db_path\n            or (\n                Path("data")\n                / "incident_analysis.db"\n            )\n        )\n\n        self.db_path.parent.mkdir(\n            parents=True,\n            exist_ok=True,\n        )\n\n        self._init_db()\n\n    def _connect(\n        self,\n    ) -> sqlite3.Connection:\n        connection = sqlite3.connect(\n            self.db_path,\n            timeout=10.0,\n        )\n\n        connection.execute(\n            "PRAGMA busy_timeout = 10000"\n        )\n\n        return connection\n\n    def _init_db(\n        self,\n    ) -> None:\n        with self._connect() as connection:\n            connection.execute(\n                "PRAGMA journal_mode = WAL"\n            )\n\n            connection.execute(\n                "PRAGMA synchronous = FULL"\n            )\n\n            connection.execute(\n                """\n                CREATE TABLE IF NOT EXISTS incident_analysis\n                (\n                    incident_id TEXT PRIMARY KEY,\n                    analysis_data TEXT NOT NULL,\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n                )\n                """\n            )\n\n    async def get(\n        self,\n        incident_id: UUID | str,\n    ) -> IncidentAnalysisRecord | None:\n        return await asyncio.to_thread(\n            self._get_sync,\n            str(\n                incident_id\n            ),\n        )\n\n    def _get_sync(\n        self,\n        incident_id: str,\n    ) -> IncidentAnalysisRecord | None:\n        with self._connect() as connection:\n            row = connection.execute(\n                """\n                SELECT analysis_data\n\n                FROM incident_analysis\n\n                WHERE incident_id = ?\n                """,\n                (\n                    incident_id,\n                ),\n            ).fetchone()\n\n        if row is None:\n            return None\n\n        return self._deserialize(\n            row[\n                0\n            ]\n        )\n\n    async def upsert(\n        self,\n        record: IncidentAnalysisRecord,\n    ) -> IncidentAnalysisRecord:\n        if not isinstance(\n            record,\n            IncidentAnalysisRecord,\n        ):\n            raise TypeError(\n                "Incident analysis record is invalid"\n            )\n\n        return await asyncio.to_thread(\n            self._upsert_sync,\n            record,\n        )\n\n    def _upsert_sync(\n        self,\n        record: IncidentAnalysisRecord,\n    ) -> IncidentAnalysisRecord:\n        connection = self._connect()\n\n        try:\n            connection.execute(\n                "BEGIN IMMEDIATE"\n            )\n\n            row = connection.execute(\n                """\n                SELECT analysis_data\n\n                FROM incident_analysis\n\n                WHERE incident_id = ?\n                """,\n                (\n                    str(\n                        record.incident_id\n                    ),\n                ),\n            ).fetchone()\n\n            if row is None:\n                merged = record\n\n                connection.execute(\n                    """\n                    INSERT INTO incident_analysis\n                    (\n                        incident_id,\n                        analysis_data,\n                        created_at,\n                        updated_at\n                    )\n\n                    VALUES\n                    (\n                        ?,\n                        ?,\n                        ?,\n                        ?\n                    )\n                    """,\n                    (\n                        str(\n                            merged.incident_id\n                        ),\n                        self._serialize(\n                            merged\n                        ),\n                        merged.created_at.isoformat(),\n                        merged.updated_at.isoformat(),\n                    ),\n                )\n\n            else:\n                current = self._deserialize(\n                    row[\n                        0\n                    ]\n                )\n\n                merged = self._merge(\n                    current=current,\n                    incoming=record,\n                )\n\n                connection.execute(\n                    """\n                    UPDATE incident_analysis\n\n                    SET\n                        analysis_data = ?,\n                        updated_at = ?\n\n                    WHERE incident_id = ?\n                    """,\n                    (\n                        self._serialize(\n                            merged\n                        ),\n                        merged.updated_at.isoformat(),\n                        str(\n                            merged.incident_id\n                        ),\n                    ),\n                )\n\n            connection.commit()\n\n            return merged\n\n        except Exception:\n            connection.rollback()\n            raise\n\n        finally:\n            connection.close()\n\n    async def list_all(\n        self,\n    ) -> list[IncidentAnalysisRecord]:\n        return await asyncio.to_thread(\n            self._list_all_sync\n        )\n\n    def _list_all_sync(\n        self,\n    ) -> list[IncidentAnalysisRecord]:\n        with self._connect() as connection:\n            rows = connection.execute(\n                """\n                SELECT analysis_data\n\n                FROM incident_analysis\n\n                ORDER BY created_at ASC, incident_id ASC\n                """\n            ).fetchall()\n\n        return [\n            self._deserialize(\n                row[\n                    0\n                ]\n            )\n            for row in rows\n        ]\n\n    @staticmethod\n    def _merge(\n        *,\n        current: IncidentAnalysisRecord,\n        incoming: IncidentAnalysisRecord,\n    ) -> IncidentAnalysisRecord:\n        if (\n            current.incident_id\n            != incoming.incident_id\n        ):\n            raise ValueError(\n                "Incident analysis identity mismatch"\n            )\n\n        primary = (\n            incoming.primary_rca\n            if (\n                incoming.primary_rca\n                is not None\n                and (\n                    current.primary_rca\n                    is None\n                    or (\n                        incoming.primary_rca\n                        .recorded_at\n                        >= current.primary_rca\n                        .recorded_at\n                    )\n                )\n            )\n            else current.primary_rca\n        )\n\n        investigation = (\n            incoming.investigation\n            if (\n                incoming.investigation\n                is not None\n                and (\n                    current.investigation\n                    is None\n                    or (\n                        incoming.investigation\n                        .updated_at\n                        >= current.investigation\n                        .updated_at\n                    )\n                )\n            )\n            else current.investigation\n        )\n\n        return IncidentAnalysisRecord(\n            incident_id=(\n                current.incident_id\n            ),\n            request_id=(\n                incoming.request_id\n                or current.request_id\n            ),\n            scope=incoming.scope,\n            primary_rca=primary,\n            investigation=investigation,\n            created_at=current.created_at,\n            updated_at=max(\n                current.updated_at,\n                incoming.updated_at,\n            ),\n        )\n\n    @staticmethod\n    def _serialize(\n        record: IncidentAnalysisRecord,\n    ) -> str:\n        return record.model_dump_json()\n\n    @staticmethod\n    def _deserialize(\n        value: str,\n    ) -> IncidentAnalysisRecord:\n        return (\n            IncidentAnalysisRecord\n            .model_validate_json(\n                value\n            )\n        )\n\n\n__all__ = [\n    "IncidentAnalysisStore",\n]\n', 'services/agent_runtime/app/conversation/runtime_provider.py': 'from __future__ import annotations\n\nfrom enum import Enum\n\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationEvidenceView,\n    ConversationHypothesisView,\n    ConversationIncidentContext,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.investigation.persistence_models import (\n    IncidentAnalysisRecord,\n)\nfrom services.agent_runtime.app.investigation.store import (\n    IncidentAnalysisStore,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\n\n\nclass RuntimeConversationIncidentContextProvider(\n    BaseConversationIncidentContextProvider\n):\n    """\n    Read-only ChatOps projection over existing authoritative Runtime stores.\n\n    Source ownership:\n    - Incident status/reason: IncidentStore\n    - primary RCA + Investigation evidence/hypotheses: IncidentAnalysisStore\n    - Approval: ApprovalService / ApprovalStore\n    - Action Execution: ActionExecutionService / ActionExecutionStore\n    - Verification: VerificationService / VerificationStore\n\n    No write method is exposed.\n    """\n\n    def __init__(\n        self,\n        *,\n        incident_store: IncidentStore,\n        analysis_store: IncidentAnalysisStore,\n        approval_service: ApprovalService,\n        action_execution_service: ActionExecutionService,\n        verification_service: VerificationService,\n    ) -> None:\n        if not isinstance(\n            incident_store,\n            IncidentStore,\n        ):\n            raise TypeError(\n                "Conversation Incident store is invalid"\n            )\n\n        if not isinstance(\n            analysis_store,\n            IncidentAnalysisStore,\n        ):\n            raise TypeError(\n                "Conversation Analysis store is invalid"\n            )\n\n        if not isinstance(\n            approval_service,\n            ApprovalService,\n        ):\n            raise TypeError(\n                "Conversation Approval service is invalid"\n            )\n\n        if not isinstance(\n            action_execution_service,\n            ActionExecutionService,\n        ):\n            raise TypeError(\n                "Conversation Action Execution service is invalid"\n            )\n\n        if not isinstance(\n            verification_service,\n            VerificationService,\n        ):\n            raise TypeError(\n                "Conversation Verification service is invalid"\n            )\n\n        self.incident_store = (\n            incident_store\n        )\n        self.analysis_store = (\n            analysis_store\n        )\n        self.approval_service = (\n            approval_service\n        )\n        self.action_execution_service = (\n            action_execution_service\n        )\n        self.verification_service = (\n            verification_service\n        )\n\n    async def get(\n        self,\n        incident_id: str,\n    ) -> ConversationIncidentContext | None:\n        incident = await self.incident_store.get(\n            incident_id\n        )\n\n        if incident is None:\n            return None\n\n        analysis = await self.analysis_store.get(\n            incident_id\n        )\n\n        approvals = await (\n            self.approval_service\n            .list_by_incident(\n                incident_id\n            )\n        )\n\n        executions = await (\n            self.action_execution_service\n            .list_by_incident(\n                incident_id\n            )\n        )\n\n        verifications = await (\n            self.verification_service\n            .list_by_incident(\n                incident_id\n            )\n        )\n\n        latest_approval = (\n            approvals[\n                -1\n            ]\n            if approvals\n            else None\n        )\n\n        latest_execution = (\n            executions[\n                -1\n            ]\n            if executions\n            else None\n        )\n\n        latest_verification = (\n            verifications[\n                -1\n            ]\n            if verifications\n            else None\n        )\n\n        (\n            root_cause,\n            root_cause_confidence,\n            rca_source,\n        ) = self._root_cause(\n            analysis\n        )\n\n        evidence = self._evidence(\n            analysis\n        )\n\n        hypotheses = (\n            self._hypotheses(\n                analysis\n            )\n        )\n\n        title = self._title(\n            analysis\n        )\n\n        recommended_action = None\n        action_risk = None\n        approval_status = None\n\n        if latest_approval is not None:\n            action = (\n                latest_approval.action\n            )\n\n            recommended_action = (\n                self._enum_value(\n                    action.type\n                )\n                + " -> "\n                + action.target\n            )\n\n            action_risk = (\n                self._enum_value(\n                    action.risk\n                )\n            )\n\n            approval_status = (\n                self._enum_value(\n                    latest_approval.status\n                )\n            )\n\n        action_execution_status = (\n            self._enum_value(\n                latest_execution.status\n            )\n            if latest_execution\n            is not None\n            else None\n        )\n\n        verification_status = (\n            self._enum_value(\n                latest_verification.status\n            )\n            if latest_verification\n            is not None\n            else None\n        )\n\n        metadata = {\n            "rca_source": (\n                rca_source\n            ),\n            "analysis_available": (\n                analysis is not None\n            ),\n            "investigation_status": (\n                self._enum_value(\n                    analysis.investigation.status\n                )\n                if (\n                    analysis is not None\n                    and analysis.investigation\n                    is not None\n                )\n                else None\n            ),\n        }\n\n        return ConversationIncidentContext(\n            incident_id=str(\n                incident.id\n            ),\n            status=self._enum_value(\n                incident.status\n            ),\n            title=title,\n            summary=incident.reason,\n            root_cause=root_cause,\n            root_cause_confidence=(\n                root_cause_confidence\n            ),\n            evidence=evidence,\n            hypotheses=hypotheses,\n            recommended_action=(\n                recommended_action\n            ),\n            action_risk=action_risk,\n            approval_status=(\n                approval_status\n            ),\n            action_execution_status=(\n                action_execution_status\n            ),\n            verification_status=(\n                verification_status\n            ),\n            metadata=metadata,\n        )\n\n    @classmethod\n    def _root_cause(\n        cls,\n        analysis: (\n            IncidentAnalysisRecord\n            | None\n        ),\n    ) -> tuple[\n        str | None,\n        float | None,\n        str | None,\n    ]:\n        if analysis is None:\n            return (\n                None,\n                None,\n                None,\n            )\n\n        if (\n            analysis.primary_rca\n            is not None\n        ):\n            return (\n                analysis.primary_rca\n                .root_cause,\n                analysis.primary_rca\n                .confidence,\n                "planner_rca",\n            )\n\n        investigation = (\n            analysis.investigation\n        )\n\n        if (\n            investigation is not None\n            and investigation.conclusion\n            is not None\n        ):\n            return (\n                investigation.conclusion\n                .root_cause,\n                investigation.conclusion\n                .confidence,\n                "investigation_shadow",\n            )\n\n        return (\n            None,\n            None,\n            None,\n        )\n\n    @classmethod\n    def _evidence(\n        cls,\n        analysis: (\n            IncidentAnalysisRecord\n            | None\n        ),\n    ) -> tuple[\n        ConversationEvidenceView,\n        ...,\n    ]:\n        if analysis is None:\n            return ()\n\n        items = []\n\n        if (\n            analysis.primary_rca\n            is not None\n        ):\n            for index, summary in enumerate(\n                analysis.primary_rca.evidence,\n                start=1,\n            ):\n                items.append(\n                    ConversationEvidenceView(\n                        evidence_id=(\n                            "planner-rca-"\n                            + str(\n                                index\n                            )\n                        ),\n                        source="planner_rca",\n                        summary=summary,\n                        trusted=False,\n                        cluster_verified=False,\n                    )\n                )\n\n        if (\n            analysis.investigation\n            is not None\n        ):\n            for item in (\n                analysis.investigation\n                .evidence\n            ):\n                items.append(\n                    ConversationEvidenceView(\n                        evidence_id=(\n                            item.evidence_id\n                        ),\n                        source=item.source,\n                        summary=(\n                            cls._evidence_summary(\n                                item\n                            )\n                        ),\n                        trusted=item.trusted,\n                        cluster_verified=(\n                            item.cluster_verified\n                        ),\n                    )\n                )\n\n        return tuple(\n            items\n        )\n\n    @staticmethod\n    def _hypotheses(\n        analysis: (\n            IncidentAnalysisRecord\n            | None\n        ),\n    ) -> tuple[\n        ConversationHypothesisView,\n        ...,\n    ]:\n        if (\n            analysis is None\n            or analysis.investigation\n            is None\n        ):\n            return ()\n\n        return tuple(\n            ConversationHypothesisView(\n                cause=item.cause,\n                confidence=item.confidence,\n            )\n            for item in (\n                analysis.investigation\n                .hypotheses\n            )\n        )\n\n    @staticmethod\n    def _title(\n        analysis: (\n            IncidentAnalysisRecord\n            | None\n        ),\n    ) -> str | None:\n        if analysis is None:\n            return None\n\n        scope = analysis.scope\n\n        if scope.resource:\n            return (\n                scope.resource\n                + " / "\n                + scope.alert_name\n            )\n\n        return scope.alert_name\n\n    @staticmethod\n    def _evidence_summary(\n        item,\n    ) -> str:\n        if not item.success:\n            return (\n                item.probe.value\n                + ": "\n                + (\n                    item.error_code\n                    or "collection_failed"\n                )\n            )\n\n        facts = []\n\n        for key in sorted(\n            item.facts\n        ):\n            value = item.facts[\n                key\n            ]\n\n            facts.append(\n                str(\n                    key\n                )[\n                    :128\n                ]\n                + "="\n                + str(\n                    value\n                )[\n                    :256\n                ]\n            )\n\n        if not facts:\n            return (\n                item.probe.value\n                + ": collected"\n            )\n\n        return (\n            item.probe.value\n            + ": "\n            + ", ".join(\n                facts\n            )[\n                :1600\n            ]\n        )\n\n    @staticmethod\n    def _enum_value(\n        value,\n    ) -> str:\n        if isinstance(\n            value,\n            Enum,\n        ):\n            return str(\n                value.value\n            )\n\n        return str(\n            value\n        )\n\n\n__all__ = [\n    "RuntimeConversationIncidentContextProvider",\n]\n', 'services/agent_runtime/app/runtime/runtime.py': 'from copy import deepcopy\nfrom typing import Any\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.connection_factory import (\n    create_prometheus_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbe,\n    ProductionReadinessLiveProbeError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.persistence_models import (\n    build_incident_analysis_record,\n)\nfrom services.agent_runtime.app.investigation.store import (\n    IncidentAnalysisStore,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.runtime_provider import (\n    RuntimeConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            prometheus_cluster_registry\n            is None\n        ):\n            self.prometheus_cluster_registry = (\n                create_prometheus_cluster_registry()\n            )\n        else:\n            self.prometheus_cluster_registry = (\n                prometheus_cluster_registry\n            )\n\n        self.cluster_verified_evidence_required = (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n        if (\n            self.investigation_coordinator\n            is not None\n        ):\n            self.investigation_coordinator.require_cluster_verified_evidence = (\n                self.cluster_verified_evidence_required\n            )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        readiness_registry_types_valid = (\n            (\n                self.kubernetes_cluster_registry\n                is None\n                or isinstance(\n                    self.kubernetes_cluster_registry,\n                    KubernetesClusterRegistry,\n                )\n            )\n            and (\n                self.prometheus_cluster_registry\n                is None\n                or isinstance(\n                    self.prometheus_cluster_registry,\n                    PrometheusClusterRegistry,\n                )\n            )\n        )\n\n        self.production_multi_cluster_readiness = None\n        self.production_multi_cluster_coverage = None\n\n        self.production_multi_cluster_live_readiness = None\n\n        if readiness_registry_types_valid:\n            self.production_multi_cluster_readiness = (\n                ProductionMultiClusterReadinessGate(\n                    kubernetes_cluster_registry=(\n                        self.kubernetes_cluster_registry\n                    ),\n                    prometheus_cluster_registry=(\n                        self.prometheus_cluster_registry\n                    ),\n                    tools=self.tools,\n                    strict_evidence_required=(\n                        self.cluster_verified_evidence_required\n                    ),\n                )\n            )\n\n            self.production_multi_cluster_coverage = (\n                self.production_multi_cluster_readiness\n                .evaluate_all()\n            )\n\n            if (\n                self.production_multi_cluster_readiness\n                .applicable\n            ):\n                self.production_multi_cluster_live_readiness = (\n                    ProductionReadinessLiveProbe(\n                        readiness_gate=(\n                            self.production_multi_cluster_readiness\n                        ),\n                        tools=self.tools,\n                    )\n                )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools,\n            require_cluster_verified_evidence=(\n                self.cluster_verified_evidence_required\n            ),\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        self.incident_analysis_store = (\n            IncidentAnalysisStore()\n        )\n\n        self.conversation_context_provider = (\n            RuntimeConversationIncidentContextProvider(\n                incident_store=self.incident_store,\n                analysis_store=(\n                    self.incident_analysis_store\n                ),\n                approval_service=self.approval,\n                action_execution_service=(\n                    self.action_execution_service\n                ),\n                verification_service=(\n                    self.verification\n                ),\n            )\n        )\n\n        self.conversation = ConversationOrchestrator(\n            provider=(\n                self.conversation_context_provider\n            )\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n            "production_multi_cluster_readiness",\n            "production_multi_cluster_live_readiness",\n            "incident_analysis_persistence",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Persist the authoritative Planner RCA immediately after the primary\n        # workflow. This remains weaker than the Pipeline itself: persistence\n        # failure is sanitized in metadata and cannot change Incident state.\n        await self._persist_incident_analysis(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Merge the bounded Investigation snapshot into the same per-Incident\n        # analysis record. Historical Memory remains independent.\n        await self._persist_incident_analysis(\n            context\n        )\n\n        return results\n\n    async def _persist_incident_analysis(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort durable analysis projection for ChatOps follow-up.\n\n        This method never mutates Incident, Approval, Action or Verification.\n        A persistence fault is recorded as a bounded failure code and remains\n        weaker than the authoritative PlannerPipeline.\n        """\n\n        store = getattr(\n            self,\n            "incident_analysis_store",\n            None,\n        )\n\n        if not isinstance(\n            store,\n            IncidentAnalysisStore,\n        ):\n            return\n\n        metadata = getattr(\n            context,\n            "metadata",\n            None,\n        )\n\n        if not isinstance(\n            metadata,\n            dict,\n        ):\n            return\n\n        incident = getattr(\n            context,\n            "incident",\n            None,\n        )\n\n        incident_id = getattr(\n            incident,\n            "id",\n            None,\n        )\n\n        if incident_id is None:\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "skipped",\n                "reason": "incident_identity_missing",\n            }\n\n            return\n\n        try:\n            existing = await store.get(\n                incident_id\n            )\n\n            record = build_incident_analysis_record(\n                incident_id=incident_id,\n                event=context.event,\n                request_id=context.request_id,\n                primary_rca=(\n                    context.variables.get(\n                        "rca"\n                    )\n                    if isinstance(\n                        context.variables,\n                        dict,\n                    )\n                    else None\n                ),\n                investigation_snapshot=(\n                    metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                existing=existing,\n            )\n\n            persisted = await store.upsert(\n                record\n            )\n\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "persisted",\n                "incident_id": str(\n                    persisted.incident_id\n                ),\n                "primary_rca": (\n                    persisted.primary_rca\n                    is not None\n                ),\n                "investigation": (\n                    persisted.investigation\n                    is not None\n                ),\n            }\n\n        except Exception as exc:\n            metadata[\n                "incident_analysis_persistence"\n            ] = {\n                "schema_version": "v1",\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[\n                        :256\n                    ]\n                ),\n            }\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_production_multi_cluster_live_readiness(\n        self,\n        context: AgentContext,\n        *,\n        acknowledgement: str,\n        reason: str,\n    ) -> dict[str, Any]:\n        """\n        Explicit bounded live-read production readiness proof.\n\n        This method is never called automatically by execute() or Runtime\n        startup. It records only a sanitized readiness snapshot.\n        """\n\n        if not isinstance(context, AgentContext):\n            raise TypeError(\n                "AgentRuntime live readiness requires AgentContext"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime live readiness requires shared Runtime tools"\n            )\n\n        probe = getattr(\n            self,\n            "production_multi_cluster_live_readiness",\n            None,\n        )\n\n        if probe is None:\n            raise ProductionReadinessLiveProbeError(\n                "AgentRuntime production live readiness is unavailable"\n            )\n\n        report = await probe.probe_event(\n            context.event,\n            acknowledgement=acknowledgement,\n            reason=reason,\n        )\n\n        snapshot = report.snapshot()\n\n        context.metadata[\n            "production_multi_cluster_live_readiness"\n        ] = deepcopy(snapshot)\n\n        return snapshot\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        if getattr(\n            self,\n            "cluster_verified_evidence_required",\n            False,\n        ):\n            if (\n                self.production_multi_cluster_readiness\n                is None\n            ):\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow readiness proof is unavailable"\n                )\n\n            readiness = (\n                self.production_multi_cluster_readiness\n                .evaluate_event(\n                    context.event\n                )\n            )\n\n            context.metadata[\n                "production_multi_cluster_readiness"\n            ] = readiness.snapshot()\n\n            if not readiness.ready:\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow read coverage is not ready"\n                )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n', 'services/agent_runtime/app/conversation/models.py': 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom enum import Enum\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field, field_validator\n\n\nclass ConversationIntent(str, Enum):\n    STATUS = "status"\n    RCA = "rca"\n    EVIDENCE = "evidence"\n    NEXT_STEP = "next_step"\n    VERIFICATION = "verification"\n    APPROVE = "approve"\n    REJECT = "reject"\n    REMEDIATE = "remediate"\n    HELP = "help"\n    UNKNOWN = "unknown"\n\n\nclass ConversationReplyMode(str, Enum):\n    READ_ONLY = "read_only"\n    WRITE_ACTION_REQUIRED = "write_action_required"\n    NEEDS_INCIDENT = "needs_incident"\n    INCIDENT_NOT_FOUND = "incident_not_found"\n\n\nclass ConversationTurnRequest(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    text: str\n    incident_id: str | None = None\n\n    @field_validator(\n        "conversation_id",\n        "text",\n        "incident_id",\n        mode="before",\n    )\n    @classmethod\n    def validate_text(cls, value, info):\n        if value is None and info.field_name == "incident_id":\n            return None\n\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or "\\x00" in value\n        ):\n            raise ValueError(\n                f"{info.field_name} is invalid"\n            )\n\n        limit = 4096 if info.field_name == "text" else 256\n        if len(value) > limit:\n            raise ValueError(\n                f"{info.field_name} is too long"\n            )\n\n        return value\n\n\nclass ConversationEvidenceView(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    evidence_id: str\n    source: str\n    summary: str\n    trusted: bool = False\n    cluster_verified: bool = False\n\n\nclass ConversationHypothesisView(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    cause: str\n    confidence: float = Field(ge=0.0, le=1.0)\n\n\nclass ConversationIncidentContext(BaseModel):\n    """\n    Stable ChatOps-facing incident view.\n\n    Existing persistence remains authoritative. The conversation layer receives\n    only a read-only projection assembled by a provider adapter.\n    """\n\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    incident_id: str\n    status: str\n    title: str | None = None\n    summary: str | None = None\n\n    root_cause: str | None = None\n    root_cause_confidence: float | None = Field(\n        default=None,\n        ge=0.0,\n        le=1.0,\n    )\n\n    evidence: tuple[ConversationEvidenceView, ...] = ()\n    hypotheses: tuple[ConversationHypothesisView, ...] = ()\n\n    recommended_action: str | None = None\n    action_risk: str | None = None\n    approval_status: str | None = None\n    action_execution_status: str | None = None\n    verification_status: str | None = None\n\n    metadata: dict[str, Any] = Field(default_factory=dict)\n\n\nclass ConversationReplySection(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    key: str\n    title: str\n    lines: tuple[str, ...] = ()\n\n\nclass ConversationReplyPlan(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    incident_id: str | None\n    intent: ConversationIntent\n    mode: ConversationReplyMode\n\n    sections: tuple[ConversationReplySection, ...] = ()\n    suggested_actions: tuple[str, ...] = ()\n    write_operation: str | None = None\n\n    created_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n\n\nclass ConversationSession(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n\n    conversation_id: str\n    incident_id: str | None = None\n    last_intent: ConversationIntent | None = None\n    turn_count: int = Field(default=0, ge=0)\n    created_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n', 'services/agent_runtime/app/conversation/provider.py': 'from __future__ import annotations\n\nfrom abc import ABC, abstractmethod\n\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n)\n\n\nclass BaseConversationIncidentContextProvider(ABC):\n    """\n    Read-only bridge into existing Runtime persistence.\n\n    Mutation methods are deliberately absent.\n    """\n\n    @abstractmethod\n    async def get(\n        self,\n        incident_id: str,\n    ) -> ConversationIncidentContext | None:\n        raise NotImplementedError\n\n\nclass DictConversationIncidentContextProvider(\n    BaseConversationIncidentContextProvider\n):\n    def __init__(\n        self,\n        items: dict[\n            str,\n            ConversationIncidentContext,\n        ] | None = None,\n    ) -> None:\n        self._items = dict(items or {})\n\n    async def get(\n        self,\n        incident_id: str,\n    ) -> ConversationIncidentContext | None:\n        return self._items.get(incident_id)\n', 'services/agent_runtime/app/conversation/orchestrator.py': 'from __future__ import annotations\n\nfrom services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n)\n\n\nclass ConversationOrchestrator:\n    """\n    Channel-neutral ChatOps core.\n\n    v1 binds a conversation to an Incident, classifies intent, reads a stable\n    Incident projection, and returns a structured reply plan.\n\n    It has no direct Action/Approval/Verification write authority.\n    """\n\n    _WRITE_INTENTS = {\n        ConversationIntent.APPROVE,\n        ConversationIntent.REJECT,\n        ConversationIntent.REMEDIATE,\n    }\n\n    def __init__(\n        self,\n        *,\n        provider: BaseConversationIncidentContextProvider,\n        sessions: InMemoryConversationSessionStore | None = None,\n        classifier: (\n            DeterministicConversationIntentClassifier\n            | None\n        ) = None,\n    ) -> None:\n        if not isinstance(\n            provider,\n            BaseConversationIncidentContextProvider,\n        ):\n            raise TypeError(\n                "Conversation context provider is invalid"\n            )\n\n        self.provider = provider\n        self.sessions = (\n            sessions\n            or InMemoryConversationSessionStore()\n        )\n        self.classifier = (\n            classifier\n            or DeterministicConversationIntentClassifier()\n        )\n\n    async def handle(\n        self,\n        request: ConversationTurnRequest,\n    ) -> ConversationReplyPlan:\n        if not isinstance(\n            request,\n            ConversationTurnRequest,\n        ):\n            raise TypeError(\n                "Conversation request is invalid"\n            )\n\n        intent = self.classifier.classify(\n            request.text\n        )\n\n        current = await self.sessions.get(\n            request.conversation_id\n        )\n\n        incident_id = (\n            request.incident_id\n            or (\n                current.incident_id\n                if current is not None\n                else None\n            )\n        )\n\n        await self.sessions.update(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=intent,\n        )\n\n        if intent == ConversationIntent.HELP:\n            return self._help(\n                request,\n                incident_id,\n            )\n\n        if incident_id is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=None,\n                intent=intent,\n                mode=ConversationReplyMode.NEEDS_INCIDENT,\n                sections=(\n                    ConversationReplySection(\n                        key="incident_binding",\n                        title="需要 Incident",\n                        lines=(\n                            "请先绑定一个 Incident，再继续查询或操作。",\n                        ),\n                    ),\n                ),\n                suggested_actions=(\n                    "bind_incident",\n                    "help",\n                ),\n            )\n\n        context = await self.provider.get(\n            incident_id\n        )\n\n        if context is None:\n            return ConversationReplyPlan(\n                conversation_id=request.conversation_id,\n                incident_id=incident_id,\n                intent=intent,\n                mode=(\n                    ConversationReplyMode\n                    .INCIDENT_NOT_FOUND\n                ),\n                sections=(\n                    ConversationReplySection(\n                        key="incident",\n                        title="Incident 不存在",\n                        lines=(\n                            f"未找到 Incident {incident_id}。",\n                        ),\n                    ),\n                ),\n                suggested_actions=("bind_incident",),\n            )\n\n        if intent in self._WRITE_INTENTS:\n            return self._write_intent(\n                request=request,\n                context=context,\n                intent=intent,\n            )\n\n        if intent == ConversationIntent.STATUS:\n            return self._status(request, context)\n\n        if intent == ConversationIntent.RCA:\n            return self._rca(request, context)\n\n        if intent == ConversationIntent.EVIDENCE:\n            return self._evidence(request, context)\n\n        if intent == ConversationIntent.NEXT_STEP:\n            return self._next_step(request, context)\n\n        if intent == ConversationIntent.VERIFICATION:\n            return self._verification(request, context)\n\n        return self._unknown(request, context)\n\n    @staticmethod\n    def _base(\n        request,\n        context,\n        *,\n        intent,\n        sections,\n        suggested_actions=(),\n    ):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=tuple(sections),\n            suggested_actions=tuple(suggested_actions),\n        )\n\n    def _status(self, request, context):\n        lines = [f"状态: {context.status}"]\n\n        if context.title:\n            lines.append(f"事件: {context.title}")\n\n        if context.approval_status:\n            lines.append(\n                f"审批: {context.approval_status}"\n            )\n\n        if context.action_execution_status:\n            lines.append(\n                "执行: "\n                + context.action_execution_status\n            )\n\n        if context.verification_status:\n            lines.append(\n                f"验证: {context.verification_status}"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.STATUS,\n            sections=(\n                ConversationReplySection(\n                    key="status",\n                    title="Incident 状态",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _rca(self, request, context):\n        if context.root_cause:\n            confidence = (\n                f"{context.root_cause_confidence:.0%}"\n                if context.root_cause_confidence is not None\n                else "unknown"\n            )\n            lines = (\n                f"根因: {context.root_cause}",\n                f"置信度: {confidence}",\n            )\n        elif context.hypotheses:\n            best = max(\n                context.hypotheses,\n                key=lambda item: item.confidence,\n            )\n            lines = (\n                "当前尚无最终根因。",\n                f"最高假设: {best.cause}",\n                f"假设置信度: {best.confidence:.0%}",\n            )\n        else:\n            lines = (\n                "当前还没有足够证据形成 RCA。",\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.RCA,\n            sections=(\n                ConversationReplySection(\n                    key="rca",\n                    title="根因分析",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_evidence",\n                "what_next",\n            ),\n        )\n\n    def _evidence(self, request, context):\n        if not context.evidence:\n            lines = ("当前还没有可展示的证据。",)\n        else:\n            lines = tuple(\n                (\n                    ("✓ " if item.trusted else "△ ")\n                    + item.summary\n                    + f" [{item.source}]"\n                )\n                for item in context.evidence\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.EVIDENCE,\n            sections=(\n                ConversationReplySection(\n                    key="evidence",\n                    title="证据",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=(\n                "show_rca",\n                "what_next",\n            ),\n        )\n\n    def _next_step(self, request, context):\n        lines = []\n\n        if context.recommended_action:\n            lines.append(\n                f"建议: {context.recommended_action}"\n            )\n\n            if context.action_risk:\n                lines.append(\n                    f"风险: {context.action_risk}"\n                )\n\n            if context.approval_status:\n                lines.append(\n                    f"审批状态: {context.approval_status}"\n                )\n        elif context.root_cause:\n            lines.append(\n                "根因已经形成，但当前没有可执行修复建议。"\n            )\n        else:\n            lines.append(\n                "继续收集证据并缩小根因假设。"\n            )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.NEXT_STEP,\n            sections=(\n                ConversationReplySection(\n                    key="next_step",\n                    title="下一步",\n                    lines=tuple(lines),\n                ),\n            ),\n            suggested_actions=(\n                (\n                    "request_remediation"\n                    if context.recommended_action\n                    else "show_evidence"\n                ),\n            ),\n        )\n\n    def _verification(self, request, context):\n        lines = (\n            (\n                f"验证状态: {context.verification_status}"\n                if context.verification_status\n                else "当前还没有 Verification 结果。"\n            ),\n        )\n\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.VERIFICATION,\n            sections=(\n                ConversationReplySection(\n                    key="verification",\n                    title="恢复验证",\n                    lines=lines,\n                ),\n            ),\n            suggested_actions=("show_status",),\n        )\n\n    @staticmethod\n    def _write_intent(\n        *,\n        request,\n        context,\n        intent,\n    ):\n        operation = {\n            ConversationIntent.APPROVE: "approval.approve",\n            ConversationIntent.REJECT: "approval.reject",\n            ConversationIntent.REMEDIATE: "remediation.request",\n        }[intent]\n\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=context.incident_id,\n            intent=intent,\n            mode=(\n                ConversationReplyMode\n                .WRITE_ACTION_REQUIRED\n            ),\n            sections=(\n                ConversationReplySection(\n                    key="write_boundary",\n                    title="需要认证写操作",\n                    lines=(\n                        "Conversation Orchestrator v1 不直接执行写操作。",\n                        "该意图必须通过现有认证、RBAC、Approval/Action 边界继续。",\n                    ),\n                ),\n            ),\n            suggested_actions=(\n                "open_authenticated_write_flow",\n                "show_status",\n            ),\n            write_operation=operation,\n        )\n\n    @staticmethod\n    def _help(request, incident_id):\n        return ConversationReplyPlan(\n            conversation_id=request.conversation_id,\n            incident_id=incident_id,\n            intent=ConversationIntent.HELP,\n            mode=ConversationReplyMode.READ_ONLY,\n            sections=(\n                ConversationReplySection(\n                    key="help",\n                    title="可以这样问",\n                    lines=(\n                        "现在状态怎么样？",\n                        "根因是什么？",\n                        "有哪些证据？",\n                        "下一步怎么办？",\n                        "验证结果怎么样？",\n                        "帮我修一下。",\n                        "批准执行。",\n                    ),\n                ),\n            ),\n        )\n\n    def _unknown(self, request, context):\n        return self._base(\n            request,\n            context,\n            intent=ConversationIntent.UNKNOWN,\n            sections=(\n                ConversationReplySection(\n                    key="unknown",\n                    title="我还不能确定你的意图",\n                    lines=(\n                        "可以询问状态、根因、证据、下一步或验证结果。",\n                    ),\n                ),\n            ),\n            suggested_actions=("help",),\n        )\n', 'services/agent_runtime/app/conversation/__init__.py': 'from services.agent_runtime.app.conversation.classifier import (\n    DeterministicConversationIntentClassifier,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationEvidenceView,\n    ConversationHypothesisView,\n    ConversationIncidentContext,\n    ConversationIntent,\n    ConversationReplyMode,\n    ConversationReplyPlan,\n    ConversationReplySection,\n    ConversationSession,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.conversation.orchestrator import (\n    ConversationOrchestrator,\n)\nfrom services.agent_runtime.app.conversation.provider import (\n    BaseConversationIncidentContextProvider,\n    DictConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.runtime_provider import (\n    RuntimeConversationIncidentContextProvider,\n)\nfrom services.agent_runtime.app.conversation.store import (\n    InMemoryConversationSessionStore,\n)\n\n\n__all__ = [\n    "BaseConversationIncidentContextProvider",\n    "ConversationEvidenceView",\n    "ConversationHypothesisView",\n    "ConversationIncidentContext",\n    "ConversationIntent",\n    "ConversationOrchestrator",\n    "ConversationReplyMode",\n    "ConversationReplyPlan",\n    "ConversationReplySection",\n    "ConversationSession",\n    "ConversationTurnRequest",\n    "DeterministicConversationIntentClassifier",\n    "DictConversationIncidentContextProvider",\n    "InMemoryConversationSessionStore",\n    "RuntimeConversationIncidentContextProvider",\n]\n', 'services/agent_runtime/tests/test_incident_analysis_conversation_context.py': 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom uuid import uuid4\n\nimport pytest\n\nfrom common.domain.event import (\n    Header,\n    Resource,\n    Signal,\n    StandardEvent,\n)\nfrom common.domain.event.enums import (\n    EventSource,\n    ResourceKind,\n    Severity,\n    SignalType,\n)\n\nfrom services.agent_runtime.app.action.models import (\n    ActionPlan,\n    ActionType,\n)\nfrom services.agent_runtime.app.conversation.models import (\n    ConversationReplyMode,\n    ConversationTurnRequest,\n)\nfrom services.agent_runtime.app.incident.enums import (\n    IncidentStatus,\n)\nfrom services.agent_runtime.app.incident.state import (\n    IncidentState,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.persistence_models import (\n    build_incident_analysis_record,\n)\nfrom services.agent_runtime.app.investigation.store import (\n    IncidentAnalysisStore,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    11,\n    0,\n    tzinfo=UTC,\n)\n\n\ndef event() -> StandardEvent:\n    return StandardEvent(\n        header=Header(\n            source=EventSource.ALERTMANAGER,\n            occurred_at=NOW,\n        ),\n        signal=Signal(\n            type=SignalType.ALERT,\n            name="PodOOMKilled",\n            severity=Severity.CRITICAL,\n            message="pod was OOMKilled",\n        ),\n        resources=[\n            Resource(\n                kind=ResourceKind.POD,\n                name="checkout-api-abc123",\n                namespace="checkout",\n                cluster="prod-us-03",\n            )\n        ],\n    )\n\n\ndef investigation_state() -> InvestigationState:\n    evidence = EvidenceItem(\n        evidence_id="ev-pod",\n        probe=(\n            InvestigationProbe\n            .KUBERNETES_POD_STATE\n        ),\n        source="kubernetes",\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        cluster="prod-us-03",\n        cluster_verified=True,\n        facts={\n            "oom_killed": True,\n            "restart_count": 7,\n        },\n    )\n\n    hypothesis = IncidentHypothesis(\n        hypothesis_id="h-memory",\n        cause=(\n            "Container exceeded its memory limit"\n        ),\n        confidence=0.91,\n        supporting_evidence_ids=[\n            evidence.evidence_id\n        ],\n        missing_evidence=[],\n    )\n\n    return InvestigationState(\n        investigation_id="inv-1001",\n        status=InvestigationStatus.CONCLUDED,\n        scope=InvestigationScope(\n            alert_name="PodOOMKilled",\n            alert_message="pod was OOMKilled",\n            event_occurred_at=NOW,\n            resource="checkout-api-abc123",\n            namespace="checkout",\n            cluster="prod-us-03",\n        ),\n        started_at=NOW,\n        updated_at=NOW,\n        iteration_count=2,\n        tool_call_count=1,\n        hypotheses=[\n            hypothesis\n        ],\n        evidence=[\n            evidence\n        ],\n        attempted_probes=[\n            InvestigationProbe\n            .KUBERNETES_POD_STATE\n        ],\n        decision_summaries=[\n            "Trusted pod evidence supports memory exhaustion"\n        ],\n        stop_reason=(\n            InvestigationStopReason\n            .SUFFICIENT_EVIDENCE\n        ),\n        conclusion=InvestigationConclusion(\n            root_cause=(\n                "Container exceeded its memory limit"\n            ),\n            confidence=0.91,\n            evidence_ids=[\n                evidence.evidence_id\n            ],\n        ),\n    )\n\n\n@pytest.mark.asyncio\nasync def test_incident_analysis_store_survives_restart_and_merges_sources(\n    tmp_path,\n):\n    db = (\n        tmp_path\n        / "incident_analysis.db"\n    )\n\n    incident_id = uuid4()\n\n    first = build_incident_analysis_record(\n        incident_id=incident_id,\n        event=event(),\n        request_id="req-1001",\n        primary_rca={\n            "root_cause": (\n                "Deployment reduced the memory limit"\n            ),\n            "confidence": 0.96,\n            "evidence": [\n                "OOMKilled",\n                "limit changed 1Gi -> 512Mi",\n            ],\n        },\n        now=NOW,\n    )\n\n    store_one = IncidentAnalysisStore(\n        db_path=db\n    )\n\n    await store_one.upsert(\n        first\n    )\n\n    # A new store instance simulates a Runtime restart.\n    store_two = IncidentAnalysisStore(\n        db_path=db\n    )\n\n    restarted = await store_two.get(\n        incident_id\n    )\n\n    assert restarted is not None\n    assert restarted.primary_rca is not None\n    assert (\n        restarted.primary_rca.root_cause\n        == "Deployment reduced the memory limit"\n    )\n    assert restarted.investigation is None\n\n    enriched = build_incident_analysis_record(\n        incident_id=incident_id,\n        event=event(),\n        request_id="req-1001",\n        investigation_snapshot=(\n            investigation_state()\n        ),\n        existing=restarted,\n        now=NOW,\n    )\n\n    await store_two.upsert(\n        enriched\n    )\n\n    store_three = IncidentAnalysisStore(\n        db_path=db\n    )\n\n    final = await store_three.get(\n        incident_id\n    )\n\n    assert final is not None\n    assert final.primary_rca is not None\n    assert final.investigation is not None\n    assert (\n        final.investigation.conclusion\n        is not None\n    )\n    assert (\n        final.investigation.evidence[\n            0\n        ].cluster_verified\n        is True\n    )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_persists_analysis_and_conversation_reloads_after_restart(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    for name in (\n        "PROMETHEUS_URL",\n        "KUBERNETES_API_URL",\n        "KUBERNETES_SERVICE_HOST",\n        "KUBERNETES_SERVICE_PORT",\n        "KUBERNETES_SERVICE_PORT_HTTPS",\n    ):\n        monkeypatch.delenv(\n            name,\n            raising=False,\n        )\n\n    monkeypatch.setenv(\n        "PROMETHEUS_ALLOW_MOCK_FALLBACK",\n        "true",\n    )\n\n    monkeypatch.setenv(\n        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",\n        "true",\n    )\n\n    runtime_one = AgentRuntime()\n\n    incident = IncidentState(\n        status=IncidentStatus.CONFIRMED,\n        reason="RCA complete; awaiting approval",\n    )\n\n    incident = await (\n        runtime_one.incident_store.save(\n            incident\n        )\n    )\n\n    context = AgentContext(\n        request_id="req-chatops-1",\n        event=event(),\n        incident=incident,\n        memory=runtime_one.memory,\n        tools=runtime_one.tools,\n        skills=runtime_one.skills,\n        approval=runtime_one.approval,\n        metadata={\n            "investigation_shadow": (\n                investigation_state()\n                .model_dump(\n                    mode="json"\n                )\n            )\n        },\n        variables={\n            "rca": {\n                "root_cause": (\n                    "Deployment reduced the memory limit"\n                ),\n                "confidence": 0.96,\n                "evidence": [\n                    "OOMKilled",\n                    "limit changed 1Gi -> 512Mi",\n                ],\n            }\n        },\n    )\n\n    await runtime_one._persist_incident_analysis(\n        context\n    )\n\n    plan = ActionPlan(\n        type=ActionType.INCREASE_MEMORY_LIMIT,\n        target="checkout-api",\n        namespace="checkout",\n        cluster="prod-us-03",\n    )\n\n    approval = await (\n        runtime_one.approval\n        .create_approval(\n            action=plan,\n            reason="medium risk",\n            incident_id=incident.id,\n        )\n    )\n\n    approved = await (\n        runtime_one.approval.approve(\n            approval.id,\n            operator_id="operator-1",\n            idempotency_key="approval-key-1",\n            reason="approved for test",\n        )\n    )\n\n    claim = await (\n        runtime_one.action_execution_service\n        .claim(\n            approval_id=approved.id,\n            operator_id="operator-1",\n            idempotency_key="execution-key-1",\n            action=approved.action,\n            incident_id=incident.id,\n        )\n    )\n\n    assert claim.created is True\n\n    verification = await (\n        runtime_one.verification\n        .create_verification(\n            incident_id=incident.id,\n            action=plan.type.value,\n            target=plan.target,\n            metadata={\n                "cluster": plan.cluster,\n                "namespace": plan.namespace,\n            },\n        )\n    )\n\n    assert (\n        verification.status.value\n        == "pending"\n    )\n\n    # Recreate the Runtime against the same SQLite files.\n    runtime_two = AgentRuntime()\n\n    projected = await (\n        runtime_two\n        .conversation_context_provider\n        .get(\n            str(\n                incident.id\n            )\n        )\n    )\n\n    assert projected is not None\n    assert projected.status == "confirmed"\n    assert (\n        projected.root_cause\n        == "Deployment reduced the memory limit"\n    )\n    assert (\n        projected.root_cause_confidence\n        == pytest.approx(\n            0.96\n        )\n    )\n    assert projected.approval_status == "approved"\n    assert (\n        projected.action_execution_status\n        == "running"\n    )\n    assert (\n        projected.verification_status\n        == "pending"\n    )\n\n    assert any(\n        item.cluster_verified\n        for item in projected.evidence\n    )\n\n    assert (\n        projected.hypotheses[\n            0\n        ].confidence\n        == pytest.approx(\n            0.91\n        )\n    )\n\n    reply = await runtime_two.conversation.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-thread-1",\n            incident_id=str(\n                incident.id\n            ),\n            text="现在状态怎么样？",\n        )\n    )\n\n    assert reply.mode == (\n        ConversationReplyMode.READ_ONLY\n    )\n\n    reply_text = str(\n        reply.model_dump()\n    )\n\n    assert "confirmed" in reply_text\n    assert "approved" in reply_text\n    assert "running" in reply_text\n    assert "pending" in reply_text\n\n    rca_reply = await runtime_two.conversation.handle(\n        ConversationTurnRequest(\n            conversation_id="chat-thread-1",\n            text="根因是什么？",\n        )\n    )\n\n    rca_text = str(\n        rca_reply.model_dump()\n    )\n\n    assert (\n        "Deployment reduced the memory limit"\n        in rca_text\n    )\n\n\n@pytest.mark.asyncio\nasync def test_analysis_persistence_failure_cannot_fail_primary_workflow(\n    monkeypatch,\n    tmp_path,\n):\n    runtime = object.__new__(\n        AgentRuntime\n    )\n\n    runtime.incident_analysis_store = (\n        IncidentAnalysisStore(\n            db_path=(\n                tmp_path\n                / "broken_analysis.db"\n            )\n        )\n    )\n\n    async def broken_get(\n        incident_id,\n    ):\n        raise RuntimeError(\n            "secret backend details"\n        )\n\n    monkeypatch.setattr(\n        runtime.incident_analysis_store,\n        "get",\n        broken_get,\n    )\n\n    context = AgentContext(\n        event=event(),\n        incident=IncidentState(),\n        metadata={},\n    )\n\n    await runtime._persist_incident_analysis(\n        context\n    )\n\n    snapshot = context.metadata[\n        "incident_analysis_persistence"\n    ]\n\n    assert snapshot[\n        "status"\n    ] == "failed"\n\n    assert snapshot[\n        "failure_code"\n    ] == "RuntimeError"\n\n    assert "secret" not in str(\n        snapshot\n    )\n\n\ndef test_conversation_runtime_provider_has_no_write_authority():\n    from pathlib import Path\n    import services.agent_runtime.app.conversation.runtime_provider as module\n\n    source = Path(\n        module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        ".approve(",\n        ".reject(",\n        ".resume(",\n        ".execute(",\n        ".update(",\n        ".save(",\n        "KubernetesProductionExecutor",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n'}


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
                f"{relative} changed after the reviewed ChatOps baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Incident Analysis / Conversation Context installation."
            )
        )


def discover_store_compatibility_tests(
    root: Path,
) -> list[str]:
    """
    Discover the repository's actual persistence tests by semantics instead of
    hard-coded historical filenames.

    Test files are allowed to be renamed. Each authoritative store must still
    be represented by at least one current test module before installation can
    continue.
    """

    tests_root = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
    )

    if not tests_root.exists():
        raise RuntimeError(
            "Agent Runtime tests directory is missing"
        )

    required_markers = {
        "IncidentStore": [],
        "ApprovalStore": [],
        "ActionExecutionStore": [],
        "VerificationStore": [],
    }

    for path in sorted(
        tests_root.glob(
            "test_*.py"
        )
    ):
        try:
            content = path.read_text(
                encoding="utf-8",
            )
        except (OSError, UnicodeError):
            continue

        relative = str(
            path.relative_to(
                root
            )
        ).replace(
            "\\",
            "/",
        )

        for marker in required_markers:
            if marker in content:
                required_markers[
                    marker
                ].append(
                    relative
                )

    missing = [
        marker
        for marker, matches
        in required_markers.items()
        if not matches
    ]

    if missing:
        raise RuntimeError(
            "No current compatibility test references authoritative store(s): "
            + ", ".join(
                missing
            )
        )

    selected = []

    for marker in (
        "IncidentStore",
        "ApprovalStore",
        "ActionExecutionStore",
        "VerificationStore",
    ):
        for relative in required_markers[
            marker
        ]:
            if relative not in selected:
                selected.append(
                    relative
                )

    return selected


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
        for relative, source in (
            SOURCES.items()
        )
    }

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Incident Analysis Persistence + Runtime Conversation Context Provider v1.1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Product direction:",
        "- ChatOps-first AI SRE Agent",
        "- no second Incident/Approval/Action/Verification source of truth",
        "",
        "Persistence boundary:",
        "- IncidentStore remains authoritative for Incident lifecycle",
        "- ApprovalStore remains authoritative for Approval",
        "- ActionExecutionStore remains authoritative for execution",
        "- VerificationStore remains authoritative for recovery verification",
        "- MemoryStore remains historical similarity memory",
        "- new IncidentAnalysisStore owns only per-Incident analysis facts that were previously request-local",
        "",
        "IncidentAnalysisStore contains:",
        "- bounded Incident signal/resource scope",
        "- authoritative Planner RCA projection keyed by incident_id",
        "- bounded InvestigationState including hypotheses/evidence/conclusion",
        "",
        "Runtime:",
        "- persists Planner RCA immediately after successful primary Pipeline",
        "- later merges Investigation Shadow into the same Incident analysis row",
        "- persistence failure is sanitized and cannot fail the primary workflow",
        "- owns one RuntimeConversationIncidentContextProvider",
        "- owns one ConversationOrchestrator wired to that provider",
        "",
        "Conversation projection:",
        "- Incident status/reason from IncidentStore",
        "- RCA/evidence/hypotheses from IncidentAnalysisStore",
        "- latest Approval from ApprovalStore",
        "- latest Action Execution from ActionExecutionStore",
        "- latest Verification from VerificationStore",
        "",
        "No LLM/network/Action/Approval write is performed by the installer.",
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
                    "New Incident Analysis target already exists; refusing overwrite: "
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
                "Incident Analysis / Conversation syntax failed"
            )

        focused = run_command(
            root=root,
            name="Incident Analysis + Conversation focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_incident_analysis_conversation_context.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_conversation_orchestrator.py"
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
                "Incident Analysis / Conversation focused tests failed"
            )

        persistence_paths = (
            discover_store_compatibility_tests(
                root
            )
        )

        section(
            report,
            "DISCOVERED AUTHORITATIVE STORE TESTS",
        )

        report.extend(
            persistence_paths
        )

        persistence = run_command(
            root=root,
            name="Authoritative persistence compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                *persistence_paths,
                "-q",
            ],
        )

        add_command(
            report,
            persistence,
        )

        if persistence.returncode != 0:
            raise RuntimeError(
                "Authoritative persistence compatibility failed"
            )

        workflow_paths = require_tests(
            root,
            [
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_auto_shadow_orchestration.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_read_rbac.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_api_action_verification.py"
                ),
            ],
        )

        workflow = run_command(
            root=root,
            name="Runtime / Investigation / API compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                *workflow_paths,
                "-q",
            ],
        )

        add_command(
            report,
            workflow,
        )

        if workflow.returncode != 0:
            raise RuntimeError(
                "Runtime / Investigation / API compatibility failed"
            )

        authority = run_command(
            root=root,
            name="Conversation context read-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/runtime_provider.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['.approve(','.reject(','.resume(','.execute(','.update(','.save(','KubernetesProductionExecutor'] if x in p]; "
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
                "Conversation Context authority boundary failed"
            )

        architecture = run_command(
            root=root,
            name="ChatOps persistence architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/investigation/store.py').read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "p=Path(r'services/agent_runtime/app/conversation/runtime_provider.py').read_text(encoding='utf-8'); "
                    "print('sqlite_analysis='+str('CREATE TABLE IF NOT EXISTS incident_analysis' in s)); "
                    "print('runtime_primary_persist='+str(r.count('await self._persist_incident_analysis(') >= 2)); "
                    "print('runtime_conversation='+str('self.conversation = ConversationOrchestrator' in r)); "
                    "print('provider_incident_reads='+str('incident_store.get' in p)); "
                    "print('provider_analysis_reads='+str('analysis_store.get' in p)); "
                    "print('provider_approval_reads='+str('list_by_incident' in p)); "
                    "assert 'CREATE TABLE IF NOT EXISTS incident_analysis' in s; "
                    "assert r.count('await self._persist_incident_analysis(') >= 2; "
                    "assert 'self.conversation = ConversationOrchestrator' in r; "
                    "assert 'incident_store.get' in p; "
                    "assert 'analysis_store.get' in p"
                ),
            ],
        )

        add_command(
            report,
            architecture,
        )

        if architecture.returncode != 0:
            raise RuntimeError(
                "ChatOps persistence architecture preflight failed"
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
                "Incident Analysis Persistence + Runtime Conversation Context Provider v1.1 is installed.",
                "",
                "ChatOps can now rebuild one Incident context after Runtime restart from durable stores:",
                "- status/reason",
                "- primary RCA",
                "- Investigation hypotheses/evidence/conclusion",
                "- Approval status/recommended Action",
                "- Action Execution status",
                "- Verification status",
                "",
                "Historical Memory remains separate and is not used as the Incident source of truth.",
                "",
                "Next recommended step:",
                "- Durable Conversation Binding + ChatOps Adapter Contract v1: persist conversation/thread -> incident binding, then add a channel-neutral inbound/outbound adapter contract before choosing Feishu/DingTalk/Slack.",
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
            "INCIDENT ANALYSIS PERSISTENCE + CONVERSATION CONTEXT V1.1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "Installer sent no real network/LLM/Action request."
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
                        "Incident Analysis Persistence + Runtime "
                        "Conversation Context Provider v1.1 FAILED"
                    ),
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
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
            "INCIDENT ANALYSIS PERSISTENCE + CONVERSATION CONTEXT V1.1 FAILED"
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
