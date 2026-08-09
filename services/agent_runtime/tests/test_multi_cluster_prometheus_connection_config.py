from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import services.agent_runtime.app.runtime.runtime as runtime_module
import services.agent_runtime.app.tools.prometheus.connection_factory as factory_module
import services.agent_runtime.app.tools.prometheus.tool as prometheus_tool_module

from common.config.settings import (
    AuthenticationConfig,
    ConnectionsConfig,
    PrometheusReadClusterBindingConfig,
    PrometheusReadEndpointConfig,
    PrometheusReadMultiClusterConfig,
)

from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.prometheus.connection_factory import (
    PrometheusReadConnectionFactoryConfigurationError,
    create_prometheus_cluster_registry,
)
from services.agent_runtime.app.tools.prometheus.router import (
    PrometheusClusterRegistry,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusConfigurationError,
    PrometheusTool,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


def endpoint(
    *,
    name="central-metrics",
    base_url="https://metrics.example.internal",
    authentication="none",
    bearer_token_env=None,
    bearer_token_file=None,
    ca_file=None,
):
    return PrometheusReadEndpointConfig(
        endpoint_name=name,
        base_url=base_url,
        authentication=authentication,
        bearer_token_env=(
            bearer_token_env
        ),
        bearer_token_file=(
            bearer_token_file
        ),
        ca_file=ca_file,
    )


def binding(
    cluster,
    endpoint_name="central-metrics",
):
    return PrometheusReadClusterBindingConfig(
        cluster_name=cluster,
        endpoint_name=endpoint_name,
    )


def enabled_config(
    *,
    endpoints,
    bindings,
):
    return PrometheusReadMultiClusterConfig(
        enabled=True,
        endpoints=endpoints,
        cluster_bindings=bindings,
    )


def test_connections_default_keeps_prometheus_multi_cluster_read_disabled():
    connections = ConnectionsConfig()

    assert (
        connections.prometheus_read.enabled
        is False
    )

    assert (
        connections.prometheus_read.endpoints
        == ()
    )

    assert (
        connections.prometheus_read.cluster_bindings
        == ()
    )


def test_endpoint_descriptor_rejects_raw_secret_and_insecure_url():
    with pytest.raises(
        ValidationError,
    ):
        PrometheusReadEndpointConfig.model_validate(
            {
                "endpoint_name": "central-metrics",
                "base_url": (
                    "https://metrics.example.internal"
                ),
                "authentication": "bearer",
                "bearer_token_env": (
                    "PROM_READ_TOKEN"
                ),
                "bearer_token": (
                    "raw-secret-must-not-be-configurable"
                ),
            }
        )

    with pytest.raises(
        ValidationError,
        match="clean HTTPS origin",
    ):
        endpoint(
            base_url=(
                "http://metrics.example.internal"
            ),
        )


def test_authentication_mode_is_explicit_and_fail_closed():
    with pytest.raises(
        ValidationError,
        match="cannot configure bearer-token references",
    ):
        endpoint(
            authentication="none",
            bearer_token_env=(
                "PROM_READ_TOKEN"
            ),
        )

    with pytest.raises(
        ValidationError,
        match="exactly one token source",
    ):
        endpoint(
            authentication="bearer",
        )

    with pytest.raises(
        ValidationError,
        match="exactly one token source",
    ):
        endpoint(
            authentication="bearer",
            bearer_token_env=(
                "PROM_READ_TOKEN"
            ),
            bearer_token_file=(
                "/run/secrets/prometheus-token"
            ),
        )


def test_enabled_config_requires_resolved_unique_bindings():
    with pytest.raises(
        ValidationError,
        match="at least one endpoint",
    ):
        PrometheusReadMultiClusterConfig(
            enabled=True,
            cluster_bindings=(
                binding(
                    "prod-sg-17"
                ),
            ),
        )

    with pytest.raises(
        ValidationError,
        match="at least one cluster binding",
    ):
        PrometheusReadMultiClusterConfig(
            enabled=True,
            endpoints=(
                endpoint(),
            ),
        )

    with pytest.raises(
        ValidationError,
        match="cluster bindings must use unique cluster names",
    ):
        enabled_config(
            endpoints=(
                endpoint(),
            ),
            bindings=(
                binding(
                    "prod-sg-17"
                ),
                binding(
                    "prod-sg-17"
                ),
            ),
        )

    with pytest.raises(
        ValidationError,
        match="unknown endpoint",
    ):
        enabled_config(
            endpoints=(
                endpoint(),
            ),
            bindings=(
                binding(
                    "prod-sg-17",
                    "missing-endpoint",
                ),
            ),
        )


def test_enabled_config_allows_multiple_clusters_to_share_one_endpoint():
    config = enabled_config(
        endpoints=(
            endpoint(),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
            binding(
                "prod-us-03"
            ),
        ),
    )

    assert len(
        config.endpoints
    ) == 1

    assert len(
        config.cluster_bindings
    ) == 2


def test_disabled_factory_does_not_touch_credentials_or_ca_files():
    disabled = PrometheusReadMultiClusterConfig(
        enabled=False,
        endpoints=(
            endpoint(
                authentication="bearer",
                bearer_token_env=(
                    "PROM_READ_TOKEN"
                ),
                ca_file=(
                    "/definitely/not/read/ca.pem"
                ),
            ),
        ),
        cluster_bindings=(
            binding(
                "prod-sg-17"
            ),
        ),
    )

    class ExplodingEnvironment(
        dict
    ):
        def get(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "disabled config must not read environment"
            )

    def exploding_reader(
        path,
    ):
        raise AssertionError(
            "disabled config must not read token files"
        )

    result = create_prometheus_cluster_registry(
        disabled,
        environment=ExplodingEnvironment(),
        token_file_reader=exploding_reader,
    )

    assert result is None


def test_central_endpoint_builds_one_tool_shared_by_multiple_clusters_without_network():
    config = enabled_config(
        endpoints=(
            endpoint(),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
            binding(
                "prod-us-03"
            ),
        ),
    )

    registry = create_prometheus_cluster_registry(
        config,
        environment={
            "PROMETHEUS_BEARER_TOKEN": (
                "ambient-token-must-not-be-inherited"
            ),
        },
    )

    assert isinstance(
        registry,
        PrometheusClusterRegistry,
    )

    sg = registry.resolve(
        "prod-sg-17"
    )[
        1
    ]

    us = registry.resolve(
        "prod-us-03"
    )[
        1
    ]

    assert sg is us

    assert sg.base_url == (
        "https://metrics.example.internal"
    )

    assert sg.verify_tls is True

    assert (
        sg.allow_mock_fallback
        is False
    )

    assert sg.bearer_token == ""

    assert sg.client is None


def test_bearer_environment_reference_is_resolved_without_serializing_token():
    config = enabled_config(
        endpoints=(
            endpoint(
                authentication="bearer",
                bearer_token_env=(
                    "PROM_READ_TOKEN"
                ),
            ),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
        ),
    )

    payload = config.model_dump()

    assert (
        payload[
            "endpoints"
        ][
            0
        ][
            "bearer_token_env"
        ]
        == "PROM_READ_TOKEN"
    )

    assert (
        "bearer_token"
        not in payload[
            "endpoints"
        ][
            0
        ]
    )

    registry = create_prometheus_cluster_registry(
        config,
        environment={
            "PROM_READ_TOKEN": (
                "prom-read-token-1234567890"
            ),
        },
    )

    tool = registry.resolve(
        "prod-sg-17"
    )[
        1
    ]

    assert tool.bearer_token == (
        "prom-read-token-1234567890"
    )


def test_token_file_and_ca_references_are_resolved_locally(
    tmp_path: Path,
):
    token_file = (
        tmp_path
        / "token"
    )

    token_file.write_text(
        "prom-file-token-1234567890\n",
        encoding="utf-8",
    )

    ca_file = (
        tmp_path
        / "ca.crt"
    )

    ca_file.write_text(
        "unit-test-ca-placeholder",
        encoding="utf-8",
    )

    config = enabled_config(
        endpoints=(
            endpoint(
                authentication="bearer",
                bearer_token_file=str(
                    token_file
                ),
                ca_file=str(
                    ca_file
                ),
            ),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
        ),
    )

    registry = create_prometheus_cluster_registry(
        config
    )

    tool = registry.resolve(
        "prod-sg-17"
    )[
        1
    ]

    assert tool.bearer_token == (
        "prom-file-token-1234567890"
    )

    assert tool.ca_file == str(
        ca_file
    )


def test_missing_bearer_environment_reference_fails_without_secret_value():
    config = enabled_config(
        endpoints=(
            endpoint(
                authentication="bearer",
                bearer_token_env=(
                    "PROM_READ_TOKEN"
                ),
            ),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
        ),
    )

    with pytest.raises(
        PrometheusReadConnectionFactoryConfigurationError,
        match="environment variable is missing",
    ) as captured:
        create_prometheus_cluster_registry(
            config,
            environment={},
        )

    assert (
        "prom-read-token-1234567890"
        not in str(
            captured.value
        )
    )


def test_invalid_ca_reference_fails_before_registry_is_returned(
    tmp_path: Path,
):
    config = enabled_config(
        endpoints=(
            endpoint(
                ca_file=str(
                    tmp_path
                    / "missing-ca.crt"
                ),
            ),
        ),
        bindings=(
            binding(
                "prod-sg-17"
            ),
        ),
    )

    with pytest.raises(
        PrometheusReadConnectionFactoryConfigurationError,
        match="CA file is unavailable",
    ):
        create_prometheus_cluster_registry(
            config
        )


def test_prometheus_tool_ca_file_requires_tls_verification():
    with pytest.raises(
        PrometheusConfigurationError,
        match="requires TLS verification",
    ):
        PrometheusTool(
            base_url=(
                "https://metrics.example.internal"
            ),
            verify_tls=False,
            allow_mock_fallback=False,
            bearer_token="",
            ca_file="/tmp/ca.pem",
        )


def test_runtime_uses_prometheus_config_factory_when_registry_not_explicit(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    configured_registry = (
        PrometheusClusterRegistry(
            {
                "prod-sg-17": PrometheusTool(
                    base_url=(
                        "https://metrics.example.internal"
                    ),
                    verify_tls=True,
                    bearer_token="",
                    allow_mock_fallback=False,
                )
            }
        )
    )

    prometheus_factory_calls = []

    def prometheus_factory():
        prometheus_factory_calls.append(
            True
        )

        return configured_registry

    manager_calls = []

    def manager_factory(
        **kwargs,
    ):
        manager_calls.append(
            dict(
                kwargs
            )
        )

        return ToolManager(
            ToolRegistry()
        )

    monkeypatch.setattr(
        runtime_module,
        "create_prometheus_cluster_registry",
        prometheus_factory,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
        lambda: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        manager_factory,
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
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    assert prometheus_factory_calls == [
        True
    ]

    assert (
        runtime.prometheus_cluster_registry
        is configured_registry
    )

    assert manager_calls == [
        {
            "prometheus_cluster_registry": (
                configured_registry
            )
        }
    ]


def test_explicit_runtime_prometheus_registry_bypasses_config_factory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    explicit_registry = (
        PrometheusClusterRegistry(
            {
                "prod-explicit-01": PrometheusTool(
                    base_url=(
                        "https://explicit-metrics.example.internal"
                    ),
                    verify_tls=True,
                    bearer_token="",
                    allow_mock_fallback=False,
                )
            }
        )
    )

    def forbidden_prometheus_factory():
        raise AssertionError(
            "explicit Prometheus registry must bypass config factory"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_prometheus_cluster_registry",
        forbidden_prometheus_factory,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
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

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            create_authentication_service(
                AuthenticationConfig()
            )
        ),
        prometheus_cluster_registry=(
            explicit_registry
        ),
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    assert (
        runtime.prometheus_cluster_registry
        is explicit_registry
    )


def test_connection_factory_module_has_no_write_authority():
    source = Path(
        factory_module.__file__
    ).read_text(
        encoding="utf-8"
    )

    forbidden = [
        "ActionRuntime",
        "ApprovalService",
        "VerificationRuntime",
        "KubernetesProductionExecutor",
        "KubernetesPreflightResolver",
        ".post(",
        ".patch(",
        ".put(",
        ".delete(",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []


def test_prometheus_tool_ca_support_is_read_only_transport_configuration():
    source = Path(
        prometheus_tool_module.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "ssl.create_default_context"
        in source
    )

    assert (
        "verify=self._tls_verify_value"
        in source
    )
