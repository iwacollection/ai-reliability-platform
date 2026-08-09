from datetime import UTC, datetime

import pytest

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


NOW = datetime(
    2026,
    8,
    10,
    5,
    0,
    tzinfo=UTC,
)


def event() -> StandardEvent:
    return StandardEvent(
        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message="payment-api restarted",
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name="payment-api",
                namespace="payment",
                cluster="production-a",
            )
        ],
    )


def tools() -> ToolManager:
    return ToolManager(
        ToolRegistry()
    )


class RecordingPipeline:
    def __init__(
        self,
        order,
    ):
        self.order = order
        self.contexts = []

    async def execute(
        self,
        context,
    ):
        self.order.append(
            "pipeline"
        )
        self.contexts.append(
            context
        )

        context.variables[
            "pipeline_variable"
        ] = "preserved"

        context.results[
            "pipeline_result"
        ] = {
            "success": True,
        }

        return [
            "authoritative-pipeline-result"
        ]


class SuccessfulShadowCoordinator:
    def __init__(
        self,
        order,
    ):
        self.order = order
        self.contexts = []

    async def investigate(
        self,
        context,
    ):
        self.order.append(
            "investigation"
        )
        self.contexts.append(
            context
        )

        # Attempt mutations inside Shadow context. None of these may escape
        # to the primary Pipeline context.
        context.variables[
            "shadow_only_variable"
        ] = "must-not-leak"

        context.results[
            "shadow_only_result"
        ] = {
            "must_not_leak": True,
        }

        context.metadata[
            "shadow_private"
        ] = "must-not-leak"

        context.metadata[
            "investigation_shadow"
        ] = {
            "shadow_mode": True,
            "read_only": True,
            "status": "concluded",
            "stop_reason": (
                "sufficient_evidence"
            ),
            "conclusion": {
                "root_cause": (
                    "memory pressure"
                ),
            },
        }

        return object()


class ExplodingShadowCoordinator:
    def __init__(
        self,
        order,
        secret,
    ):
        self.order = order
        self.secret = secret
        self.calls = 0

    async def investigate(
        self,
        context,
    ):
        self.order.append(
            "investigation"
        )
        self.calls += 1

        raise RuntimeError(
            self.secret
        )


class ForbiddenShadowCoordinator:
    def __init__(
        self,
    ):
        self.calls = 0

    async def investigate(
        self,
        context,
    ):
        self.calls += 1

        raise AssertionError(
            "Investigation ran after Pipeline failure"
        )


class ExplodingPipeline:
    async def execute(
        self,
        context,
    ):
        raise RuntimeError(
            "authoritative pipeline failure"
        )


def lightweight_runtime(
    *,
    pipeline,
    coordinator,
):
    runtime = object.__new__(
        AgentRuntime
    )

    runtime.pipeline = pipeline

    runtime.investigation_coordinator = (
        coordinator
    )

    runtime.tools = tools()

    return runtime


def main_context(
    runtime,
) -> AgentContext:
    return AgentContext(
        request_id="request-123",
        event=event(),
        tools=runtime.tools,
        metadata={
            "existing": "preserved",
        },
        variables={
            "existing": "preserved",
        },
        results={
            "existing": "preserved",
        },
    )


@pytest.mark.asyncio
async def test_enabled_runtime_runs_shadow_after_pipeline_in_isolated_context():
    order = []

    pipeline = RecordingPipeline(
        order
    )

    coordinator = (
        SuccessfulShadowCoordinator(
            order
        )
    )

    runtime = lightweight_runtime(
        pipeline=pipeline,
        coordinator=coordinator,
    )

    context = main_context(
        runtime
    )

    incident_before = (
        context.incident.model_dump(
            mode="json"
        )
    )

    results = await runtime.execute(
        context
    )

    assert results == [
        "authoritative-pipeline-result"
    ]

    assert order == [
        "pipeline",
        "investigation",
    ]

    assert len(
        coordinator.contexts
    ) == 1

    shadow_context = (
        coordinator.contexts[0]
    )

    assert shadow_context is not context

    assert (
        shadow_context.request_id
        == context.request_id
    )

    assert (
        shadow_context.event
        is not context.event
    )

    assert (
        shadow_context.event.model_dump(
            mode="json"
        )
        == context.event.model_dump(
            mode="json"
        )
    )

    assert (
        shadow_context.tools
        is runtime.tools
    )

    # Minimum privilege Shadow context.
    assert shadow_context.memory is None
    assert shadow_context.skills is None
    assert shadow_context.mcp is None
    assert shadow_context.sandbox is None
    assert shadow_context.sandbox_policy is None
    assert shadow_context.approval is None
    assert shadow_context.trace is None

    # Pipeline mutations remain authoritative.
    assert context.variables == {
        "existing": "preserved",
        "pipeline_variable": "preserved",
    }

    assert context.results == {
        "existing": "preserved",
        "pipeline_result": {
            "success": True,
        },
    }

    # Shadow mutations cannot cross the context boundary.
    assert (
        "shadow_only_variable"
        not in context.variables
    )

    assert (
        "shadow_only_result"
        not in context.results
    )

    assert (
        "shadow_private"
        not in context.metadata
    )

    assert (
        context.incident.model_dump(
            mode="json"
        )
        == incident_before
    )

    # Only the bounded Shadow snapshot crosses back.
    snapshot = context.metadata[
        "investigation_shadow"
    ]

    assert snapshot[
        "shadow_mode"
    ] is True

    assert snapshot[
        "read_only"
    ] is True

    assert snapshot[
        "status"
    ] == "concluded"

    assert (
        "investigation_shadow_orchestration"
        not in context.metadata
    )


@pytest.mark.asyncio
async def test_disabled_runtime_executes_pipeline_without_shadow_work():
    order = []

    pipeline = RecordingPipeline(
        order
    )

    runtime = lightweight_runtime(
        pipeline=pipeline,
        coordinator=None,
    )

    context = main_context(
        runtime
    )

    results = await runtime.execute(
        context
    )

    assert results == [
        "authoritative-pipeline-result"
    ]

    assert order == [
        "pipeline",
    ]

    assert (
        "investigation_shadow"
        not in context.metadata
    )

    assert (
        "investigation_shadow_orchestration"
        not in context.metadata
    )


@pytest.mark.asyncio
async def test_shadow_failure_cannot_fail_successful_pipeline_or_leak_secret():
    order = []

    secret = (
        "https://user:secret-token@"
        "provider.example.invalid"
    )

    pipeline = RecordingPipeline(
        order
    )

    coordinator = (
        ExplodingShadowCoordinator(
            order,
            secret,
        )
    )

    runtime = lightweight_runtime(
        pipeline=pipeline,
        coordinator=coordinator,
    )

    context = main_context(
        runtime
    )

    results = await runtime.execute(
        context
    )

    assert results == [
        "authoritative-pipeline-result"
    ]

    assert order == [
        "pipeline",
        "investigation",
    ]

    assert (
        "investigation_shadow"
        not in context.metadata
    )

    orchestration = context.metadata[
        "investigation_shadow_orchestration"
    ]

    assert orchestration == {
        "shadow_mode": True,
        "read_only": True,
        "automatic": True,
        "status": "failed",
        "failure_code": "RuntimeError",
    }

    assert secret not in str(
        context.metadata
    )


@pytest.mark.asyncio
async def test_pipeline_failure_remains_authoritative_and_blocks_shadow():
    coordinator = (
        ForbiddenShadowCoordinator()
    )

    runtime = lightweight_runtime(
        pipeline=ExplodingPipeline(),
        coordinator=coordinator,
    )

    context = main_context(
        runtime
    )

    with pytest.raises(
        RuntimeError,
        match="authoritative pipeline failure",
    ):
        await runtime.execute(
            context
        )

    assert coordinator.calls == 0

    assert (
        "investigation_shadow"
        not in context.metadata
    )
