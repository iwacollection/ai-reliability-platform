import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)
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

from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)
from services.agent_runtime.app.llm.client import (
    LLMClient,
)
from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)
from services.agent_runtime.app.llm.gateway.rate_limiter import (
    RateLimiter,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.tools.base import (
    BaseTool,
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
    3,
    50,
    tzinfo=UTC,
)


class ScriptedInvestigationProvider(
    BaseLLMProvider
):
    """
    Deterministic provider used only by this integration test.

    It reads the bounded Investigation state embedded in the Reasoner
    prompt and behaves like a minimal LLM investigation loop:

    1. no evidence      -> request Kubernetes Pod state
    2. one evidence     -> request Prometheus memory working set
    3. two evidence     -> conclude
    """

    def __init__(
        self,
    ) -> None:
        self.requests: list[
            ChatRequest
        ] = []

    @property
    def name(
        self,
    ) -> str:
        return "scripted-investigation"

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        self.requests.append(
            request
        )

        marker = "State:\n"

        if marker not in request.user_prompt:
            raise AssertionError(
                "Investigation prompt does not contain bounded state"
            )

        raw_state = request.user_prompt.split(
            marker,
            1,
        )[1]

        state = json.loads(
            raw_state
        )

        evidence = state.get(
            "evidence",
            [],
        )

        if len(evidence) == 0:
            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory_limit_pressure"
                        ),
                        "cause": (
                            "Container may be terminated "
                            "because of memory pressure"
                        ),
                        "confidence": 0.40,
                        "supporting_evidence_ids": [],
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "pod termination state",
                            "memory working set",
                        ],
                    }
                ],
                "rationale_summary": (
                    "Collect Pod termination evidence first"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "kubernetes_pod_state"
                ),
                "conclusion": None,
            }

        elif len(evidence) == 1:
            first_id = evidence[0][
                "evidence_id"
            ]

            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory_limit_pressure"
                        ),
                        "cause": (
                            "Container was OOM killed "
                            "under memory pressure"
                        ),
                        "confidence": 0.76,
                        "supporting_evidence_ids": [
                            first_id,
                        ],
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "memory working set",
                        ],
                    }
                ],
                "rationale_summary": (
                    "OOM evidence exists; collect "
                    "memory working set"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "prometheus_memory_working_set"
                ),
                "conclusion": None,
            }

        elif len(evidence) == 2:
            evidence_ids = [
                item["evidence_id"]
                for item in evidence
            ]

            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory_limit_pressure"
                        ),
                        "cause": (
                            "Container memory pressure caused "
                            "an OOM termination"
                        ),
                        "confidence": 0.95,
                        "supporting_evidence_ids": (
                            evidence_ids
                        ),
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [],
                    }
                ],
                "rationale_summary": (
                    "Kubernetes OOM evidence and memory "
                    "working-set evidence agree"
                ),
                "stop": True,
                "stop_reason": (
                    "sufficient_evidence"
                ),
                "next_probe": None,
                "conclusion": {
                    "root_cause": (
                        "Container memory pressure caused "
                        "the OOM termination"
                    ),
                    "confidence": 0.95,
                    "evidence_ids": evidence_ids,
                    "remaining_uncertainties": [],
                },
            }

        else:
            raise AssertionError(
                "Investigation performed unexpected extra reasoning"
            )

        return ChatResponse(
            content=json.dumps(
                payload
            ),
            model=(
                "scripted-investigation-v1"
            ),
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        )


class ScriptedKubernetesTool(
    BaseTool
):
    def __init__(
        self,
    ) -> None:
        self.calls: list[
            dict[str, Any]
        ] = []

    @property
    def name(
        self,
    ) -> str:
        return "kubernetes"

    async def execute(
        self,
        action: str,
        resource: str,
        target: str,
        **kwargs: Any,
    ) -> dict:
        self.calls.append(
            {
                "action": action,
                "resource": resource,
                "target": target,
                **kwargs,
            }
        )

        return {
            "success": True,
            "source": "kubernetes",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": NOW.isoformat(),
            "data": {
                "phase": "Running",
                "ready": False,
                "scheduled": True,
                "oom_killed": True,
                "containers": [
                    {
                        "restart_count": 7,
                        "state_reason": (
                            "CrashLoopBackOff"
                        ),
                        "last_termination_reason": (
                            "OOMKilled"
                        ),
                    }
                ],
            },
        }


class ScriptedPrometheusTool(
    BaseTool
):
    def __init__(
        self,
    ) -> None:
        self.calls: list[
            dict[str, Any]
        ] = []

    @property
    def name(
        self,
    ) -> str:
        return "prometheus"

    async def execute(
        self,
        query: str,
        **kwargs: Any,
    ) -> dict:
        self.calls.append(
            {
                "query": query,
                **kwargs,
            }
        )

        return {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": NOW.isoformat(),
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "pod": "payment-api",
                            "namespace": "payment",
                        },
                        "value": [
                            1786333800,
                            "503316480",
                        ],
                    }
                ],
            },
        }


def create_scripted_tool_manager():
    registry = ToolRegistry()

    kubernetes = (
        ScriptedKubernetesTool()
    )

    prometheus = (
        ScriptedPrometheusTool()
    )

    registry.register(
        kubernetes
    )

    registry.register(
        prometheus
    )

    return (
        ToolManager(
            registry
        ),
        kubernetes,
        prometheus,
    )


def create_scripted_gateway():
    provider = (
        ScriptedInvestigationProvider()
    )

    client = LLMClient(
        provider
    )

    gateway = LLMGateway(
        clients={
            "openai": client,
        },
        rate_limiter=RateLimiter(
            enabled=False,
        ),
    )

    return (
        gateway,
        provider,
    )


def enabled_settings():
    return InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=InvestigationLimits(
            max_iterations=4,
            max_tool_calls=3,
            timeout_seconds=10,
        ),
    )


def disabled_authentication_service():
    return create_authentication_service(
        AuthenticationConfig()
    )


def isolate_optional_production_components(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        lambda: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_production_executor",
        lambda **_: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_production_pilot_live_readiness_probe",
        lambda: None,
    )


def build_event():
    return StandardEvent(
        header=Header(
            source=(
                EventSource.ALERTMANAGER
            ),
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message=(
                "payment-api restarted after OOM"
            ),
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


def build_enabled_runtime(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.chdir(
        tmp_path
    )

    isolate_optional_production_components(
        monkeypatch
    )

    (
        tool_manager,
        kubernetes,
        prometheus,
    ) = create_scripted_tool_manager()

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        lambda: tool_manager,
    )

    (
        gateway,
        provider,
    ) = create_scripted_gateway()

    adapter = (
        InvestigationLLMGatewayAdapter(
            gateway
        )
    )

    reasoner = (
        LLMInvestigationReasoner(
            adapter
        )
    )

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        llm_gateway=gateway,
        investigation_reasoner=reasoner,
        investigation_settings=(
            enabled_settings()
        ),
    )

    return (
        runtime,
        provider,
        kubernetes,
        prometheus,
    )


@pytest.mark.asyncio
async def test_explicit_shadow_execution_runs_full_llm_evidence_loop(
    monkeypatch,
    tmp_path,
):
    (
        runtime,
        provider,
        kubernetes,
        prometheus,
    ) = build_enabled_runtime(
        monkeypatch,
        tmp_path,
    )

    async def forbidden_pipeline_call(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Investigation Shadow called PlannerPipeline"
        )

    async def forbidden_action_call(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Investigation Shadow called ActionRuntime"
        )

    monkeypatch.setattr(
        runtime.pipeline,
        "execute",
        forbidden_pipeline_call,
    )

    monkeypatch.setattr(
        runtime.action_runtime,
        "execute",
        forbidden_action_call,
    )

    context = AgentContext(
        event=build_event(),
        memory=runtime.memory,
        tools=runtime.tools,
        skills=runtime.skills,
        mcp=runtime.mcp,
        sandbox=runtime.sandbox,
        sandbox_policy=(
            runtime.sandbox_policy
        ),
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

    incident_before = (
        context.incident.model_dump(
            mode="json"
        )
    )

    result = await (
        runtime.run_investigation_shadow(
            context
        )
    )

    assert result.status == (
        InvestigationStatus.CONCLUDED
    )

    assert result.stop_reason == (
        InvestigationStopReason.SUFFICIENT_EVIDENCE
    )

    assert result.iteration_count == 3
    assert result.tool_call_count == 2

    assert len(
        provider.requests
    ) == 3

    assert len(
        kubernetes.calls
    ) == 1

    assert len(
        prometheus.calls
    ) == 1

    assert kubernetes.calls[0] == {
        "action": "describe",
        "resource": "pod",
        "target": "payment-api",
        "namespace": "payment",
        "cluster": "production-a",
    }

    query = prometheus.calls[0][
        "query"
    ]

    assert (
        "container_memory_working_set_bytes"
        in query
    )

    assert (
        'pod="payment-api"'
        in query
    )

    assert (
        'namespace="payment"'
        in query
    )

    assert (
        'cluster="production-a"'
        in query
    )

    assert len(
        result.evidence
    ) == 2

    assert all(
        item.trusted
        for item in result.evidence
    )

    assert result.conclusion is not None

    assert (
        result.conclusion.confidence
        == 0.95
    )

    assert len(
        result.conclusion.evidence_ids
    ) == 2

    assert context.metadata[
        "existing"
    ] == "preserved"

    assert (
        "investigation_shadow"
        in context.metadata
    )

    assert context.variables == {
        "existing": "preserved",
    }

    assert context.results == {
        "existing": "preserved",
    }

    assert (
        context.incident.model_dump(
            mode="json"
        )
        == incident_before
    )

    assert context.executions == []
    assert context.evaluations == []

    assert (
        runtime.registry.get(
            "noise"
        ).llm_gateway
        is runtime.llm_gateway
    )

    assert (
        runtime.registry.get(
            "rca"
        ).llm_gateway
        is runtime.llm_gateway
    )

    assert (
        runtime.registry.get(
            "healing"
        ).llm_gateway
        is runtime.llm_gateway
    )

    investigation_adapter = (
        runtime
        .investigation_coordinator
        .reasoner
        .investigation_llm
    )

    assert (
        investigation_adapter.llm_gateway
        is runtime.llm_gateway
    )


@pytest.mark.asyncio
async def test_disabled_runtime_rejects_explicit_shadow_execution(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    isolate_optional_production_components(
        monkeypatch
    )

    (
        tool_manager,
        _,
        _,
    ) = create_scripted_tool_manager()

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        lambda: tool_manager,
    )

    (
        gateway,
        provider,
    ) = create_scripted_gateway()

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        llm_gateway=gateway,
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    context = AgentContext(
        event=build_event(),
        tools=runtime.tools,
    )

    with pytest.raises(
        RuntimeError,
        match="Shadow is disabled",
    ):
        await runtime.run_investigation_shadow(
            context
        )

    assert provider.requests == []


@pytest.mark.asyncio
async def test_shadow_execution_rejects_non_runtime_tool_manager(
    monkeypatch,
    tmp_path,
):
    (
        runtime,
        provider,
        _,
        _,
    ) = build_enabled_runtime(
        monkeypatch,
        tmp_path,
    )

    (
        foreign_tools,
        _,
        _,
    ) = create_scripted_tool_manager()

    context = AgentContext(
        event=build_event(),
        tools=foreign_tools,
    )

    with pytest.raises(
        TypeError,
        match="shared Runtime tools",
    ):
        await runtime.run_investigation_shadow(
            context
        )

    assert provider.requests == []
