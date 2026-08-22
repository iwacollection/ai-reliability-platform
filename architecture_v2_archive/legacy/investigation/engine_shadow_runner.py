from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGate,
    InvestigationEngineShadowSettings,
)
from services.agent_runtime.app.investigation.engine_shadow_runtime_factory import (
    InvestigationEngineShadowRuntimeComponents,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    default_investigation_probes,
)
from services.agent_runtime.app.investigation.session_loop import (
    InvestigationSessionLoopOutcome,
    InvestigationSessionLoopStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
    SessionKey,
)

_SESSION_KEY_ADAPTER = TypeAdapter(SessionKey)
_SHADOW_CLAIMANT = "investigation-shadow-runner-v1"
_SHADOW_CREATED_BY = "langgraph-shadow-runtime-v1"


class InvestigationEngineShadowRunStatus(str, Enum):
    NOT_SELECTED = "not_selected"
    CONCURRENCY_LIMIT = "concurrency_limit"
    EXECUTED = "executed"
    REPLAYED = "replayed"
    FAILED = "failed"


class InvestigationEngineShadowRunResult(BaseModel):
    """Bounded result that cannot alter or replace the primary result."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    status: InvestigationEngineShadowRunStatus
    selected: bool
    read_only: Literal[True] = True
    primary_result_influence: Literal[False] = False
    max_external_steps: Literal[1] = 1
    external_calls_made: int | None = Field(
        default=0,
        ge=0,
        le=1,
    )
    session_id: UUID | None = None
    session_status: InvestigationSessionStatus | None = None
    outcome: InvestigationSessionLoopOutcome | None = None
    stop_reason: InvestigationSessionLoopStopReason | None = None
    failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$",
    )

    @model_validator(mode="after")
    def validate_shape(self):
        no_session = self.session_id is None and self.session_status is None
        if self.status == InvestigationEngineShadowRunStatus.NOT_SELECTED:
            if self.selected or not no_session or self.external_calls_made != 0:
                raise ValueError("not-selected Shadow result is invalid")
        elif self.status == InvestigationEngineShadowRunStatus.CONCURRENCY_LIMIT:
            if not self.selected or not no_session or self.external_calls_made != 0:
                raise ValueError("concurrency-limited Shadow result is invalid")
        elif self.status == InvestigationEngineShadowRunStatus.REPLAYED:
            if (
                not self.selected
                or self.session_id is None
                or self.session_status is None
                or self.external_calls_made != 0
            ):
                raise ValueError("replayed Shadow result is invalid")
        elif self.status == InvestigationEngineShadowRunStatus.EXECUTED:
            if (
                not self.selected
                or self.session_id is None
                or self.session_status is None
                or self.external_calls_made is None
                or self.outcome is None
                or self.stop_reason is None
            ):
                raise ValueError("executed Shadow result is invalid")
        else:
            if (
                not self.selected
                or self.external_calls_made is not None
                or self.failure_code is None
            ):
                raise ValueError("failed Shadow result is invalid")

        if (
            self.status != InvestigationEngineShadowRunStatus.FAILED
            and self.failure_code is not None
        ):
            raise ValueError("non-failed Shadow result contains failure_code")
        return self


class _ProcessLocalConcurrencyLimiter:
    """Non-blocking process-local cap; durable Claims remain cross-process."""

    def __init__(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Shadow concurrency limit is invalid")
        self.limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def try_slot(self) -> AsyncIterator[bool]:
        async with self._lock:
            acquired = self._active < self.limit
            if acquired:
                self._active += 1
        try:
            yield acquired
        finally:
            if acquired:
                async with self._lock:
                    self._active -= 1


@dataclass
class _ShadowInvocationContext:
    request_id: str | None
    event: Any
    tools: Any
    metadata: dict[str, Any]


class InvestigationEngineShadowRunner:
    """
    Execute at most one durable read-only Shadow step per unique run key.

    Exact replay returns the persisted Session without advancing it. This is
    stricter than the general Session Engine, whose explicit callers may
    intentionally advance multiple steps over time.
    """

    def __init__(
        self,
        *,
        runtime: InvestigationEngineShadowRuntimeComponents,
        settings: InvestigationEngineShadowSettings,
        tools: Any,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(
            runtime,
            InvestigationEngineShadowRuntimeComponents,
        ):
            raise TypeError("Shadow Runner Runtime is invalid")
        if not isinstance(settings, InvestigationEngineShadowSettings):
            raise TypeError("Shadow Runner settings are invalid")
        if not runtime.decision.allowed:
            raise ValueError("Shadow Runner requires an Allow decision")
        if (
            runtime.decision.sample_rate != settings.sample_rate
            or runtime.decision.max_concurrent_sessions != settings.max_concurrent_sessions
            or runtime.decision.matrix_digest != settings.expected_matrix_digest
            or runtime.decision.release_digest != settings.expected_release_digest
        ):
            raise ValueError("Shadow Runner Gate binding is invalid")
        if tools is None or not callable(getattr(tools, "call", None)):
            raise TypeError("Shadow Runner requires shared read-only tools")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("Shadow Runner clock is invalid")

        self.runtime = runtime
        self.settings = settings
        self.tools = tools
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._limiter = _ProcessLocalConcurrencyLimiter(settings.max_concurrent_sessions)

    @property
    def active_invocations(self) -> int:
        return self._limiter.active

    async def run_once(
        self,
        context: Any,
        *,
        run_key: str,
    ) -> InvestigationEngineShadowRunResult:
        incident_id = self._incident_id(context)
        normalized_run_key = _SESSION_KEY_ADAPTER.validate_python(run_key)
        if getattr(context, "tools", None) is not self.tools:
            raise TypeError("Shadow Runner requires shared Runtime tools")

        if not InvestigationEngineShadowGate.selected_for_shadow(
            decision=self.runtime.decision,
            incident_id=incident_id,
            run_key=normalized_run_key,
        ):
            return InvestigationEngineShadowRunResult(
                status=InvestigationEngineShadowRunStatus.NOT_SELECTED,
                selected=False,
            )

        async with self._limiter.try_slot() as acquired:
            if not acquired:
                return InvestigationEngineShadowRunResult(
                    status=(InvestigationEngineShadowRunStatus.CONCURRENCY_LIMIT),
                    selected=True,
                )

            shadow_context = self._shadow_context(context)
            session_id: UUID | None = None
            try:
                current = self._now()
                initial_state = InvestigationState(
                    scope=self._scope_from_context(shadow_context),
                    limits=InvestigationLimits(),
                    available_probes=self._available_probes(shadow_context),
                    started_at=current,
                    updated_at=current,
                )
                created = await self.runtime.engine.create_or_get(
                    incident_id=incident_id,
                    run_key=normalized_run_key,
                    initial_state=initial_state,
                    created_by=_SHADOW_CREATED_BY,
                    now=current,
                )
                session_id = created.session.session_id

                # An exact retry must never turn one sampled invocation into a
                # second external read. Interrupted fresh Sessions therefore
                # remain visible for explicit recovery instead of auto-resume.
                if created.replayed:
                    return InvestigationEngineShadowRunResult(
                        status=InvestigationEngineShadowRunStatus.REPLAYED,
                        selected=True,
                        external_calls_made=0,
                        session_id=created.session.session_id,
                        session_status=created.session.status,
                    )

                result = await self.runtime.engine.advance(
                    created.session.session_id,
                    context=shadow_context,
                    claimant=_SHADOW_CLAIMANT,
                    max_external_steps=1,
                    expected_version=0,
                )
                return InvestigationEngineShadowRunResult(
                    status=InvestigationEngineShadowRunStatus.EXECUTED,
                    selected=True,
                    external_calls_made=result.external_calls_made,
                    session_id=result.session.session_id,
                    session_status=result.session.status,
                    outcome=result.outcome,
                    stop_reason=result.stop_reason,
                )
            except Exception as error:
                return InvestigationEngineShadowRunResult(
                    status=InvestigationEngineShadowRunStatus.FAILED,
                    selected=True,
                    external_calls_made=None,
                    session_id=session_id,
                    failure_code=self._failure_code(error),
                )

    def _shadow_context(self, context: Any) -> _ShadowInvocationContext:
        event = getattr(context, "event", None)
        if event is None:
            raise ValueError("Shadow Runner requires one event")
        return _ShadowInvocationContext(
            request_id=getattr(context, "request_id", None),
            event=deepcopy(event),
            tools=self.tools,
            metadata={},
        )

    @staticmethod
    def _incident_id(context: Any) -> UUID:
        incident = getattr(context, "incident", None)
        value = getattr(incident, "id", None)
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError("Shadow Runner Incident identity is invalid") from error

    @staticmethod
    def _scope_from_context(context: Any) -> InvestigationScope:
        event = getattr(context, "event", None)
        signal = getattr(event, "signal", None)
        resources = getattr(event, "resources", None)
        if signal is None or not resources:
            raise ValueError("Shadow Runner requires one event resource")

        resource = resources[0]
        header = getattr(event, "header", None)
        occurred_at = getattr(header, "occurred_at", None)
        if occurred_at is not None:
            if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
                raise ValueError("Shadow Runner event time must be timezone-aware")
            occurred_at = occurred_at.astimezone(UTC)

        return InvestigationScope(
            alert_name=str(getattr(signal, "name", "")),
            alert_message=str(getattr(signal, "message", "") or ""),
            event_occurred_at=occurred_at,
            resource=str(getattr(resource, "name", "")),
            namespace=str(getattr(resource, "namespace", None) or "default"),
            cluster=(
                str(getattr(resource, "cluster")) if getattr(resource, "cluster", None) else None
            ),
        )

    def _available_probes(self, context: Any) -> list[InvestigationProbe]:
        executor = self.runtime.driver.probe_executor
        resolver = getattr(executor, "available_probes", None)
        resolved = resolver(context) if callable(resolver) else default_investigation_probes()
        if not isinstance(resolved, (list, tuple)):
            raise TypeError("Shadow Runner available probes are invalid")

        normalized: list[InvestigationProbe] = []
        for item in resolved:
            if not isinstance(item, InvestigationProbe):
                raise TypeError("Shadow Runner available probe is invalid")
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("Shadow Runner requires one available probe")
        return normalized

    def _now(self) -> datetime:
        value = self._utc_clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Shadow Runner clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _failure_code(error: Exception) -> str:
        value = re.sub(
            r"[^A-Za-z0-9._:-]",
            "_",
            type(error).__name__,
        )[:128]
        if not value or not value[0].isalpha():
            return "ShadowRunnerError"
        return value


__all__ = [
    "InvestigationEngineShadowRunResult",
    "InvestigationEngineShadowRunStatus",
    "InvestigationEngineShadowRunner",
]
