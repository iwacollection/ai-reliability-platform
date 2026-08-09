from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import services.agent_runtime.app.runtime.runtime as runtime_module
import services.agent_runtime.app.tools.kubernetes.connection_factory as factory_module

from common.config.settings import (
    AppConfig,
    ConnectionsConfig,
    KubernetesReadClusterConfig,
    KubernetesReadMultiClusterConfig,
    LLMConfig,
    RuntimeConfig,
    Settings,
)

from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from common.config.settings import (
    AuthenticationConfig,
)
from services.agent_runtime.app.tools.kubernetes.connection_factory import (
    KubernetesReadConnectionFactoryConfigurationError,
    create_kubernetes_cluster_registry,
)
from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


def cluster_config(
    *,
    name="prod-sg-17",
    api_url="https://sg-kubernetes.example.internal",
    bearer_token_env="K8S_PROD_SG_READ_TOKEN",
    bearer_token_file=None,
    ca_file=None,
):
    return KubernetesReadClusterConfig(
        cluster_name=name,
        api_url=api_url,
        bearer_token_env=(
            bearer_token_env
        ),
        bearer_token_file=(
            bearer_token_file
        ),
        ca_file=ca_file,
    )


def enabled_config(
    *clusters,
):
    return KubernetesReadMultiClusterConfig(
        enabled=True,
        clusters=clusters,
    )


def test_settings_default_keeps_multi_cluster_read_disabled():
    settings = Settings(
        app=AppConfig(
            name="test",
            version="1",
        ),
        llm=LLMConfig(
            provider="mock",
            temperature=0.0,
            timeout=30,
        ),
        runtime=RuntimeConfig(
            pipeline="sequential",
            max_workers=1,
        ),
    )

    assert isinstance(
        settings.connections,
        ConnectionsConfig,
    )

    assert (
        settings
        .connections
        .kubernetes_read
        .enabled
        is False
    )

    assert (
        settings
        .connections
        .kubernetes_read
        .clusters
        == ()
    )


def test_cluster_descriptor_rejects_raw_secret_and_insecure_url():
    with pytest.raises(
        ValidationError,
    ):
        KubernetesReadClusterConfig.model_validate(
            {
                "cluster_name": "prod-sg-17",
                "api_url": (
                    "https://sg-kubernetes.example.internal"
                ),
                "bearer_token": "raw-secret-must-not-be-configurable",
                "bearer_token_env": "K8S_PROD_SG_READ_TOKEN",
            }
        )

    with pytest.raises(
        ValidationError,
        match="clean HTTPS origin",
    ):
        cluster_config(
            api_url=(
                "http://sg-kubernetes.example.internal"
            ),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "bearer_token_env": None,
            "bearer_token_file": None,
        },
        {
            "bearer_token_env": "K8S_PROD_SG_READ_TOKEN",
            "bearer_token_file": "/run/secrets/sg-token",
        },
    ],
)
def test_cluster_descriptor_requires_exactly_one_credential_reference(
    kwargs,
):
    with pytest.raises(
        ValidationError,
        match="exactly one token source",
    ):
        cluster_config(
            **kwargs,
        )


def test_enabled_config_requires_clusters_and_unique_connection_identity():
    with pytest.raises(
        ValidationError,
        match="at least one cluster",
    ):
        KubernetesReadMultiClusterConfig(
            enabled=True
        )

    first = cluster_config()

    with pytest.raises(
        ValidationError,
        match="cluster names must be unique",
    ):
        enabled_config(
            first,
            cluster_config(
                api_url=(
                    "https://sg-duplicate.example.internal"
                ),
                bearer_token_env=(
                    "K8S_PROD_SG_DUP_READ_TOKEN"
                ),
            ),
        )

    with pytest.raises(
        ValidationError,
        match="API URLs must be unique",
    ):
        enabled_config(
            first,
            cluster_config(
                name="prod-us-03",
                bearer_token_env=(
                    "K8S_PROD_US_READ_TOKEN"
                ),
            ),
        )

    with pytest.raises(
        ValidationError,
        match="distinct credential references",
    ):
        enabled_config(
            first,
            cluster_config(
                name="prod-us-03",
                api_url=(
                    "https://us-kubernetes.example.internal"
                ),
                bearer_token_env=(
                    "K8S_PROD_SG_READ_TOKEN"
                ),
            ),
        )


def test_disabled_factory_does_not_touch_credentials_or_files():
    disabled = KubernetesReadMultiClusterConfig(
        enabled=False,
        clusters=(
            cluster_config(),
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

    result = create_kubernetes_cluster_registry(
        disabled,
        environment=ExplodingEnvironment(),
        token_file_reader=exploding_reader,
    )

    assert result is None


def test_enabled_env_config_builds_exact_read_only_registry_without_network():
    config = enabled_config(
        cluster_config(),
        cluster_config(
            name="prod-us-03",
            api_url=(
                "https://us-kubernetes.example.internal"
            ),
            bearer_token_env=(
                "K8S_PROD_US_READ_TOKEN"
            ),
        ),
    )

    registry = create_kubernetes_cluster_registry(
        config,
        environment={
            "K8S_PROD_SG_READ_TOKEN": (
                "sg-read-token-1234567890"
            ),
            "K8S_PROD_US_READ_TOKEN": (
                "us-read-token-1234567890"
            ),
        },
    )

    assert isinstance(
        registry,
        KubernetesClusterRegistry,
    )

    assert registry.cluster_names == (
        "prod-sg-17",
        "prod-us-03",
    )

    sg = registry.resolve(
        "prod-sg-17"
    )

    us = registry.resolve(
        "prod-us-03"
    )

    assert sg.api_url == (
        "https://sg-kubernetes.example.internal"
    )

    assert us.api_url == (
        "https://us-kubernetes.example.internal"
    )

    assert sg.verify_tls is True
    assert us.verify_tls is True

    assert (
        sg.allow_dry_run_fallback
        is False
    )

    assert (
        us.allow_dry_run_fallback
        is False
    )

    assert sg.client is None
    assert us.client is None


def test_token_file_and_ca_references_are_resolved_locally(
    tmp_path: Path,
):
    token_file = (
        tmp_path
        / "token"
    )

    token_file.write_text(
        "file-read-token-1234567890\n",
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
        cluster_config(
            bearer_token_env=None,
            bearer_token_file=str(
                token_file
            ),
            ca_file=str(
                ca_file
            ),
        )
    )

    registry = create_kubernetes_cluster_registry(
        config
    )

    tool = registry.resolve(
        "prod-sg-17"
    )

    assert tool.ca_file == (
        ca_file
    )

    assert tool.bearer_token == (
        "file-read-token-1234567890"
    )


def test_missing_environment_secret_fails_without_exposing_secret_value():
    config = enabled_config(
        cluster_config()
    )

    with pytest.raises(
        KubernetesReadConnectionFactoryConfigurationError,
        match=(
            "environment variable is missing"
        ),
    ) as captured:
        create_kubernetes_cluster_registry(
            config,
            environment={},
        )

    assert (
        "sg-read-token-1234567890"
        not in str(
            captured.value
        )
    )


def test_invalid_ca_reference_fails_before_registry_is_returned(
    tmp_path: Path,
):
    missing = (
        tmp_path
        / "missing-ca.crt"
    )

    config = enabled_config(
        cluster_config(
            ca_file=str(
                missing
            ),
        )
    )

    with pytest.raises(
        KubernetesReadConnectionFactoryConfigurationError,
        match="CA file is unavailable",
    ):
        create_kubernetes_cluster_registry(
            config,
            environment={
                "K8S_PROD_SG_READ_TOKEN": (
                    "sg-read-token-1234567890"
                )
            },
        )


def test_config_serialization_contains_references_not_token_values():
    config = enabled_config(
        cluster_config()
    )

    payload = config.model_dump()

    text = str(
        payload
    )

    assert (
        "K8S_PROD_SG_READ_TOKEN"
        in text
    )

    assert (
        "sg-read-token-1234567890"
        not in text
    )

    assert (
        "bearer_token"
        not in payload[
            "clusters"
        ][
            0
        ]
    )


def test_runtime_uses_config_factory_only_when_registry_not_explicit(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    configured_registry = (
        KubernetesClusterRegistry(
            [
                factory_module.KubernetesTool(
                    api_url=(
                        "https://sg-kubernetes.example.internal"
                    ),
                    cluster_name="prod-sg-17",
                    bearer_token=(
                        "sg-read-token-1234567890"
                    ),
                    allow_dry_run_fallback=False,
                )
            ]
        )
    )

    registry_factory_calls = []

    def registry_factory():
        registry_factory_calls.append(
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
        "create_kubernetes_cluster_registry",
        registry_factory,
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

    assert registry_factory_calls == [
        True
    ]

    assert (
        runtime.kubernetes_cluster_registry
        is configured_registry
    )

    assert manager_calls == [
        {
            "kubernetes_cluster_registry": (
                configured_registry
            )
        }
    ]


def test_explicit_runtime_registry_bypasses_connection_config_factory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    explicit_registry = (
        KubernetesClusterRegistry(
            [
                factory_module.KubernetesTool(
                    api_url=(
                        "https://explicit-kubernetes.example.internal"
                    ),
                    cluster_name="prod-explicit-01",
                    bearer_token=(
                        "explicit-read-token-123456"
                    ),
                    allow_dry_run_fallback=False,
                )
            ]
        )
    )

    def forbidden_registry_factory():
        raise AssertionError(
            "explicit registry must bypass connection config factory"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
        forbidden_registry_factory,
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
        kubernetes_cluster_registry=(
            explicit_registry
        ),
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    assert (
        runtime.kubernetes_cluster_registry
        is explicit_registry
    )


def test_connection_factory_module_contains_no_write_authority():
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
