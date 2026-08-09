from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.investigation.multi_cluster_readiness import (
    ProductionMultiClusterReadinessError,
)
from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


class NeverCalledCoordinator:
    def __init__(
        self,
    ) -> None:
        self.calls = 0
        self.require_cluster_verified_evidence = (
            False
        )

    async def investigate(
        self,
        context,
    ):
        self.calls += 1
        raise AssertionError(
            "Coordinator must not run when readiness fails"
        )


class ReadyCoordinator:
    def __init__(
        self,
    ) -> None:
        self.calls = 0
        self.require_cluster_verified_evidence = (
            False
        )

    async def investigate(
        self,
        context,
    ):
        self.calls += 1
        return SimpleNamespace(
            status="ok"
        )


class FakeReadiness:
    def __init__(
        self,
        *,
        ready,
    ) -> None:
        self.ready = ready
        self.calls = 0

    def evaluate_event(
        self,
        event,
    ):
        self.calls += 1

        return SimpleNamespace(
            ready=self.ready,
            snapshot=lambda: {
                "schema_version": "v1",
                "read_only": True,
                "decision_influence": False,
                "applicable": True,
                "ready": self.ready,
                "cluster": "prod-us-03",
                "issues": (
                    []
                    if self.ready
                    else [
                        "prometheus_registry_missing"
                    ]
                ),
            },
        )


def _base_runtime(
    monkeypatch,
    tmp_path,
    coordinator,
):
    monkeypatch.chdir(
        tmp_path
    )

    monkeypatch.setattr(
        runtime_module,
        "create_investigation_coordinator",
        lambda **_: coordinator,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
        lambda: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_prometheus_cluster_registry",
        lambda: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        lambda **_: ToolManager(
            ToolRegistry()
        ),
    )

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

    return runtime_module.AgentRuntime(
        authentication_service=(
            create_authentication_service(
                AuthenticationConfig()
            )
        ),
        investigation_settings=(
            InvestigationSettings(
                enabled=False
            )
        ),
    )


def shadow_context(
    runtime,
):
    return AgentContext.model_construct(
        event=SimpleNamespace(
            resources=[
                SimpleNamespace(
                    cluster="prod-us-03"
                )
            ]
        ),
        tools=runtime.tools,
        metadata={},
    )


@pytest.mark.asyncio
async def test_runtime_strict_shadow_fails_before_coordinator_when_readiness_not_ready(
    monkeypatch,
    tmp_path,
):
    coordinator = NeverCalledCoordinator()

    runtime = _base_runtime(
        monkeypatch,
        tmp_path,
        coordinator,
    )

    runtime.investigation_coordinator = (
        coordinator
    )

    runtime.cluster_verified_evidence_required = (
        True
    )

    runtime.production_multi_cluster_readiness = (
        FakeReadiness(
            ready=False
        )
    )

    context = shadow_context(
        runtime
    )

    with pytest.raises(
        ProductionMultiClusterReadinessError,
    ):
        await runtime.run_investigation_shadow(
            context
        )

    assert coordinator.calls == 0

    assert (
        context.metadata[
            "production_multi_cluster_readiness"
        ][
            "ready"
        ]
        is False
    )


@pytest.mark.asyncio
async def test_runtime_strict_shadow_runs_coordinator_after_readiness_passes(
    monkeypatch,
    tmp_path,
):
    coordinator = ReadyCoordinator()

    runtime = _base_runtime(
        monkeypatch,
        tmp_path,
        coordinator,
    )

    runtime.investigation_coordinator = (
        coordinator
    )

    runtime.cluster_verified_evidence_required = (
        True
    )

    runtime.production_multi_cluster_readiness = (
        FakeReadiness(
            ready=True
        )
    )

    context = shadow_context(
        runtime
    )

    result = await runtime.run_investigation_shadow(
        context
    )

    assert result.status == "ok"
    assert coordinator.calls == 1

    assert (
        context.metadata[
            "production_multi_cluster_readiness"
        ][
            "ready"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_runtime_legacy_shadow_does_not_require_readiness_gate(
    monkeypatch,
    tmp_path,
):
    coordinator = ReadyCoordinator()

    runtime = _base_runtime(
        monkeypatch,
        tmp_path,
        coordinator,
    )

    runtime.investigation_coordinator = (
        coordinator
    )

    runtime.cluster_verified_evidence_required = (
        False
    )

    runtime.production_multi_cluster_readiness = (
        FakeReadiness(
            ready=False
        )
    )

    context = shadow_context(
        runtime
    )

    result = await runtime.run_investigation_shadow(
        context
    )

    assert result.status == "ok"
    assert coordinator.calls == 1

    assert (
        "production_multi_cluster_readiness"
        not in context.metadata
    )
