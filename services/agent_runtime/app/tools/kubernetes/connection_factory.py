from __future__ import annotations

from collections.abc import (
    Callable,
    Mapping,
)
from os import environ
from pathlib import Path
from typing import Any

from common.config import (
    get_settings,
)
from common.config.settings import (
    KubernetesReadClusterConfig,
    KubernetesReadMultiClusterConfig,
)

from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
    KubernetesClusterRoutingError,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesConfigurationError,
    KubernetesTool,
)


class KubernetesReadConnectionFactoryConfigurationError(
    RuntimeError
):
    """
    Read-only multi-cluster Kubernetes connections cannot be assembled safely.
    """


_MAX_TOKEN_FILE_BYTES = (
    16
    * 1024
)


def _resolve_config(
    config: (
        KubernetesReadMultiClusterConfig
        | None
    ),
) -> KubernetesReadMultiClusterConfig:
    resolved = (
        get_settings()
        .connections
        .kubernetes_read
        if config is None
        else config
    )

    if not isinstance(
        resolved,
        KubernetesReadMultiClusterConfig,
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read connection factory requires validated configuration"
        )

    return resolved


def _resolve_environment(
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ),
) -> Mapping[
    str,
    str,
]:
    resolved = (
        environ
        if environment is None
        else environment
    )

    if not isinstance(
        resolved,
        Mapping,
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read credential source must be a mapping"
        )

    return resolved


def _default_token_file_reader(
    path: str,
) -> str:
    token_path = Path(
        path
    )

    try:
        if not token_path.is_file():
            raise KubernetesReadConnectionFactoryConfigurationError(
                "Kubernetes read token file is unavailable"
            )

        if (
            token_path.stat().st_size
            > _MAX_TOKEN_FILE_BYTES
        ):
            raise KubernetesReadConnectionFactoryConfigurationError(
                "Kubernetes read token file is too large"
            )

        return token_path.read_text(
            encoding="utf-8"
        )

    except KubernetesReadConnectionFactoryConfigurationError:
        raise

    except OSError:
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read token file is unavailable"
        ) from None


def _validate_token(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read credential is invalid"
        )

    normalized = value.rstrip(
        "\r\n"
    )

    if (
        not normalized
        or normalized
        != normalized.strip()
        or len(
            normalized
        )
        < 16
        or len(
            normalized.encode(
                "utf-8"
            )
        )
        > _MAX_TOKEN_FILE_BYTES
        or "\x00"
        in normalized
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read credential is invalid"
        )

    return normalized


def _load_bearer_token(
    config: KubernetesReadClusterConfig,
    *,
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ),
    token_file_reader: (
        Callable[
            [
                str,
            ],
            str,
        ]
        | None
    ),
) -> str:
    if (
        config.bearer_token_env
        is not None
    ):
        source = _resolve_environment(
            environment
        )

        value = source.get(
            config.bearer_token_env
        )

        if value is None:
            raise KubernetesReadConnectionFactoryConfigurationError(
                "Kubernetes read credential environment variable is missing: "
                + config.bearer_token_env
            )

        return _validate_token(
            value
        )

    if (
        config.bearer_token_file
        is not None
    ):
        reader = (
            token_file_reader
            or _default_token_file_reader
        )

        try:
            value = reader(
                config.bearer_token_file
            )

        except KubernetesReadConnectionFactoryConfigurationError:
            raise

        except Exception:
            raise KubernetesReadConnectionFactoryConfigurationError(
                "Kubernetes read token file is unavailable"
            ) from None

        return _validate_token(
            value
        )

    raise KubernetesReadConnectionFactoryConfigurationError(
        "Kubernetes read cluster has no configured credential source"
    )


def _validate_ca_file(
    path: str | None,
) -> str | None:
    if path is None:
        return None

    try:
        if not Path(
            path
        ).is_file():
            raise KubernetesReadConnectionFactoryConfigurationError(
                "Kubernetes read CA file is unavailable"
            )

    except OSError:
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read CA file is unavailable"
        ) from None

    return path


def create_kubernetes_cluster_registry(
    config: (
        KubernetesReadMultiClusterConfig
        | None
    ) = None,
    *,
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ) = None,
    token_file_reader: (
        Callable[
            [
                str,
            ],
            str,
        ]
        | None
    ) = None,
) -> KubernetesClusterRegistry | None:
    """
    Build read-only multi-cluster connections from validated non-secret config.

    Disabled configuration returns None before reading environment variables,
    token files, CA files, or constructing any KubernetesTool.

    Enabled configuration resolves local credential references, constructs
    fail-closed cluster-bound read-only KubernetesTool objects, and returns an
    immutable KubernetesClusterRegistry. No HTTP request is made here.
    """

    resolved = _resolve_config(
        config
    )

    if not resolved.enabled:
        return None

    tools = []

    try:
        for item in resolved.clusters:
            token = _load_bearer_token(
                item,
                environment=environment,
                token_file_reader=(
                    token_file_reader
                ),
            )

            ca_file = _validate_ca_file(
                item.ca_file
            )

            tool = KubernetesTool(
                api_url=item.api_url,
                timeout_seconds=(
                    item.request_timeout_seconds
                ),
                verify_tls=True,
                bearer_token=token,
                token_file=None,
                ca_file=ca_file,
                cluster_name=(
                    item.cluster_name
                ),
                allow_dry_run_fallback=False,
            )

            if (
                tool.api_url
                != item.api_url
                or tool.cluster_name
                != item.cluster_name
                or tool.verify_tls
                is not True
                or tool.allow_dry_run_fallback
                is not False
            ):
                raise KubernetesReadConnectionFactoryConfigurationError(
                    "Kubernetes read Tool did not retain the validated connection boundary"
                )

            tools.append(
                tool
            )

        registry = (
            KubernetesClusterRegistry(
                tools
            )
        )

    except KubernetesReadConnectionFactoryConfigurationError:
        raise

    except (
        KubernetesClusterRoutingError,
        KubernetesConfigurationError,
        TypeError,
        ValueError,
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read cluster registry configuration is invalid"
        ) from None

    if (
        registry.count
        != len(
            resolved.clusters
        )
    ):
        raise KubernetesReadConnectionFactoryConfigurationError(
            "Kubernetes read cluster registry lost configured connections"
        )

    return registry


__all__ = [
    "KubernetesReadConnectionFactoryConfigurationError",
    "create_kubernetes_cluster_registry",
]
