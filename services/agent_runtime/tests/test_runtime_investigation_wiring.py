from pathlib import Path

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.factory import (
    InvestigationFactoryError,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationDecision,
    InvestigationLimits,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
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


class FakeReasoner(BaseInvestigationReasoner):
    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        raise AssertionError(
            "Runtime wiring must not run the reasoner"
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
    monkeypatch.chdir(tmp_path)
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


def test_default_runtime_keeps_investigation_disabled(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    assert runtime.investigation_settings.enabled is False
    assert runtime.investigation_coordinator is None
    assert not hasattr(
        runtime.pipeline,
        "investigation_coordinator",
    )
    assert runtime.registry.names() == [
        "noise",
        "diagnosis",
        "rca",
        "healing",
        "change",
    ]
    assert "investigation" not in runtime.registry.names()


def test_explicit_enabled_runtime_owns_coordinator_without_pipeline_wiring(
    monkeypatch,
    tmp_path,
):
    reasoner = FakeReasoner()
    settings = InvestigationSettings(
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

    runtime = create_runtime(
        monkeypatch,
        tmp_path,
        investigation_reasoner=reasoner,
        investigation_settings=settings,
    )

    assert runtime.investigation_settings is settings
    assert isinstance(
        runtime.investigation_coordinator,
        EvidenceDrivenInvestigationCoordinator,
    )
    assert (
        runtime.investigation_coordinator.reasoner
        is reasoner
    )
    assert (
        runtime.investigation_coordinator.limits
        is settings.limits
    )
    assert not hasattr(
        runtime.pipeline,
        "investigation_coordinator",
    )


def test_enabled_environment_without_reasoner_fails_before_components(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "AGENT_INVESTIGATION_SHADOW_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "AGENT_INVESTIGATION_SHADOW_ACKNOWLEDGEMENT",
        INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    )

    component_calls = 0

    def unexpected_component_creation():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError(
            "Investigation failure created a Runtime component"
        )

    monkeypatch.setattr(
        runtime_module,
        "MemoryStore",
        unexpected_component_creation,
    )

    with pytest.raises(
        InvestigationFactoryError,
        match="requires a reasoner",
    ):
        runtime_module.AgentRuntime(
            authentication_service=(
                disabled_authentication_service()
            )
        )

    assert component_calls == 0


def test_invalid_reasoner_fails_before_authentication_factory(
    monkeypatch,
):
    authentication_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_calls
        authentication_calls += 1
        raise AssertionError(
            "Invalid reasoner called authentication factory"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )

    with pytest.raises(
        TypeError,
        match="reasoner is invalid",
    ):
        runtime_module.AgentRuntime(
            investigation_reasoner=object()
        )

    assert authentication_calls == 0


def test_invalid_settings_fail_before_authentication_factory(
    monkeypatch,
):
    authentication_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_calls
        authentication_calls += 1
        raise AssertionError(
            "Invalid settings called authentication factory"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )

    with pytest.raises(
        TypeError,
        match="settings are invalid",
    ):
        runtime_module.AgentRuntime(
            investigation_settings=object()
        )

    assert authentication_calls == 0

