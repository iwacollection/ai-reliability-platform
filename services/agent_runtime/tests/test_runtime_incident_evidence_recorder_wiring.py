from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from common.domain.event import Header, Resource, Signal, StandardEvent
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

import services.agent_runtime.app.runtime.runtime as runtime_module

from services.agent_runtime.app.incident_evidence.settings import (
    INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT,
    IncidentEvidenceRecorderConfigurationError,
    IncidentEvidenceRecorderSettings,
)
from services.agent_runtime.app.model.context import AgentContext
from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.app.tools.manager import ToolManager
from services.agent_runtime.app.tools.registry import ToolRegistry


NOW = datetime(
    2026,
    8,
    10,
    8,
    20,
    tzinfo=UTC,
)


def event() -> StandardEvent:
    return StandardEvent(
        header=Header(
            event_id=UUID(
                "11111111-1111-4111-8111-111111111111"
            ),
            trace_id=UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            source=EventSource.ALERTMANAGER,
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message="payment-api restarted",
            labels={},
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


class Pipeline:
    def __init__(self, order):
        self.order = order

    async def execute(self, context):
        self.order.append("pipeline")

        assert (
            "incident_evidence_recorder"
            not in context.metadata
        )

        return [
            "authoritative-pipeline-result"
        ]


def tools() -> ToolManager:
    return ToolManager(
        ToolRegistry()
    )


def lightweight_runtime(order) -> AgentRuntime:
    value = object.__new__(
        AgentRuntime
    )
    value.pipeline = Pipeline(order)
    value.tools = tools()
    value.investigation_coordinator = None
    return value


def context(value) -> AgentContext:
    return AgentContext(
        request_id="request-001",
        event=event(),
        tools=value.tools,
        metadata={},
    )


def test_settings_default_disabled():
    settings = (
        IncidentEvidenceRecorderSettings
        .from_environment({})
    )

    assert settings.enabled is False
    assert settings.acknowledgement is None


def test_enabled_requires_acknowledgement():
    with pytest.raises(
        IncidentEvidenceRecorderConfigurationError,
        match="configuration is invalid",
    ):
        IncidentEvidenceRecorderSettings.from_environment(
            {
                "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED": "true",
                "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT": "wrong",
            }
        )


@pytest.mark.parametrize(
    "output_dir",
    [
        "../outside",
        "C:/outside",
    ],
)
def test_output_dir_fails_closed(
    output_dir,
):
    with pytest.raises(
        IncidentEvidenceRecorderConfigurationError,
        match="configuration is invalid",
    ):
        IncidentEvidenceRecorderSettings.from_environment(
            {
                "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR": output_dir,
            }
        )


@pytest.mark.asyncio
async def test_disabled_does_not_construct_recorder(
    monkeypatch,
):
    order = []
    value = lightweight_runtime(order)
    ctx = context(value)

    constructions = 0

    def forbidden_recorder(*args, **kwargs):
        nonlocal constructions
        constructions += 1
        raise AssertionError(
            "disabled recorder must not be constructed"
        )

    monkeypatch.setattr(
        runtime_module,
        "ProductionIncidentEvidenceRecorder",
        forbidden_recorder,
    )

    monkeypatch.setattr(
        runtime_module,
        "IncidentEvidenceRecorderSettings",
        SimpleNamespace(
            from_environment=lambda: (
                SimpleNamespace(
                    enabled=False
                )
            )
        ),
    )

    result = await value.execute(ctx)

    assert result == [
        "authoritative-pipeline-result"
    ]
    assert order == ["pipeline"]
    assert constructions == 0
    assert (
        "incident_evidence_recorder"
        not in ctx.metadata
    )


@pytest.mark.asyncio
async def test_enabled_runs_after_pipeline_with_isolated_context(
    monkeypatch,
    tmp_path,
):
    order = []
    value = lightweight_runtime(order)
    ctx = context(value)
    original_event = ctx.event
    captured = {}

    class Recorder:
        def __init__(self, output_dir):
            assert output_dir == (
                tmp_path / "captures"
            )

        async def record(self, recorder_context):
            order.append("recorder")
            captured["context"] = (
                recorder_context
            )

            return SimpleNamespace(
                incident_id=(
                    "capture-11111111-1111-4111-8111-111111111111"
                ),
                path=(
                    tmp_path
                    / "captures"
                    / "capture-111.replay.json"
                ),
                created=True,
                observation_count=4,
            )

    monkeypatch.setattr(
        runtime_module,
        "IncidentEvidenceRecorderSettings",
        SimpleNamespace(
            from_environment=lambda: (
                SimpleNamespace(
                    enabled=True,
                    resolve_output_dir=lambda: (
                        tmp_path / "captures"
                    ),
                )
            )
        ),
    )

    monkeypatch.setattr(
        runtime_module,
        "ProductionIncidentEvidenceRecorder",
        Recorder,
    )

    result = await value.execute(ctx)

    assert result == [
        "authoritative-pipeline-result"
    ]
    assert order == [
        "pipeline",
        "recorder",
    ]

    recorder_context = captured[
        "context"
    ]

    assert recorder_context is not ctx
    assert (
        recorder_context.event
        is not original_event
    )
    assert (
        recorder_context.event
        == original_event
    )
    assert (
        recorder_context.tools
        is value.tools
    )
    assert recorder_context.trace is None
    assert recorder_context.variables == {}
    assert recorder_context.results == {}
    assert recorder_context.metadata == {}

    assert ctx.metadata[
        "incident_evidence_recorder"
    ] == {
        "schema_version": "v1",
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "automatic": True,
        "status": "captured",
        "created": True,
        "incident_id": (
            "capture-11111111-1111-4111-8111-111111111111"
        ),
        "observation_count": 4,
        "capture_file": (
            "capture-111.replay.json"
        ),
    }


@pytest.mark.asyncio
async def test_failure_is_sanitized_and_pipeline_result_survives(
    monkeypatch,
):
    order = []
    value = lightweight_runtime(order)
    ctx = context(value)
    secret = "secret-production-tool-detail"

    class Recorder:
        def __init__(self, output_dir):
            pass

        async def record(self, recorder_context):
            order.append("recorder")
            raise RuntimeError(secret)

    monkeypatch.setattr(
        runtime_module,
        "IncidentEvidenceRecorderSettings",
        SimpleNamespace(
            from_environment=lambda: (
                SimpleNamespace(
                    enabled=True,
                    resolve_output_dir=lambda: (
                        SimpleNamespace()
                    ),
                )
            )
        ),
    )

    monkeypatch.setattr(
        runtime_module,
        "ProductionIncidentEvidenceRecorder",
        Recorder,
    )

    result = await value.execute(ctx)

    assert result == [
        "authoritative-pipeline-result"
    ]
    assert order == [
        "pipeline",
        "recorder",
    ]

    snapshot = ctx.metadata[
        "incident_evidence_recorder"
    ]

    assert snapshot["status"] == "failed"
    assert (
        snapshot["failure_code"]
        == "RuntimeError"
    )
    assert secret not in str(ctx.metadata)


@pytest.mark.asyncio
async def test_stale_metadata_is_removed_before_pipeline(
    monkeypatch,
):
    order = []
    value = lightweight_runtime(order)
    ctx = context(value)

    ctx.metadata[
        "incident_evidence_recorder"
    ] = {
        "stale": True
    }

    monkeypatch.setattr(
        runtime_module,
        "IncidentEvidenceRecorderSettings",
        SimpleNamespace(
            from_environment=lambda: (
                SimpleNamespace(
                    enabled=False
                )
            )
        ),
    )

    result = await value.execute(ctx)

    assert result == [
        "authoritative-pipeline-result"
    ]
    assert (
        "incident_evidence_recorder"
        not in ctx.metadata
    )
