from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowGateDecision,
)
from services.agent_runtime.app.investigation.engine_shadow_orchestration import (
    InvestigationEngineShadowCompletionStatus,
    InvestigationEngineShadowOrchestrationSettings,
    InvestigationEngineShadowOrchestrator,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepStatus,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)

_SESSION_LIMIT = 20


class InvestigationEngineShadowObservationUnavailableError(RuntimeError):
    """A bounded read-only Shadow observation could not be assembled."""


class InvestigationEngineShadowComparisonStatus(str, Enum):
    NO_SHADOW_SESSION = "no_shadow_session"
    NO_MATCHING_PRIMARY_IN_WINDOW = "no_matching_primary_in_window"
    MATCHING_INPUT_AVAILABLE = "matching_input_available"


class InvestigationEngineShadowGateSnapshot(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    allowed: bool
    code: InvestigationEngineShadowGateCode
    sample_rate: float = Field(
        ge=0.0,
        le=0.05,
    )
    max_concurrent_sessions: int = Field(
        ge=0,
        le=4,
    )
    matrix_bound: bool
    release_bound: bool
    read_only: Literal[True] = True
    writes_allowed: Literal[False] = False


class InvestigationEngineShadowOrchestrationSnapshot(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool
    active: bool
    pending_tasks: int = Field(
        ge=0,
        le=4,
    )
    completed_results_retained: int = Field(
        ge=0,
        le=128,
    )
    completed: int = Field(
        ge=0,
        le=128,
    )
    timed_out: int = Field(
        ge=0,
        le=128,
    )
    failed: int = Field(
        ge=0,
        le=128,
    )
    cancelled: int = Field(
        ge=0,
        le=128,
    )
    response_awaits_shadow: Literal[False] = False
    primary_result_influence: Literal[False] = False

    @model_validator(mode="after")
    def validate_counts(self):
        if (
            self.completed
            + self.timed_out
            + self.failed
            + self.cancelled
            != self.completed_results_retained
        ):
            raise ValueError(
                "Shadow Orchestration completion counts are invalid"
            )
        if not self.enabled and (
            self.active
            or self.pending_tasks
            or self.completed_results_retained
        ):
            raise ValueError(
                "disabled Shadow Orchestration contains Runtime activity"
            )
        return self


class InvestigationEngineShadowSessionSummary(BaseModel):
    """Lifecycle-only Session view with reasoning and replay data omitted."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    session_id: UUID
    status: InvestigationSessionStatus
    version: int = Field(
        ge=0,
    )
    step_count: int = Field(
        ge=0,
        le=32,
    )
    latest_step_kind: InvestigationStepKind | None = None
    latest_step_status: InvestigationStepStatus | None = None
    latest_probe: str | None = Field(
        default=None,
        max_length=128,
    )
    latest_failure_code: str | None = Field(
        default=None,
        max_length=256,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,255}$",
    )
    automatic_resume_blocked: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_latest_step(self):
        if self.step_count == 0 and any(
            value is not None
            for value in (
                self.latest_step_kind,
                self.latest_step_status,
                self.latest_probe,
                self.latest_failure_code,
            )
        ):
            raise ValueError(
                "empty Shadow Session contains latest step data"
            )
        if self.step_count > 0 and (
            self.latest_step_kind is None
            or self.latest_step_status is None
        ):
            raise ValueError(
                "non-empty Shadow Session is missing latest step data"
            )
        return self


class InvestigationEngineShadowComparisonSnapshot(BaseModel):
    """Protocol facts only; this model never claims semantic equivalence."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    status: InvestigationEngineShadowComparisonStatus
    primary_session_id: UUID | None = None
    shadow_session_id: UUID | None = None
    immutable_input_match: bool
    lifecycle_status_equal: bool | None = None
    step_count_equal: bool | None = None
    latest_step_kind_equal: bool | None = None
    latest_step_status_equal: bool | None = None
    semantic_equivalence_evaluated: Literal[False] = False
    decision_influence: Literal[False] = False

    @model_validator(mode="after")
    def validate_shape(self):
        protocol_values = (
            self.lifecycle_status_equal,
            self.step_count_equal,
            self.latest_step_kind_equal,
            self.latest_step_status_equal,
        )
        if self.status == InvestigationEngineShadowComparisonStatus.NO_SHADOW_SESSION:
            if (
                self.primary_session_id is not None
                or self.shadow_session_id is not None
                or self.immutable_input_match
                or any(value is not None for value in protocol_values)
            ):
                raise ValueError("no-Shadow comparison is invalid")
        elif (
            self.status
            == InvestigationEngineShadowComparisonStatus.NO_MATCHING_PRIMARY_IN_WINDOW
        ):
            if (
                self.primary_session_id is not None
                or self.shadow_session_id is None
                or self.immutable_input_match
                or any(value is not None for value in protocol_values)
            ):
                raise ValueError("unmatched Shadow comparison is invalid")
        elif (
            self.primary_session_id is None
            or self.shadow_session_id is None
            or not self.immutable_input_match
            or any(value is None for value in protocol_values)
        ):
            raise ValueError("matched Shadow comparison is invalid")
        return self


class InvestigationEngineShadowObservationSnapshot(BaseModel):
    """Bounded, read-only observation for one Incident."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    incident_id: UUID
    observed_at: datetime
    read_only: Literal[True] = True
    primary_result_influence: Literal[False] = False
    cross_store_atomic: Literal[False] = False
    session_limit: Literal[20] = 20
    primary_runtime_available: bool
    shadow_runtime_available: bool
    primary_sessions_truncated: bool
    shadow_sessions_truncated: bool
    gate: InvestigationEngineShadowGateSnapshot
    orchestration: InvestigationEngineShadowOrchestrationSnapshot
    primary_sessions: tuple[
        InvestigationEngineShadowSessionSummary,
        ...,
    ] = Field(
        default_factory=tuple,
        max_length=_SESSION_LIMIT,
    )
    shadow_sessions: tuple[
        InvestigationEngineShadowSessionSummary,
        ...,
    ] = Field(
        default_factory=tuple,
        max_length=_SESSION_LIMIT,
    )
    comparison: InvestigationEngineShadowComparisonSnapshot


class InvestigationEngineShadowObservationService:
    """Join bounded primary and Shadow lifecycle facts without mutation."""

    def __init__(
        self,
        *,
        primary_service: InvestigationSessionService | None,
        shadow_service: InvestigationSessionService | None,
        decision: InvestigationEngineShadowGateDecision,
        orchestration_settings: InvestigationEngineShadowOrchestrationSettings,
        orchestrator: InvestigationEngineShadowOrchestrator | None,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if primary_service is not None and not isinstance(
            primary_service,
            InvestigationSessionService,
        ):
            raise TypeError("Shadow Observation primary service is invalid")
        if shadow_service is not None and not isinstance(
            shadow_service,
            InvestigationSessionService,
        ):
            raise TypeError("Shadow Observation Shadow service is invalid")
        if (
            primary_service is not None
            and shadow_service is primary_service
        ):
            raise ValueError("Shadow Observation stores must be isolated")
        if not isinstance(
            decision,
            InvestigationEngineShadowGateDecision,
        ):
            raise TypeError("Shadow Observation Gate decision is invalid")
        if not isinstance(
            orchestration_settings,
            InvestigationEngineShadowOrchestrationSettings,
        ):
            raise TypeError("Shadow Observation orchestration settings are invalid")
        if orchestrator is not None and not isinstance(
            orchestrator,
            InvestigationEngineShadowOrchestrator,
        ):
            raise TypeError("Shadow Observation Orchestrator is invalid")
        if shadow_service is not None and not decision.allowed:
            raise ValueError("Shadow Observation service requires an Allow decision")
        if orchestrator is not None:
            if (
                not orchestration_settings.enabled
                or shadow_service is None
                or orchestrator.runner.runtime.service is not shadow_service
            ):
                raise ValueError("Shadow Observation Orchestrator binding is invalid")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("Shadow Observation clock is invalid")

        self.primary_service = primary_service
        self.shadow_service = shadow_service
        self.decision = decision
        self.orchestration_settings = orchestration_settings
        self.orchestrator = orchestrator
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    async def observe(
        self,
        incident_id: UUID | str,
    ) -> InvestigationEngineShadowObservationSnapshot:
        normalized_incident_id = UUID(
            str(
                incident_id
            )
        )
        try:
            primary_records, shadow_records = await asyncio.gather(
                self._load(self.primary_service, normalized_incident_id),
                self._load(self.shadow_service, normalized_incident_id),
            )
        except Exception as error:
            raise InvestigationEngineShadowObservationUnavailableError(
                "Shadow observation storage is unavailable"
            ) from error

        primary_truncated = len(primary_records) > _SESSION_LIMIT
        shadow_truncated = len(shadow_records) > _SESSION_LIMIT
        primary_records = primary_records[-_SESSION_LIMIT:]
        shadow_records = shadow_records[-_SESSION_LIMIT:]

        return InvestigationEngineShadowObservationSnapshot(
            incident_id=normalized_incident_id,
            observed_at=self._now(),
            primary_runtime_available=(self.primary_service is not None),
            shadow_runtime_available=(self.shadow_service is not None),
            primary_sessions_truncated=primary_truncated,
            shadow_sessions_truncated=shadow_truncated,
            gate=self._gate_snapshot(),
            orchestration=self._orchestration_snapshot(),
            primary_sessions=tuple(
                self._session_summary(session)
                for session in primary_records
            ),
            shadow_sessions=tuple(
                self._session_summary(session)
                for session in shadow_records
            ),
            comparison=self._comparison(
                primary_records,
                shadow_records,
            ),
        )

    @staticmethod
    async def _load(
        service: InvestigationSessionService | None,
        incident_id: UUID,
    ) -> list[InvestigationSessionRecord]:
        if service is None:
            return []
        return await service.list_recent_by_incident(
            incident_id,
            limit=_SESSION_LIMIT + 1,
        )

    def _gate_snapshot(self) -> InvestigationEngineShadowGateSnapshot:
        return InvestigationEngineShadowGateSnapshot(
            allowed=self.decision.allowed,
            code=self.decision.code,
            sample_rate=self.decision.sample_rate,
            max_concurrent_sessions=self.decision.max_concurrent_sessions,
            matrix_bound=self.decision.matrix_digest is not None,
            release_bound=self.decision.release_digest is not None,
        )

    def _orchestration_snapshot(
        self,
    ) -> InvestigationEngineShadowOrchestrationSnapshot:
        results = (
            self.orchestrator.completed_results
            if self.orchestrator is not None
            else ()
        )
        counts = Counter(
            result.status
            for result in results
        )
        return InvestigationEngineShadowOrchestrationSnapshot(
            enabled=self.orchestration_settings.enabled,
            active=self.orchestrator is not None,
            pending_tasks=(
                self.orchestrator.pending_count
                if self.orchestrator is not None
                else 0
            ),
            completed_results_retained=len(results),
            completed=counts[
                InvestigationEngineShadowCompletionStatus.COMPLETED
            ],
            timed_out=counts[
                InvestigationEngineShadowCompletionStatus.TIMED_OUT
            ],
            failed=counts[
                InvestigationEngineShadowCompletionStatus.FAILED
            ],
            cancelled=counts[
                InvestigationEngineShadowCompletionStatus.CANCELLED
            ],
        )

    @staticmethod
    def _session_summary(
        session: InvestigationSessionRecord,
    ) -> InvestigationEngineShadowSessionSummary:
        latest = (
            session.steps[-1]
            if session.steps
            else None
        )
        return InvestigationEngineShadowSessionSummary(
            session_id=session.session_id,
            status=session.status,
            version=session.version,
            step_count=len(session.steps),
            latest_step_kind=(latest.kind if latest is not None else None),
            latest_step_status=(latest.status if latest is not None else None),
            latest_probe=(
                latest.probe.value
                if latest is not None and latest.probe is not None
                else None
            ),
            latest_failure_code=(
                latest.failure_code
                if latest is not None
                else None
            ),
            automatic_resume_blocked=session.automatic_resume_blocked,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _comparison(
        primary_records: list[InvestigationSessionRecord],
        shadow_records: list[InvestigationSessionRecord],
    ) -> InvestigationEngineShadowComparisonSnapshot:
        if not shadow_records:
            return InvestigationEngineShadowComparisonSnapshot(
                status=(
                    InvestigationEngineShadowComparisonStatus.NO_SHADOW_SESSION
                ),
                immutable_input_match=False,
            )

        shadow = shadow_records[-1]
        matching = [
            session
            for session in primary_records
            if session.input_digest == shadow.input_digest
        ]
        if not matching:
            return InvestigationEngineShadowComparisonSnapshot(
                status=(
                    InvestigationEngineShadowComparisonStatus
                    .NO_MATCHING_PRIMARY_IN_WINDOW
                ),
                shadow_session_id=shadow.session_id,
                immutable_input_match=False,
            )

        primary = matching[-1]
        primary_latest = primary.steps[-1] if primary.steps else None
        shadow_latest = shadow.steps[-1] if shadow.steps else None
        return InvestigationEngineShadowComparisonSnapshot(
            status=(
                InvestigationEngineShadowComparisonStatus.MATCHING_INPUT_AVAILABLE
            ),
            primary_session_id=primary.session_id,
            shadow_session_id=shadow.session_id,
            immutable_input_match=True,
            lifecycle_status_equal=(primary.status == shadow.status),
            step_count_equal=(len(primary.steps) == len(shadow.steps)),
            latest_step_kind_equal=(
                getattr(primary_latest, "kind", None)
                == getattr(shadow_latest, "kind", None)
            ),
            latest_step_status_equal=(
                getattr(primary_latest, "status", None)
                == getattr(shadow_latest, "status", None)
            ),
        )

    def _now(self) -> datetime:
        value = self._utc_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise InvestigationEngineShadowObservationUnavailableError(
                "Shadow observation clock is invalid"
            )
        return value.astimezone(UTC)


__all__ = [
    "InvestigationEngineShadowComparisonSnapshot",
    "InvestigationEngineShadowComparisonStatus",
    "InvestigationEngineShadowGateSnapshot",
    "InvestigationEngineShadowObservationService",
    "InvestigationEngineShadowObservationSnapshot",
    "InvestigationEngineShadowObservationUnavailableError",
    "InvestigationEngineShadowOrchestrationSnapshot",
    "InvestigationEngineShadowSessionSummary",
]
