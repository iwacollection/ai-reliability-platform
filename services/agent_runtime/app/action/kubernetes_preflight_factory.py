from collections.abc import Callable, Mapping
from datetime import datetime
from os import environ
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from common.config import get_settings
from common.config.settings import (
    KubernetesPreflightConfig,
)

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightPolicy,
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.safety_models import (
    KubernetesWorkloadScope,
)


class KubernetesPreflightFactoryConfigurationError(RuntimeError):
    """Trusted Kubernetes preflight cannot be assembled safely."""


_MAX_TOKEN_FILE_BYTES = 16 * 1024


def _resolve_config(
    config: KubernetesPreflightConfig | None,
) -> KubernetesPreflightConfig:
    resolved = (
        get_settings().remediation.kubernetes_preflight
        if config is None
        else config
    )
    if not isinstance(resolved, KubernetesPreflightConfig):
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight factory requires validated configuration"
        )
    return resolved


def _resolve_environment(
    environment: Mapping[str, str] | None,
) -> Mapping[str, str]:
    resolved = environ if environment is None else environment
    if not isinstance(resolved, Mapping):
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight credential source must be a mapping"
        )
    return resolved


def _default_token_file_reader(path: str) -> str:
    token_path = Path(path)
    try:
        if not token_path.is_file():
            raise KubernetesPreflightFactoryConfigurationError(
                "Kubernetes preflight token file is unavailable"
            )
        if token_path.stat().st_size > _MAX_TOKEN_FILE_BYTES:
            raise KubernetesPreflightFactoryConfigurationError(
                "Kubernetes preflight token file is too large"
            )
        return token_path.read_text(encoding="utf-8")
    except KubernetesPreflightFactoryConfigurationError:
        raise
    except OSError:
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight token file is unavailable"
        ) from None


def _validate_ca_file(path: str | None) -> bool | str:
    if path is None:
        return True

    try:
        if not Path(path).is_file():
            raise KubernetesPreflightFactoryConfigurationError(
                "Kubernetes preflight CA file is unavailable"
            )
    except OSError:
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight CA file is unavailable"
        ) from None

    return path


def _validate_token(value: Any) -> str:
    if not isinstance(value, str):
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight credential is invalid"
        )

    normalized = value.rstrip("\r\n")
    if (
        not normalized
        or normalized != normalized.strip()
        or len(normalized) < 16
        or len(normalized.encode("utf-8")) > _MAX_TOKEN_FILE_BYTES
        or "\x00" in normalized
    ):
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight credential is invalid"
        )
    return normalized


def _load_bearer_token(
    config: KubernetesPreflightConfig,
    *,
    environment: Mapping[str, str] | None,
    token_file_reader: Callable[[str], str] | None,
) -> str:
    if config.bearer_token_env is not None:
        source = _resolve_environment(environment)
        value = source.get(config.bearer_token_env)
        if value is None:
            raise KubernetesPreflightFactoryConfigurationError(
                "Kubernetes preflight credential environment variable is missing: "
                f"{config.bearer_token_env}"
            )
        return _validate_token(value)

    if config.bearer_token_file is not None:
        reader = token_file_reader or _default_token_file_reader
        try:
            value = reader(config.bearer_token_file)
        except KubernetesPreflightFactoryConfigurationError:
            raise
        except Exception:
            raise KubernetesPreflightFactoryConfigurationError(
                "Kubernetes preflight token file is unavailable"
            ) from None
        return _validate_token(value)

    raise KubernetesPreflightFactoryConfigurationError(
        "Kubernetes preflight has no configured credential source"
    )


def create_kubernetes_preflight_resolver(
    config: KubernetesPreflightConfig | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    token_file_reader: Callable[[str], str] | None = None,
    client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] | None = None,
    contract_id_factory: Callable[[], UUID] | None = None,
) -> KubernetesPreflightResolver | None:
    """
    Build the trusted resolver from validated non-secret settings.

    Disabled configuration returns None before reading the environment, token
    file, CA file, or creating a network client. Enabled configuration is
    validated completely and fails startup instead of falling back to mock or
    insecure Kubernetes behavior.
    """

    resolved = _resolve_config(config)
    if not resolved.enabled:
        return None

    if resolved.api_url is None or resolved.cluster_name is None:
        raise KubernetesPreflightFactoryConfigurationError(
            "Enabled Kubernetes preflight connection is incomplete"
        )

    token = _load_bearer_token(
        resolved,
        environment=environment,
        token_file_reader=token_file_reader,
    )
    verify_tls = _validate_ca_file(resolved.ca_file)
    targets = tuple(
        KubernetesWorkloadScope(
            cluster=item.cluster,
            namespace=item.namespace,
            name=item.deployment,
            container=item.container,
        )
        for item in resolved.allowed_targets
    )
    policy = KubernetesPreflightPolicy(
        enabled=True,
        allowed_targets=targets,
        increase_percent=resolved.increase_percent,
        contract_ttl_seconds=resolved.contract_ttl_seconds,
        request_timeout_seconds=resolved.request_timeout_seconds,
        field_manager=resolved.field_manager,
        policy_version=resolved.policy_version,
    )

    try:
        return KubernetesPreflightResolver(
            api_url=resolved.api_url,
            cluster_name=resolved.cluster_name,
            policy=policy,
            bearer_token=token,
            verify_tls=verify_tls,
            client=client,
            clock=clock,
            contract_id_factory=contract_id_factory,
        )
    except Exception:
        raise KubernetesPreflightFactoryConfigurationError(
            "Kubernetes preflight resolver configuration is invalid"
        ) from None


__all__ = [
    "KubernetesPreflightFactoryConfigurationError",
    "create_kubernetes_preflight_resolver",
]
