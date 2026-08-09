from __future__ import annotations

from typing import Any

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module
import services.agent_runtime.app.tools.kubernetes.router as router_module

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.tools.factory import (
    create_tool_manager,
)
from services.agent_runtime.app.tools.kubernetes.change_tool import (
    KubernetesChangeTool,
)
from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
    KubernetesClusterRoutingError,
    MultiClusterKubernetesChangeToolRouter,
    MultiClusterKubernetesToolRouter,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


class RecordingKubernetesTool(
    KubernetesTool
):
    def __init__(
        self,
        cluster: str,
    ) -> None:
        super().__init__(
            api_url=(
                f"https://{cluster}.kubernetes.test"
            ),
            cluster_name=cluster,
            bearer_token=(
                f"{cluster}-unit-token-123456"
            ),
            allow_dry_run_fallback=False,
        )

        self.calls: list[
            dict[str, Any]
        ] = []

    async def execute(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            dict(
                kwargs
            )
        )

        return {
            "success": True,
            "source": "kubernetes",
            "mode": "read_only",
            "production_signal": True,
            "cluster": self.cluster_name,
            "observed_at": (
                "2026-08-11T03:00:00+00:00"
            ),
            "data": {
                "selected_cluster": (
                    self.cluster_name
                ),
            },
        }


class RecordingChangeTool:
    created = []

    def __init__(
        self,
        kubernetes,
    ) -> None:
        self.kubernetes = kubernetes
        self.calls = []

        type(
            self
        ).created.append(
            self
        )

    async def execute(
        self,
        **kwargs,
    ):
        self.calls.append(
            dict(
                kwargs
            )
        )

        return {
            "success": True,
            "source": "kubernetes_change",
            "mode": "read_only",
            "production_signal": True,
            "cluster": (
                self.kubernetes
                .cluster_name
            ),
            "observed_at": (
                "2026-08-11T03:00:00+00:00"
            ),
            "data": {
                "selected_cluster": (
                    self.kubernetes
                    .cluster_name
                ),
            },
        }


def cluster_tools():
    sg = RecordingKubernetesTool(
        "prod-sg-17"
    )

    us = RecordingKubernetesTool(
        "prod-us-03"
    )

    return sg, us


def test_registry_is_exact_immutable_startup_mapping():
    sg, us = cluster_tools()

    registry = KubernetesClusterRegistry(
        [
            sg,
            us,
        ]
    )

    assert registry.count == 2

    assert registry.cluster_names == (
        "prod-sg-17",
        "prod-us-03",
    )

    assert (
        registry.resolve(
            "prod-sg-17"
        )
        is sg
    )

    assert (
        registry.resolve(
            "prod-us-03"
        )
        is us
    )

    assert not hasattr(
        registry,
        "register",
    )


def test_registry_rejects_duplicate_or_unbound_cluster_tools():
    sg, _ = cluster_tools()

    with pytest.raises(
        KubernetesClusterRoutingError,
        match="Duplicate",
    ):
        KubernetesClusterRegistry(
            [
                sg,
                sg,
            ]
        )

    with pytest.raises(
        KubernetesClusterRoutingError,
        match="cluster_name",
    ):
        KubernetesClusterRegistry(
            [
                KubernetesTool(
                    api_url=(
                        "https://unbound.kubernetes.test"
                    ),
                    cluster_name=None,
                    bearer_token=(
                        "unbound-unit-token-123456"
                    ),
                    allow_dry_run_fallback=False,
                )
            ]
        )


def test_registry_rejects_cluster_without_live_endpoint():
    tool = KubernetesTool(
        api_url=None,
        cluster_name="prod-no-endpoint",
        bearer_token=(
            "no-endpoint-unit-token-123456"
        ),
        allow_dry_run_fallback=False,
    )

    tool.api_url = None

    with pytest.raises(
        KubernetesClusterRoutingError,
        match="API endpoint",
    ):
        KubernetesClusterRegistry(
            [
                tool
            ]
        )


@pytest.mark.asyncio
async def test_kubernetes_router_selects_exact_requested_cluster():
    sg, us = cluster_tools()

    registry = KubernetesClusterRegistry(
        [
            sg,
            us,
        ]
    )

    router = MultiClusterKubernetesToolRouter(
        registry
    )

    result = await router.execute(
        action="describe",
        resource="pod",
        target="device-gateway-xyz789",
        namespace="fleet-edge",
        cluster="prod-us-03",
    )

    assert result[
        "cluster"
    ] == "prod-us-03"

    assert sg.calls == []

    assert us.calls == [
        {
            "action": "describe",
            "resource": "pod",
            "target": (
                "device-gateway-xyz789"
            ),
            "namespace": "fleet-edge",
            "cluster": "prod-us-03",
        }
    ]


@pytest.mark.asyncio
async def test_unknown_cluster_fails_before_any_child_tool_call():
    sg, us = cluster_tools()

    router = (
        MultiClusterKubernetesToolRouter(
            KubernetesClusterRegistry(
                [
                    sg,
                    us,
                ]
            )
        )
    )

    with pytest.raises(
        KubernetesClusterRoutingError,
        match="not registered",
    ):
        await router.execute(
            action="describe",
            resource="pod",
            target="x",
            namespace="default",
            cluster="prod-eu-05",
        )

    assert sg.calls == []
    assert us.calls == []


@pytest.mark.asyncio
async def test_multiple_clusters_require_explicit_cluster():
    sg, us = cluster_tools()

    router = (
        MultiClusterKubernetesToolRouter(
            KubernetesClusterRegistry(
                [
                    sg,
                    us,
                ]
            )
        )
    )

    with pytest.raises(
        KubernetesClusterRoutingError,
        match="cluster is required",
    ):
        await router.execute(
            action="describe",
            resource="pod",
            target="x",
            namespace="default",
        )

    assert sg.calls == []
    assert us.calls == []


@pytest.mark.asyncio
async def test_single_cluster_router_keeps_missing_cluster_compatibility():
    sg, _ = cluster_tools()

    router = (
        MultiClusterKubernetesToolRouter(
            KubernetesClusterRegistry(
                [
                    sg
                ]
            )
        )
    )

    result = await router.execute(
        action="describe",
        resource="pod",
        target="printer-session-api-abc123",
        namespace="printing-control",
    )

    assert result[
        "cluster"
    ] == "prod-sg-17"

    assert sg.calls[
        0
    ][
        "cluster"
    ] == "prod-sg-17"


@pytest.mark.asyncio
async def test_change_router_uses_same_cluster_bound_kubernetes_tool(
    monkeypatch,
):
    RecordingChangeTool.created = []

    monkeypatch.setattr(
        router_module,
        "KubernetesChangeTool",
        RecordingChangeTool,
    )

    sg, us = cluster_tools()

    registry = KubernetesClusterRegistry(
        [
            sg,
            us,
        ]
    )

    router = (
        MultiClusterKubernetesChangeToolRouter(
            registry
        )
    )

    result = await router.execute(
        target="device-gateway-xyz789",
        namespace="fleet-edge",
        cluster="prod-us-03",
        incident_time=(
            "2026-08-11T03:00:00+00:00"
        ),
        view="workload",
    )

    assert result[
        "cluster"
    ] == "prod-us-03"

    selected = [
        item
        for item
        in RecordingChangeTool.created
        if (
            item.kubernetes
            is us
        )
    ]

    assert len(
        selected
    ) == 1

    assert selected[
        0
    ].calls == [
        {
            "target": (
                "device-gateway-xyz789"
            ),
            "namespace": "fleet-edge",
            "incident_time": (
                "2026-08-11T03:00:00+00:00"
            ),
            "view": "workload",
            "cluster": "prod-us-03",
        }
    ]


def test_default_tool_factory_preserves_single_cluster_tool_identity(
    monkeypatch,
):
    for name in (
        "KUBERNETES_API_URL",
        "KUBERNETES_CLUSTER_NAME",
        "KUBERNETES_BEARER_TOKEN",
        "KUBERNETES_TOKEN_FILE",
        "KUBERNETES_CA_FILE",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    manager = create_tool_manager()

    kubernetes = (
        manager.registry.get(
            "kubernetes"
        )
    )

    change = (
        manager.registry.get(
            "kubernetes_change"
        )
    )

    assert isinstance(
        kubernetes,
        KubernetesTool,
    )

    assert isinstance(
        change,
        KubernetesChangeTool,
    )

    assert (
        change.kubernetes
        is kubernetes
    )


def test_explicit_cluster_registry_switches_factory_to_router_tools():
    sg, us = cluster_tools()

    clusters = KubernetesClusterRegistry(
        [
            sg,
            us,
        ]
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            clusters
        )
    )

    assert isinstance(
        manager.registry.get(
            "kubernetes"
        ),
        MultiClusterKubernetesToolRouter,
    )

    assert isinstance(
        manager.registry.get(
            "kubernetes_change"
        ),
        MultiClusterKubernetesChangeToolRouter,
    )

    assert (
        manager.registry.get(
            "kubernetes"
        ).clusters
        is clusters
    )

    assert (
        manager.registry.get(
            "kubernetes_change"
        ).clusters
        is clusters
    )


def test_runtime_rejects_invalid_cluster_registry_before_factories(
    monkeypatch,
):
    authentication_calls = 0

    def forbidden_authentication():
        nonlocal authentication_calls
        authentication_calls += 1
        raise AssertionError(
            "authentication factory must not run"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        forbidden_authentication,
    )

    with pytest.raises(
        TypeError,
        match="cluster registry",
    ):
        runtime_module.AgentRuntime(
            kubernetes_cluster_registry=object(),
        )

    assert authentication_calls == 0


def test_runtime_passes_explicit_cluster_registry_only_when_opted_in(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    sg, us = cluster_tools()

    clusters = KubernetesClusterRegistry(
        [
            sg,
            us,
        ]
    )

    captured = []

    def routed_manager_factory(
        *,
        kubernetes_cluster_registry,
    ):
        captured.append(
            kubernetes_cluster_registry
        )

        registry = ToolRegistry()

        return ToolManager(
            registry
        )

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        routed_manager_factory,
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

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            create_authentication_service(
                AuthenticationConfig()
            )
        ),
        kubernetes_cluster_registry=(
            clusters
        ),
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    assert captured == [
        clusters
    ]

    assert (
        runtime.kubernetes_cluster_registry
        is clusters
    )

    assert isinstance(
        runtime.tools,
        ToolManager,
    )


def test_router_module_has_no_write_or_remediation_authority():
    source = (
        router_module.__file__
    )

    assert source is not None

    text = open(
        source,
        "r",
        encoding="utf-8",
    ).read()

    forbidden = [
        "ActionRuntime",
        "ApprovalService",
        "VerificationRuntime",
        ".post(",
        ".patch(",
        ".put(",
        ".delete(",
    ]

    assert [
        item
        for item in forbidden
        if item in text
    ] == []
