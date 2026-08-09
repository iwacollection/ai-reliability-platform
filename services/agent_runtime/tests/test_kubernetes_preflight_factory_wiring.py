from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config.settings import (
    AuthenticationConfig,
    KubernetesPreflightConfig,
    Settings,
)

import services.agent_runtime.app.runtime.runtime as runtime_module

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.kubernetes_preflight_factory import (
    KubernetesPreflightFactoryConfigurationError,
    create_kubernetes_preflight_resolver,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)


SECRET = "kubernetes-pilot-service-account-token-000001"


def minimal_settings_payload() -> dict:
    return {
        "app": {
            "name": "AI Reliability Platform",
            "version": "0.1.0",
        },
        "llm": {
            "provider": "mock",
            "temperature": 0.0,
            "timeout": 30,
        },
        "runtime": {
            "pipeline": "sequential",
            "max_workers": 4,
        },
    }


def enabled_config(
    *,
    credential_source: str = "environment",
    ca_file: str | None = None,
) -> KubernetesPreflightConfig:
    data = {
        "enabled": True,
        "api_url": "https://kubernetes.test",
        "cluster_name": "production-a",
        "ca_file": ca_file,
        "allowed_targets": [
            {
                "cluster": "production-a",
                "namespace": "payment",
                "deployment": "payment-api",
                "container": "payment-api",
            }
        ],
        "increase_percent": 25,
        "contract_ttl_seconds": 600,
    }
    if credential_source == "environment":
        data["bearer_token_env"] = "KUBERNETES_PILOT_TOKEN"
    elif credential_source == "file":
        data["bearer_token_file"] = "/var/run/secrets/pilot/token"
    else:
        raise AssertionError("Unknown test credential source")
    return KubernetesPreflightConfig.model_validate(data)


def disabled_authentication_service():
    return create_authentication_service(
        AuthenticationConfig()
    )


def create_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    kubernetes_preflight: KubernetesPreflightResolver | None = None,
):
    monkeypatch.chdir(tmp_path)
    return runtime_module.AgentRuntime(
        authentication_service=disabled_authentication_service(),
        kubernetes_preflight=kubernetes_preflight,
    )


def test_settings_default_to_disabled_preflight_without_yaml_changes():
    settings = Settings.model_validate(minimal_settings_payload())

    assert settings.remediation.kubernetes_preflight.enabled is False
    assert settings.remediation.kubernetes_preflight.allowed_targets == ()
    assert settings.remediation.kubernetes_preflight.api_url is None
    assert settings.remediation.kubernetes_preflight.bearer_token_env is None
    assert settings.remediation.kubernetes_preflight.bearer_token_file is None


def test_settings_store_only_credential_references_not_secret_values():
    payload = minimal_settings_payload()
    payload["remediation"] = {
        "kubernetes_preflight": enabled_config().model_dump(mode="json")
    }
    settings = Settings.model_validate(payload)
    serialized = settings.model_dump_json()

    assert "KUBERNETES_PILOT_TOKEN" in serialized
    assert SECRET not in serialized

    unsafe = enabled_config().model_dump(mode="python")
    unsafe["bearer_token"] = SECRET
    with pytest.raises(ValidationError, match="extra_forbidden"):
        KubernetesPreflightConfig.model_validate(unsafe)


@pytest.mark.parametrize(
    "update",
    [
        {"api_url": None},
        {"cluster_name": None},
        {"bearer_token_env": None},
        {"bearer_token_file": "/tmp/second-token"},
        {"allowed_targets": []},
        {"api_url": "http://kubernetes.test"},
        {
            "allowed_targets": [
                {
                    "cluster": "production-b",
                    "namespace": "payment",
                    "deployment": "payment-api",
                    "container": "payment-api",
                }
            ]
        },
    ],
)
def test_enabled_settings_fail_closed_on_incomplete_or_unsafe_input(update):
    data = enabled_config().model_dump(mode="python")
    data.update(update)
    with pytest.raises(ValidationError):
        KubernetesPreflightConfig.model_validate(data)


def test_disabled_factory_reads_no_environment_or_token_file():
    environment_reads = 0
    file_reads = 0

    class PoisonEnvironment(dict):
        def get(self, key, default=None):
            nonlocal environment_reads
            environment_reads += 1
            raise AssertionError("Disabled preflight read the environment")

    def poison_file_reader(path: str) -> str:
        nonlocal file_reads
        file_reads += 1
        raise AssertionError("Disabled preflight read a token file")

    resolver = create_kubernetes_preflight_resolver(
        KubernetesPreflightConfig(),
        environment=PoisonEnvironment(),
        token_file_reader=poison_file_reader,
    )

    assert resolver is None
    assert environment_reads == 0
    assert file_reads == 0


def test_factory_builds_exact_env_backed_resolver_without_network_access():
    resolver = create_kubernetes_preflight_resolver(
        enabled_config(),
        environment={"KUBERNETES_PILOT_TOKEN": SECRET},
    )

    assert isinstance(resolver, KubernetesPreflightResolver)
    assert resolver.api_url == "https://kubernetes.test"
    assert resolver.cluster_name == "production-a"
    assert resolver.client is None
    assert resolver.policy.enabled is True
    assert resolver.policy.increase_percent == 25
    assert resolver.policy.allowed_targets[0].name == "payment-api"
    assert SECRET not in repr(resolver.__dict__)


def test_factory_reads_token_file_once_and_strips_only_line_endings():
    reads = []

    def read_token(path: str) -> str:
        reads.append(path)
        return SECRET + "\r\n"

    resolver = create_kubernetes_preflight_resolver(
        enabled_config(credential_source="file"),
        token_file_reader=read_token,
    )

    assert isinstance(resolver, KubernetesPreflightResolver)
    assert reads == ["/var/run/secrets/pilot/token"]
    assert SECRET not in repr(resolver.__dict__)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"KUBERNETES_PILOT_TOKEN": "short"},
        {"KUBERNETES_PILOT_TOKEN": " " + SECRET},
        {"KUBERNETES_PILOT_TOKEN": SECRET + "\x00"},
    ],
)
def test_factory_rejects_missing_or_invalid_environment_credentials(environment):
    with pytest.raises(KubernetesPreflightFactoryConfigurationError) as captured:
        create_kubernetes_preflight_resolver(
            enabled_config(),
            environment=environment,
        )

    assert SECRET not in str(captured.value)
    assert SECRET not in repr(captured.value)


def test_factory_rejects_unavailable_ca_before_resolver_creation(tmp_path: Path):
    missing_ca = tmp_path / "missing-ca.crt"
    with pytest.raises(
        KubernetesPreflightFactoryConfigurationError,
        match="CA file is unavailable",
    ):
        create_kubernetes_preflight_resolver(
            enabled_config(ca_file=str(missing_ca)),
            environment={"KUBERNETES_PILOT_TOKEN": SECRET},
        )


def test_runtime_creates_one_default_preflight_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls = 0

    def create_disabled_resolver():
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        create_disabled_resolver,
    )
    runtime = create_isolated_runtime(monkeypatch, tmp_path)

    assert calls == 1
    assert runtime.kubernetes_preflight is None


def test_runtime_preserves_explicit_preflight_without_calling_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    expected = create_kubernetes_preflight_resolver(
        enabled_config(),
        environment={"KUBERNETES_PILOT_TOKEN": SECRET},
    )

    def unexpected_factory_call():
        raise AssertionError("Explicit preflight injection called the factory")

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        unexpected_factory_call,
    )
    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        kubernetes_preflight=expected,
    )

    assert runtime.kubernetes_preflight is expected


def test_invalid_runtime_injection_fails_before_factories_or_components(
    monkeypatch: pytest.MonkeyPatch,
):
    authentication_calls = 0
    preflight_calls = 0
    component_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_calls
        authentication_calls += 1
        raise AssertionError("Invalid injection called authentication factory")

    def unexpected_preflight_factory():
        nonlocal preflight_calls
        preflight_calls += 1
        raise AssertionError("Invalid injection called preflight factory")

    def unexpected_component():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError("Invalid injection created a component")

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        unexpected_preflight_factory,
    )
    monkeypatch.setattr(runtime_module, "MemoryStore", unexpected_component)

    with pytest.raises(TypeError, match="preflight resolver is invalid"):
        runtime_module.AgentRuntime(kubernetes_preflight=object())

    assert authentication_calls == 0
    assert preflight_calls == 0
    assert component_calls == 0


def test_preflight_factory_failure_is_fail_fast_before_components(
    monkeypatch: pytest.MonkeyPatch,
):
    component_calls = 0

    def fail_factory():
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight startup configuration is invalid"
        )

    def unexpected_component():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError("Factory failure created a component")

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        fail_factory,
    )
    monkeypatch.setattr(runtime_module, "MemoryStore", unexpected_component)

    with pytest.raises(
        KubernetesPreflightFactoryConfigurationError,
        match="startup configuration is invalid",
    ):
        runtime_module.AgentRuntime(
            authentication_service=disabled_authentication_service()
        )

    assert component_calls == 0


def test_preflight_wiring_does_not_enter_action_or_verification_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    resolver = create_kubernetes_preflight_resolver(
        enabled_config(),
        environment={"KUBERNETES_PILOT_TOKEN": SECRET},
    )
    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        kubernetes_preflight=resolver,
    )

    assert runtime.kubernetes_preflight is resolver
    assert not hasattr(runtime.action_runtime, "kubernetes_preflight")
    assert (
        runtime.action_runtime.action_execution_service
        is runtime.action_execution_service
    )
    assert (
        runtime.verification_coordinator.verification_runtime
        is runtime.verification_runtime
    )
    assert runtime.action_runtime.executor.__class__.__name__ == "MockExecutor"
