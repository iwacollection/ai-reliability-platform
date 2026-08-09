from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module
import services.agent_runtime.app.tools.prometheus.router as router_module

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
)
from services.agent_runtime.app.investigation.probes import (
    ReadOnlyInvestigationProbeExecutor,
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
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.prometheus.router import (
    MultiClusterPrometheusToolRouter,
    PrometheusClusterRegistry,
    PrometheusClusterRoutingError,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


NOW = datetime(
    2026,
    8,
    11,
    4,
    30,
    tzinfo=UTC,
)


class RecordingPrometheusTool(
    PrometheusTool
):
    def __init__(
        self,
        endpoint_name: str,
    ) -> None:
        super().__init__(
            base_url=(
                f"https://{endpoint_name}.prometheus.test"
            ),
            verify_tls=True,
            allow_mock_fallback=False,
        )

        self.endpoint_name = (
            endpoint_name
        )

        self.calls: list[
            dict[str, Any]
        ] = []

    async def execute(
        self,
        query: str,
        time=None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        call = {
            "query": query,
        }

        if time is not None:
            call[
                "time"
            ] = time

        call.update(
            kwargs
        )

        self.calls.append(
            call
        )

        return {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "observed_at": (
                NOW.isoformat()
            ),
            "query": query,
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "endpoint": (
                                self.endpoint_name
                            ),
                        },
                        "value": [
                            NOW.timestamp(),
                            "7",
                        ],
                    }
                ],
            },
            "warnings": [],
        }


def endpoints():
    sg = RecordingPrometheusTool(
        "sg"
    )

    us = RecordingPrometheusTool(
        "us"
    )

    return sg, us


def test_registry_is_immutable_exact_cluster_mapping():
    sg, us = endpoints()

    registry = PrometheusClusterRegistry(
        {
            "prod-sg-17": sg,
            "prod-us-03": us,
        }
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
        == (
            "prod-sg-17",
            sg,
        )
    )

    assert not hasattr(
        registry,
        "register",
    )


def test_registry_allows_shared_central_endpoint_for_multiple_clusters():
    central = RecordingPrometheusTool(
        "central"
    )

    registry = PrometheusClusterRegistry(
        {
            "prod-sg-17": central,
            "prod-us-03": central,
        }
    )

    assert (
        registry.resolve(
            "prod-sg-17"
        )[
            1
        ]
        is central
    )

    assert (
        registry.resolve(
            "prod-us-03"
        )[
            1
        ]
        is central
    )


@pytest.mark.parametrize(
    "tool",
    [
        PrometheusTool(
            base_url=None,
            allow_mock_fallback=False,
        ),
        PrometheusTool(
            base_url=(
                "https://mock-fallback.prometheus.test"
            ),
            allow_mock_fallback=True,
        ),
        PrometheusTool(
            base_url=(
                "https://insecure.prometheus.test"
            ),
            verify_tls=False,
            allow_mock_fallback=False,
        ),
    ],
)
def test_registry_rejects_unsafe_live_endpoint_bindings(
    tool,
):
    with pytest.raises(
        PrometheusClusterRoutingError,
    ):
        PrometheusClusterRegistry(
            {
                "prod-sg-17": tool,
            }
        )


@pytest.mark.asyncio
async def test_router_selects_exact_requested_metrics_endpoint():
    sg, us = endpoints()

    router = (
        MultiClusterPrometheusToolRouter(
            PrometheusClusterRegistry(
                {
                    "prod-sg-17": sg,
                    "prod-us-03": us,
                }
            )
        )
    )

    result = await router.execute(
        cluster="prod-us-03",
        query=(
            'up{cluster="prod-us-03"}'
        ),
    )

    assert result[
        "cluster"
    ] == "prod-us-03"

    assert sg.calls == []

    assert us.calls == [
        {
            "query": (
                'up{cluster="prod-us-03"}'
            ),
        }
    ]


@pytest.mark.asyncio
async def test_unknown_cluster_fails_before_any_prometheus_call():
    sg, us = endpoints()

    router = (
        MultiClusterPrometheusToolRouter(
            PrometheusClusterRegistry(
                {
                    "prod-sg-17": sg,
                    "prod-us-03": us,
                }
            )
        )
    )

    with pytest.raises(
        PrometheusClusterRoutingError,
        match="not registered",
    ):
        await router.execute(
            cluster="prod-eu-05",
            query="up",
        )

    assert sg.calls == []
    assert us.calls == []


@pytest.mark.asyncio
async def test_multiple_metrics_clusters_require_explicit_cluster():
    sg, us = endpoints()

    router = (
        MultiClusterPrometheusToolRouter(
            PrometheusClusterRegistry(
                {
                    "prod-sg-17": sg,
                    "prod-us-03": us,
                }
            )
        )
    )

    with pytest.raises(
        PrometheusClusterRoutingError,
        match="cluster is required",
    ):
        await router.execute(
            query="up",
        )

    assert sg.calls == []
    assert us.calls == []


@pytest.mark.asyncio
async def test_single_metrics_cluster_keeps_missing_cluster_compatibility():
    sg, _ = endpoints()

    router = (
        MultiClusterPrometheusToolRouter(
            PrometheusClusterRegistry(
                {
                    "prod-sg-17": sg,
                }
            )
        )
    )

    result = await router.execute(
        query="up",
    )

    assert result[
        "cluster"
    ] == "prod-sg-17"

    assert len(
        sg.calls
    ) == 1


@pytest.mark.asyncio
async def test_probe_cluster_routes_to_matching_prometheus_endpoint():
    sg, us = endpoints()

    registry = ToolRegistry()

    registry.register(
        MultiClusterPrometheusToolRouter(
            PrometheusClusterRegistry(
                {
                    "prod-sg-17": sg,
                    "prod-us-03": us,
                }
            )
        )
    )

    context = SimpleNamespace(
        tools=ToolManager(
            registry
        ),
        trace=None,
    )

    scope = InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message=(
            "device gateway restart rate is elevated"
        ),
        event_occurred_at=NOW,
        resource=(
            "device-gateway-xyz789"
        ),
        namespace="fleet-edge",
        cluster="prod-us-03",
    )

    evidence = await (
        ReadOnlyInvestigationProbeExecutor()
        .collect(
            context,
            scope,
            (
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
        )
    )

    assert sg.calls == []

    assert len(
        us.calls
    ) == 1

    assert (
        'cluster="prod-us-03"'
        in us.calls[
            0
        ][
            "query"
        ]
    )

    assert evidence.source == (
        "prometheus"
    )

    assert evidence.trusted is True


def test_default_tool_factory_preserves_legacy_prometheus_singleton():
    manager = create_tool_manager()

    prometheus = (
        manager.registry.get(
            "prometheus"
        )
    )

    assert isinstance(
        prometheus,
        PrometheusTool,
    )

    assert not isinstance(
        prometheus,
        MultiClusterPrometheusToolRouter,
    )


def test_explicit_prometheus_registry_switches_factory_to_router():
    sg, us = endpoints()

    clusters = PrometheusClusterRegistry(
        {
            "prod-sg-17": sg,
            "prod-us-03": us,
        }
    )

    manager = create_tool_manager(
        prometheus_cluster_registry=(
            clusters
        )
    )

    prometheus = (
        manager.registry.get(
            "prometheus"
        )
    )

    assert isinstance(
        prometheus,
        MultiClusterPrometheusToolRouter,
    )

    assert (
        prometheus.clusters
        is clusters
    )


def test_runtime_rejects_invalid_prometheus_registry_before_factories(
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
        match="Prometheus cluster registry",
    ):
        runtime_module.AgentRuntime(
            prometheus_cluster_registry=object(),
        )

    assert authentication_calls == 0


def test_runtime_passes_explicit_prometheus_registry_only_when_opted_in(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    sg, us = endpoints()

    clusters = PrometheusClusterRegistry(
        {
            "prod-sg-17": sg,
            "prod-us-03": us,
        }
    )

    captured = []

    def routed_manager_factory(
        *,
        prometheus_cluster_registry,
    ):
        captured.append(
            prometheus_cluster_registry
        )

        return ToolManager(
            ToolRegistry()
        )

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        routed_manager_factory,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
        lambda: None,
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
        prometheus_cluster_registry=(
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
        runtime.prometheus_cluster_registry
        is clusters
    )

    assert isinstance(
        runtime.tools,
        ToolManager,
    )


def test_prometheus_router_module_has_no_write_authority():
    source = router_module.__file__

    assert source is not None

    text = Path(
        source
    ).read_text(
        encoding="utf-8"
    )

    forbidden = [
        "ActionRuntime",
        "ApprovalService",
        "VerificationRuntime",
        "KubernetesProductionExecutor",
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
