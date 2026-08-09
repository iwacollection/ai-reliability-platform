from pathlib import Path

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)
from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)
from services.agent_runtime.app.registry.factory import (
    create_agent_registry,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)


INVESTIGATION_ENV_NAMES = [
    "AGENT_INVESTIGATION_SHADOW_ENABLED",
    "AGENT_INVESTIGATION_SHADOW_ACKNOWLEDGEMENT",
    "AGENT_INVESTIGATION_MAX_ITERATIONS",
    "AGENT_INVESTIGATION_MAX_TOOL_CALLS",
    "AGENT_INVESTIGATION_TIMEOUT_SECONDS",
]


def shared_gateway() -> LLMGateway:
    """
    Create a real Gateway object without a callable provider.

    These ownership tests never execute chat(), so an empty client registry
    safely proves object identity without an external LLM request.
    """

    return LLMGateway(
        clients={},
    )


def disabled_authentication_service():
    return create_authentication_service(
        AuthenticationConfig()
    )


def clear_investigation_environment(
    monkeypatch,
):
    for name in INVESTIGATION_ENV_NAMES:
        monkeypatch.delenv(
            name,
            raising=False,
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


def create_runtime(
    monkeypatch,
    tmp_path: Path,
    **kwargs,
):
    monkeypatch.chdir(
        tmp_path
    )

    clear_investigation_environment(
        monkeypatch
    )

    isolate_optional_production_components(
        monkeypatch
    )

    return runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        **kwargs,
    )


def enabled_settings() -> InvestigationSettings:
    return InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=InvestigationLimits(
            max_iterations=3,
            max_tool_calls=4,
            timeout_seconds=8,
        ),
    )


def assert_registry_uses_gateway(
    registry,
    gateway,
):
    assert (
        registry.get(
            "noise"
        ).llm_gateway
        is gateway
    )

    assert (
        registry.get(
            "rca"
        ).llm_gateway
        is gateway
    )

    assert (
        registry.get(
            "healing"
        ).llm_gateway
        is gateway
    )


def test_registry_uses_one_injected_gateway_for_all_llm_agents():
    gateway = shared_gateway()

    registry = create_agent_registry(
        llm_gateway=gateway,
    )

    assert_registry_uses_gateway(
        registry,
        gateway,
    )

    assert registry.names() == [
        "noise",
        "diagnosis",
        "rca",
        "healing",
        "change",
    ]


def test_runtime_owns_and_injects_explicit_shared_gateway(
    monkeypatch,
    tmp_path,
):
    gateway = shared_gateway()

    runtime = create_runtime(
        monkeypatch,
        tmp_path,
        llm_gateway=gateway,
    )

    assert runtime.llm_gateway is gateway

    assert_registry_uses_gateway(
        runtime.registry,
        gateway,
    )

    assert (
        runtime.investigation_coordinator
        is None
    )


def test_enabled_llm_investigation_gateway_is_adopted_by_runtime(
    monkeypatch,
    tmp_path,
):
    gateway = shared_gateway()

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

    runtime = create_runtime(
        monkeypatch,
        tmp_path,
        investigation_reasoner=reasoner,
        investigation_settings=(
            enabled_settings()
        ),
    )

    assert runtime.llm_gateway is gateway

    assert (
        runtime.investigation_coordinator
        is not None
    )

    assert (
        runtime.investigation_coordinator.reasoner
        is reasoner
    )

    assert (
        reasoner.investigation_llm
        is adapter
    )

    assert (
        adapter.llm_gateway
        is runtime.llm_gateway
    )

    assert_registry_uses_gateway(
        runtime.registry,
        runtime.llm_gateway,
    )

    assert not hasattr(
        runtime.pipeline,
        "investigation_coordinator",
    )


def test_mismatched_investigation_and_runtime_gateway_fails_closed(
    monkeypatch,
):
    investigation_gateway = (
        shared_gateway()
    )

    runtime_gateway = (
        shared_gateway()
    )

    adapter = (
        InvestigationLLMGatewayAdapter(
            investigation_gateway
        )
    )

    reasoner = (
        LLMInvestigationReasoner(
            adapter
        )
    )

    authentication_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_calls

        authentication_calls += 1

        raise AssertionError(
            "Gateway mismatch created Runtime components"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )

    with pytest.raises(
        TypeError,
        match="must be shared",
    ):
        runtime_module.AgentRuntime(
            llm_gateway=runtime_gateway,
            investigation_reasoner=reasoner,
            investigation_settings=(
                enabled_settings()
            ),
        )

    assert authentication_calls == 0


def test_invalid_runtime_gateway_fails_before_components(
    monkeypatch,
):
    authentication_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_calls

        authentication_calls += 1

        raise AssertionError(
            "Invalid Gateway created Runtime components"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )

    with pytest.raises(
        TypeError,
        match="shared LLM gateway is invalid",
    ):
        runtime_module.AgentRuntime(
            llm_gateway=object(),
        )

    assert authentication_calls == 0
