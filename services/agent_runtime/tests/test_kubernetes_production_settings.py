import pytest
from pydantic import ValidationError

from common.config.settings import (
    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT,
    KubernetesPreflightConfig,
    KubernetesPreflightTargetConfig,
    KubernetesProductionExecutionConfig,
    RemediationConfig,
)


def preflight_config() -> KubernetesPreflightConfig:
    return KubernetesPreflightConfig(
        enabled=True,
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        bearer_token_env="K8S_PREFLIGHT_TOKEN",
        allowed_targets=(
            KubernetesPreflightTargetConfig(
                cluster="production-a",
                namespace="payment",
                deployment="payment-api",
                container="payment-api",
            ),
        ),
    )


def execution_config(**overrides) -> KubernetesProductionExecutionConfig:
    values = {
        "enabled": True,
        "write_acknowledgement": (
            KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
        ),
        "bearer_token_env": "K8S_PRODUCTION_EXECUTION_TOKEN",
    }
    values.update(overrides)
    return KubernetesProductionExecutionConfig(**values)


def test_production_execution_defaults_disabled_without_credentials():
    config = KubernetesProductionExecutionConfig()
    assert config.enabled is False
    assert config.bearer_token_env is None
    assert config.bearer_token_file is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"write_acknowledgement": None},
        {"write_acknowledgement": "ENABLE"},
        {"bearer_token_env": None},
        {
            "bearer_token_env": "K8S_PRODUCTION_EXECUTION_TOKEN",
            "bearer_token_file": "production.token",
        },
    ],
)
def test_enabled_execution_requires_exact_ack_and_one_credential(overrides):
    with pytest.raises(ValidationError):
        execution_config(**overrides)


def test_remediation_requires_enabled_preflight_and_separate_reference():
    with pytest.raises(ValidationError):
        RemediationConfig(
            kubernetes_production_execution=execution_config(),
        )

    with pytest.raises(ValidationError):
        RemediationConfig(
            kubernetes_preflight=preflight_config(),
            kubernetes_production_execution=execution_config(
                bearer_token_env="K8S_PREFLIGHT_TOKEN"
            ),
        )

    validated = RemediationConfig(
        kubernetes_preflight=preflight_config(),
        kubernetes_production_execution=execution_config(),
    )
    assert validated.kubernetes_production_execution.enabled is True


def test_disabled_execution_preserves_legacy_preflight_configuration():
    config = RemediationConfig(
        kubernetes_preflight=KubernetesPreflightConfig()
    )
    assert config.kubernetes_preflight.enabled is False
    assert config.kubernetes_production_execution.enabled is False
