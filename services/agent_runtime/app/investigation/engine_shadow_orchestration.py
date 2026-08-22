from __future__ import annotations

import asyncio
import os
import re
from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_runtime.app.investigation.engine_shadow_runner import (
    InvestigationEngineShadowRunner,
    InvestigationEngineShadowRunResult,
)
from services.agent_runtime.app.investigation.settings import (
    optional_text,
    parse_bool,
    parse_float,
    parse_int,
)

INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT = (
    "I_ENABLE_POST_PIPELINE_LANGGRAPH_SHADOW_ORCHESTRATION_V1"
)


class InvestigationEngineShadowOrchestrationConfigurationError(ValueError):
    """Post-Pipeline Shadow orchestration configuration is invalid."""


class InvestigationEngineShadowOrchestrationSettings(BaseModel):
    """Disabled-default policy for detached post-Pipeline Shadow work."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    acknowledgement: str | None = Field(
        default=None,
        max_length=128,
    )
    timeout_seconds: float = Field(
        default=2.0,
        ge=0.1,
        le=5.0,
    )
    max_pending_tasks: int = Field(
        default=1,
        ge=1,
        le=4,
    )
    completed_result_limit: int = Field(
        default=32,
        ge=1,
        le=128,
    )

    @model_validator(mode="after")
    def validate_enablement(self):
        if (
            self.enabled
            and self.acknowledgement
            != INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled post-Pipeline Shadow orchestration requires exact "
                "acknowledgement"
            )
        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "InvestigationEngineShadowOrchestrationSettings":
        values = environment if environment is not None else os.environ
        try:
            return cls(
                enabled=parse_bool(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ENABLED"
                    ),
                    default=False,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ENABLED"
                    ),
                ),
                acknowledgement=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT"
                    )
                ),
                timeout_seconds=parse_float(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_TIMEOUT_SECONDS"
                    ),
                    default=2.0,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_TIMEOUT_SECONDS"
                    ),
                ),
                max_pending_tasks=parse_int(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_MAX_PENDING"
                    ),
                    default=1,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_MAX_PENDING"
                    ),
                ),
                completed_result_limit=parse_int(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_RESULT_LIMIT"
                    ),
                    default=32,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_RESULT_LIMIT"
                    ),
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvestigationEngineShadowOrchestrationConfigurationError(
                "post-Pipeline Shadow orchestration configuration is invalid"
            ) from error


class InvestigationEngineShadowSubmissionStatus(str, Enum):
    SUBMITTED = "submitted"
    CAPACITY_LIMIT = "capacity_limit"
    FAILED = "failed"


class InvestigationEngineShadowSubmissionResult(BaseModel):
    """Immediate, bounded submission result; it contains no evidence."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    status: InvestigationEngineShadowSubmissionStatus
    accepted: bool
    read_only: Literal[True] = True
    detached: Literal[True] = True
    primary_result_influence: Literal[False] = False
    failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$",
    )

    @model_validator(mode="after")
    def validate_shape(self):
        if self.status == InvestigationEngineShadowSubmissionStatus.SUBMITTED:
            if not self.accepted or self.failure_code is not None:
                raise ValueError("submitted Shadow orchestration result is invalid")
        elif self.status == InvestigationEngineShadowSubmissionStatus.CAPACITY_LIMIT:
            if self.accepted or self.failure_code is not None:
                raise ValueError("capacity-limited Shadow result is invalid")
        elif self.accepted or self.failure_code is None:
            raise ValueError("failed Shadow submission result is invalid")
        return self


class InvestigationEngineShadowCompletionStatus(str, Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvestigationEngineShadowCompletionResult(BaseModel):
    """Bounded completion retained only by the Shadow task supervisor."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    status: InvestigationEngineShadowCompletionStatus
    read_only: Literal[True] = True
    detached: Literal[True] = True
    primary_result_influence: Literal[False] = False
    runner_result: InvestigationEngineShadowRunResult | None = None
    failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$",
    )

    @model_validator(mode="after")
    def validate_shape(self):
        if self.status == InvestigationEngineShadowCompletionStatus.COMPLETED:
            if self.runner_result is None or self.failure_code is not None:
                raise ValueError("completed Shadow orchestration result is invalid")
        elif self.runner_result is not None:
            raise ValueError("non-completed Shadow result contains Runner output")
        elif self.status in {
            InvestigationEngineShadowCompletionStatus.FAILED,
            InvestigationEngineShadowCompletionStatus.CANCELLED,
        } and self.failure_code is None:
            raise ValueError("failed Shadow completion result is invalid")
        elif (
            self.status == InvestigationEngineShadowCompletionStatus.TIMED_OUT
            and self.failure_code is not None
        ):
            raise ValueError("timed-out Shadow result contains a failure code")
        return self


@dataclass(frozen=True)
class _DetachedShadowContext:
    request_id: str | None
    event: Any
    incident: Any
    tools: Any
    metadata: dict[str, Any]
    results: dict[str, Any]


class InvestigationEngineShadowOrchestrator:
    """
    Submit one post-Pipeline Shadow step without awaiting it in the request.

    The task set is process-local and bounded. Durable Session identities and
    Claims remain the cross-process replay boundary. Completion data is kept
    only in a bounded in-memory deque; the durable Shadow Session is the
    authoritative diagnostic record.
    """

    def __init__(
        self,
        *,
        runner: InvestigationEngineShadowRunner,
        settings: InvestigationEngineShadowOrchestrationSettings,
    ) -> None:
        if not isinstance(runner, InvestigationEngineShadowRunner):
            raise TypeError("Shadow Orchestrator Runner is invalid")
        if not isinstance(
            settings,
            InvestigationEngineShadowOrchestrationSettings,
        ):
            raise TypeError("Shadow Orchestrator settings are invalid")
        if not settings.enabled:
            raise ValueError("Shadow Orchestrator requires enabled settings")

        self.runner = runner
        self.settings = settings
        self._tasks: set[asyncio.Task] = set()
        self._completed: deque[InvestigationEngineShadowCompletionResult] = deque(
            maxlen=settings.completed_result_limit
        )

    @property
    def pending_count(self) -> int:
        return len(self._tasks)

    @property
    def completed_results(
        self,
    ) -> tuple[InvestigationEngineShadowCompletionResult, ...]:
        return tuple(self._completed)

    def submit(self, context: Any) -> InvestigationEngineShadowSubmissionResult:
        """Create one detached task and return without awaiting Shadow work."""

        if len(self._tasks) >= self.settings.max_pending_tasks:
            return InvestigationEngineShadowSubmissionResult(
                status=InvestigationEngineShadowSubmissionStatus.CAPACITY_LIMIT,
                accepted=False,
            )

        try:
            loop = asyncio.get_running_loop()
            detached = self._detached_context(context)
            run_key = self._run_key()
            task = loop.create_task(
                self._run(detached, run_key=run_key),
                name="investigation-langgraph-shadow-v1",
            )
            self._tasks.add(task)
            task.add_done_callback(self._consume)
        except Exception as error:
            return InvestigationEngineShadowSubmissionResult(
                status=InvestigationEngineShadowSubmissionStatus.FAILED,
                accepted=False,
                failure_code=self._failure_code(error),
            )

        return InvestigationEngineShadowSubmissionResult(
            status=InvestigationEngineShadowSubmissionStatus.SUBMITTED,
            accepted=True,
        )

    async def drain(self) -> None:
        """Testing and controlled-shutdown hook; never used by execute()."""

        current = tuple(self._tasks)
        if current:
            await asyncio.gather(*current, return_exceptions=True)

    async def cancel_pending(self) -> None:
        """Cancel detached work during an explicit controlled shutdown."""

        current = tuple(self._tasks)
        for task in current:
            task.cancel()
        if current:
            await asyncio.gather(*current, return_exceptions=True)

    async def _run(
        self,
        context: _DetachedShadowContext,
        *,
        run_key: str,
    ) -> InvestigationEngineShadowCompletionResult:
        try:
            result = await asyncio.wait_for(
                self.runner.run_once(
                    context,
                    run_key=run_key,
                ),
                timeout=self.settings.timeout_seconds,
            )
            return InvestigationEngineShadowCompletionResult(
                status=InvestigationEngineShadowCompletionStatus.COMPLETED,
                runner_result=result,
            )
        except TimeoutError:
            return InvestigationEngineShadowCompletionResult(
                status=InvestigationEngineShadowCompletionStatus.TIMED_OUT,
            )
        except Exception as error:
            return InvestigationEngineShadowCompletionResult(
                status=InvestigationEngineShadowCompletionStatus.FAILED,
                failure_code=self._failure_code(error),
            )

    def _consume(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            result = task.result()
        except asyncio.CancelledError:
            result = InvestigationEngineShadowCompletionResult(
                status=InvestigationEngineShadowCompletionStatus.CANCELLED,
                failure_code="ShadowTaskCancelled",
            )
        except BaseException as error:
            result = InvestigationEngineShadowCompletionResult(
                status=InvestigationEngineShadowCompletionStatus.FAILED,
                failure_code=self._failure_code(error),
            )
        self._completed.append(result)

    def _run_key(self) -> str:
        decision = self.runner.runtime.decision
        release = decision.release_digest
        matrix = decision.matrix_digest
        if release is None or matrix is None:
            raise ValueError("Shadow Orchestrator Gate digest is unavailable")
        return f"post-pipeline-shadow-v1:{release[:16]}:{matrix[:16]}"

    def _detached_context(self, context: Any) -> _DetachedShadowContext:
        incident = getattr(context, "incident", None)
        incident_id = getattr(incident, "id", None)
        event = getattr(context, "event", None)
        if incident_id is None or event is None:
            raise ValueError("Shadow Orchestrator context is invalid")
        if getattr(context, "tools", None) is not self.runner.tools:
            raise TypeError("Shadow Orchestrator requires shared Runtime tools")
        return _DetachedShadowContext(
            request_id=getattr(context, "request_id", None),
            event=deepcopy(event),
            incident=SimpleNamespace(id=incident_id),
            tools=self.runner.tools,
            metadata={},
            results={},
        )

    @staticmethod
    def _failure_code(error: BaseException) -> str:
        value = re.sub(
            r"[^A-Za-z0-9._:-]",
            "_",
            type(error).__name__,
        )[:128]
        if not value or not value[0].isalpha():
            return "ShadowOrchestrationError"
        return value


def create_investigation_engine_shadow_orchestrator(
    *,
    settings: InvestigationEngineShadowOrchestrationSettings | None = None,
    runner: InvestigationEngineShadowRunner | None = None,
) -> InvestigationEngineShadowOrchestrator | None:
    """Disabled mode returns before inspecting the optional Runner."""

    resolved = (
        settings
        if settings is not None
        else InvestigationEngineShadowOrchestrationSettings.from_environment()
    )
    if not isinstance(
        resolved,
        InvestigationEngineShadowOrchestrationSettings,
    ):
        raise TypeError("Shadow Orchestration settings are invalid")
    if not resolved.enabled:
        return None
    if runner is None:
        return None
    if not isinstance(runner, InvestigationEngineShadowRunner):
        raise TypeError("Shadow Orchestration Runner is invalid")
    return InvestigationEngineShadowOrchestrator(
        runner=runner,
        settings=resolved,
    )


__all__ = [
    "INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT",
    "InvestigationEngineShadowCompletionResult",
    "InvestigationEngineShadowCompletionStatus",
    "InvestigationEngineShadowOrchestrationConfigurationError",
    "InvestigationEngineShadowOrchestrationSettings",
    "InvestigationEngineShadowOrchestrator",
    "InvestigationEngineShadowSubmissionResult",
    "InvestigationEngineShadowSubmissionStatus",
    "create_investigation_engine_shadow_orchestrator",
]
